"""One delegated task: a short agent loop on DeepSeek, with no memory between tasks."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .gateway import GatewayError
from .sandbox import Sandbox, SandboxError

# Project instructions loaded into every task, when the project has them.
PROJECT_FILES = ("AGENTS.md", "CLAUDE.md")
MAX_PROJECT_FILE_CHARS = 20_000
# A cautious guess, so the worker stops reading well before the real limit.
CHARS_PER_TOKEN = 3
CONTEXT_SHARE = 0.8

TOOLS = [
    {
        "name": "list_files",
        "description": "List project files whose path or name matches a glob pattern, such as *.md or docs/*.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string", "description": "Glob pattern. Default: every file."}},
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file in the project. Lines come back numbered.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root."},
                "offset": {"type": "integer", "description": "First line to read. Default 1."},
                "limit": {"type": "integer", "description": "Number of lines to read. Default and maximum 2000."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": "Search project files for lines matching a regular expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression."},
                "file_pattern": {"type": "string", "description": "Only search files matching this glob."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "write_output",
        "description": "Write your result to the output file. Calling it again replaces what you wrote before.",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string", "description": "The full file content."}},
            "required": ["content"],
        },
    },
]

SYSTEM_PROMPT = """You are a worker. Another AI agent hands you one task and reviews your result.

Rules:
- Write your result with write_output. It writes only to {output_path}. You cannot change any other file.
- Read what you need with list_files, search and read_file. Do not guess what a file says.
- You have no internet access. If the task needs information you cannot find in the project, say so in your summary. Never invent facts.
- Follow the project instructions below when they apply to your task.
- When you are done, reply with a short summary: what you wrote, which files you relied on, and anything you were unsure of.
"""


@dataclass
class Result:
    status: str  # done, no_output, incomplete or failed
    summary: str = ""
    output_path: str = ""
    turns: int = 0
    files_read: list = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str = ""


def _project_instructions(root):
    sections = []
    for name in PROJECT_FILES:
        path = root / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")[:MAX_PROJECT_FILE_CHARS]
            except (OSError, UnicodeDecodeError):
                continue
            sections.append(f"## {name}\n\n{text}")
    return "\n\n".join(sections)


def _call_tool(sandbox, name, args, files_read):
    if name == "list_files":
        return sandbox.list_files(args.get("pattern") or "*")
    if name == "read_file":
        path = args.get("path", "")
        try:
            offset, limit = int(args.get("offset") or 1), int(args.get("limit") or 2000)
        except (TypeError, ValueError):
            raise SandboxError("offset and limit must be whole numbers.") from None
        text = sandbox.read_file(path, offset, limit)
        files_read.append(sandbox.relative(sandbox.resolve(path)))
        return text
    if name == "search":
        return sandbox.search(args.get("pattern", ""), args.get("file_pattern") or "*")
    if name == "write_output":
        content = args.get("content")
        if not isinstance(content, str):
            raise SandboxError("content must be text.")
        return sandbox.write_output(content)
    raise SandboxError(f"There is no tool named {name}.")


def run(gateway, config, task, project_dir, output_path, context_files=(), overwrite=False,
        clock=time.monotonic):
    started = clock()

    def finish(status, **fields):
        return Result(status=status, duration_seconds=round(clock() - started, 1), **fields)

    root = Path(project_dir)
    if not root.is_absolute() or not root.is_dir():
        return finish("failed", error=f"project_dir must be an absolute path to a folder, got {project_dir!r}.")
    try:
        sandbox = Sandbox(root, output_path)
    except SandboxError as error:
        return finish("failed", error=f"output_path: {error}")
    output_relative = sandbox.relative(sandbox.output)
    if sandbox.output.exists() and not overwrite:
        return finish("failed", output_path=output_relative,
                      error=f"{output_relative} already exists. Pick a new path, or set overwrite to true.")

    files_read = []
    preloaded = []
    for path in context_files:
        try:
            preloaded.append(f"## {path}\n\n{_call_tool(sandbox, 'read_file', {'path': path}, files_read)}")
        except SandboxError as error:
            return finish("failed", error=f"context_files: {error}")

    system = SYSTEM_PROMPT.format(output_path=output_relative)
    instructions = _project_instructions(sandbox.root)
    if instructions:
        system += "\n# Project instructions\n\n" + instructions
    prompt = f"# Task\n\n{task}\n\nWrite the result to {output_relative}."
    if preloaded:
        prompt += "\n\n# Files given to you\n\n" + "\n\n".join(preloaded)

    budget = int(config.context_tokens * CONTEXT_SHARE * CHARS_PER_TOKEN)
    used = len(system) + len(prompt)
    if used > budget:
        return finish("failed", error="The task and context_files are too large for the worker. Send fewer files.")

    messages = [{"role": "user", "content": prompt}]
    wrote = False
    turns = 0
    while turns < config.max_turns:
        turns += 1
        try:
            response = gateway.create_message(system, messages, TOOLS)
        except GatewayError as error:
            return finish("failed", output_path=output_relative, turns=turns - 1,
                          files_read=files_read, error=str(error))

        content = response.get("content") or []
        # Send the turn back unchanged, including thinking blocks.
        messages.append({"role": "assistant", "content": content})
        used += len(json.dumps(content))

        if response.get("stop_reason") != "tool_use":
            summary = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
            status = "done" if wrote else "no_output"
            return finish(status, summary=summary, output_path=output_relative, turns=turns,
                          files_read=files_read,
                          error="" if wrote else "The worker finished without writing the output file.")

        results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            name, args = block.get("name"), block.get("input") or {}
            try:
                output = _call_tool(sandbox, name, args, files_read)
                is_error = False
                if name == "write_output":
                    wrote = True
            except (SandboxError, OSError) as error:
                output, is_error = str(error), True
            if used + len(output) > budget:
                output = "Your context budget is used up. Write your output now with what you have."
                is_error = True
            used += len(output)
            results.append({"type": "tool_result", "tool_use_id": block.get("id"),
                            "content": output, "is_error": is_error})
        if not results:
            return finish("failed", output_path=output_relative, turns=turns, files_read=files_read,
                          error="The model asked for a tool but sent no tool call.")
        messages.append({"role": "user", "content": results})

    return finish("incomplete", output_path=output_relative, turns=turns, files_read=files_read,
                  error=f"The worker did not finish within {config.max_turns} turns."
                  + (" A partial output was written." if wrote else ""))
