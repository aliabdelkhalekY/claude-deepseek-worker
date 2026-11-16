"""MCP server over stdin and stdout: newline-delimited JSON-RPC 2.0.

Written with only the standard library, so it runs on the system Python with nothing to install.
"""

import json
import sys
import threading
import traceback

from . import VERSION, config as config_module, worker
from .delegation_log import OUTCOMES, DelegationLog
from .gateway import Gateway

DEFAULT_PROTOCOL_VERSION = "2025-06-18"


def tool_definitions(config):
    delegate_description = f"""Hand a simple task to a DeepSeek worker on the user's own gateway, to save your own effort. Good fits: drafting documents, summaries, boilerplate and first drafts that you will review. Keep design, code logic and decisions yourself. The delegate-to-deepseek skill has the full routing rules.

The worker:
- has no internet access;
- has no memory between calls and has not seen this conversation, so put everything it needs in task and context_files;
- can read files in project_dir, except .git and files that may hold secrets;
- writes only to output_path;
- automatically gets the project's AGENTS.md and CLAUDE.md;
- holds about {config.context_tokens:,} tokens of context.

It returns a short summary and the output path, not the content. Read the output file before you rely on it, then call record_outcome.

The gateway is sometimes down. If this tool fails, do the task yourself right away. Do not retry or wait."""

    return [
        {
            "name": "delegate",
            "description": delegate_description,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "A complete brief: goal, audience, format, length, facts from this conversation, and what not to do."},
                    "project_dir": {"type": "string", "description": "Absolute path of the project. Use your current working directory."},
                    "output_path": {"type": "string", "description": "File to write, relative to project_dir."},
                    "context_files": {"type": "array", "items": {"type": "string"}, "description": "Files the worker must read, relative to project_dir. It can find others itself."},
                    "overwrite": {"type": "boolean", "description": "Allow replacing an existing output_path. Default false."},
                },
                "required": ["task", "project_dir", "output_path"],
            },
        },
        {
            "name": "record_outcome",
            "description": "Record how a delegation's result turned out, after you reviewed it. The log shows over time which tasks DeepSeek handles well.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "delegation_id": {"type": "string", "description": "The id that delegate returned."},
                    "outcome": {"type": "string", "enum": list(OUTCOMES), "description": "accepted as is, edited by you, or rejected."},
                    "note": {"type": "string", "description": "One line on what was wrong or right."},
                },
                "required": ["delegation_id", "outcome"],
            },
        },
    ]


def _text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class Server:
    def __init__(self, config, gateway, log, stdout):
        self.config = config
        self.gateway = gateway
        self.log = log
        self._stdout = stdout
        self._write_lock = threading.Lock()

    def send(self, message):
        with self._write_lock:
            self._stdout.write(json.dumps(message) + "\n")
            self._stdout.flush()

    def serve(self, stdin):
        threads = []
        while True:
            line = stdin.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "Parse error"}})
                continue
            if not isinstance(message, dict) or "method" not in message:
                continue  # A response or something else we do not handle.
            if "id" not in message:
                continue  # A notification. None of them need an answer.
            if message["method"] == "tools/call":
                # Run each tool call on its own thread, so several delegations can run at once.
                thread = threading.Thread(target=self._answer, args=(message,), daemon=True)
                thread.start()
                threads.append(thread)
            else:
                self._answer(message)
        for thread in threads:
            thread.join()

    def _answer(self, message):
        request_id = message["id"]
        try:
            result = self.handle(message["method"], message.get("params") or {})
        except LookupError as error:
            self.send({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32601, "message": str(error)}})
            return
        except Exception as error:  # Keep the server alive whatever happens in one request.
            traceback.print_exc(file=sys.stderr)
            self.send({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32603, "message": f"Internal error: {error}"}})
            return
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def handle(self, method, params):
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "deepseek-worker", "version": VERSION},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tool_definitions(self.config)}
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "delegate":
                return self.delegate(args)
            if name == "record_outcome":
                return self.record_outcome(args)
            return _text_result(f"Unknown tool: {name}", is_error=True)
        raise LookupError(f"Method not found: {method}")

    def delegate(self, args):
        missing = [key for key in ("task", "project_dir", "output_path") if not args.get(key)]
        if missing:
            return _text_result(f"Missing required arguments: {', '.join(missing)}.", is_error=True)
        missing = config_module.missing_settings(self.config)
        if missing:
            return _text_result(
                f"The worker is not set up (no {', '.join(missing)}), so it cannot run. The user "
                "can fix this by running scripts/install.sh with a provider profile, in the "
                "deepseek-worker repository. Do the task yourself for now.", is_error=True)

        context_files = args.get("context_files") or []
        if not isinstance(context_files, list):
            return _text_result("context_files must be a list of paths.", is_error=True)

        delegation_id = self.log.new_id()
        result = worker.run(self.gateway, self.config, args["task"], args["project_dir"],
                            args["output_path"], context_files, bool(args.get("overwrite")))
        self.log.record_delegation(delegation_id, args["project_dir"], args["task"], result)

        lines = [f"Delegation {delegation_id}: {result.status} in {result.duration_seconds}s, "
                 f"{result.turns} model requests."]
        if result.output_path:
            lines.append(f"Output: {result.output_path}")
        if result.error:
            lines.append(f"Problem: {result.error}")
        if result.summary:
            lines.append(f"Worker's summary: {result.summary}")
        if result.files_read:
            lines.append("Files read: " + ", ".join(dict.fromkeys(result.files_read)))
        if result.status == "done":
            lines.append(f"Read the output before you rely on it, then call record_outcome "
                         f"with delegation_id {delegation_id}.")
        else:
            lines.append("Do the task yourself now. Do not retry the delegation.")
        return _text_result("\n".join(lines), is_error=result.status != "done")

    def record_outcome(self, args):
        delegation_id = args.get("delegation_id", "")
        outcome = args.get("outcome", "")
        if outcome not in OUTCOMES:
            return _text_result(f"outcome must be one of: {', '.join(OUTCOMES)}.", is_error=True)
        if not self.log.record_outcome(delegation_id, outcome, args.get("note", "")):
            return _text_result(f"No delegation with id {delegation_id!r} in the log.", is_error=True)
        return _text_result(f"Recorded {outcome} for {delegation_id}.")


def main():
    try:
        config = config_module.load()
    except ValueError as error:
        print(f"deepseek-worker: {error}", file=sys.stderr)
        return 1
    # Claude Code speaks UTF-8 with \n line endings. Windows would otherwise use its own
    # code page and \r\n.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    server = Server(config, Gateway(config), DelegationLog(config.log_path), sys.stdout)
    server.serve(sys.stdin)
    return 0
