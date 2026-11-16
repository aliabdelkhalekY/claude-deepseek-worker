"""Check that a gateway handles tool calls across several turns.

The model gets two tools over a small fake file system. The answer is in a
file it cannot see without calling both tools. PASS means it listed the files,
read the right one, and reported the answer.

Uses only the Python standard library. Reads DEEPSEEK_WORKER_API_KEY, DEEPSEEK_WORKER_BASE_URL and DEEPSEEK_WORKER_MODEL.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("DEEPSEEK_WORKER_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_WORKER_MODEL", "")
MAX_TURNS = 6
TIMEOUT_SECONDS = 120

SECRET = "Falcon-7731"
FILES = {
    "README.md": "# Demo project\nA small demo. Release notes are in the notes folder.\n",
    "notes/release.txt": f"The release codename is {SECRET}.\n",
    "src/main.py": "print('hello')\n",
}

TOOLS = [
    {
        "name": "list_files",
        "description": "List every file path in the project.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Return the text of one file in the project.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path from list_files."}},
            "required": ["path"],
        },
    },
]

TASK = (
    "What is the release codename of this project? You cannot see the files. "
    "Use the tools to find it, then answer with the codename."
)


def run_tool(name, tool_input):
    if name == "list_files":
        return "\n".join(FILES), False
    if name == "read_file":
        path = tool_input.get("path", "")
        if path in FILES:
            return FILES[path], False
        return f"No such file: {path}", True
    return f"Unknown tool: {name}", True


def call_gateway(token, messages):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 2048,
        "tools": TOOLS,
        "messages": messages,
    }).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/v1/messages",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def fail(reason):
    print(f"FAIL: {reason}")
    sys.exit(1)


def main():
    token = os.environ.get("DEEPSEEK_WORKER_API_KEY")
    for name, value in (("DEEPSEEK_WORKER_API_KEY", token), ("DEEPSEEK_WORKER_BASE_URL", BASE_URL),
                        ("DEEPSEEK_WORKER_MODEL", MODEL)):
        if not value:
            fail(f"{name} is not set.")

    print(f"Gateway: {BASE_URL}")
    print(f"Model:   {MODEL}")

    messages = [{"role": "user", "content": TASK}]
    tool_calls = []
    final_text = ""

    for turn in range(1, MAX_TURNS + 1):
        try:
            response = call_gateway(token, messages)
        except urllib.error.HTTPError as error:
            fail(f"HTTP {error.code} on turn {turn}: {error.read().decode(errors='replace')[:500]}")
        except urllib.error.URLError as error:
            fail(f"Could not reach the gateway: {error.reason}")

        content = response.get("content", [])
        stop_reason = response.get("stop_reason")
        usage = response.get("usage", {})
        kinds = [block.get("type") for block in content]
        print(f"Turn {turn}: stop_reason={stop_reason} blocks={kinds} "
              f"tokens in/out={usage.get('input_tokens')}/{usage.get('output_tokens')}")

        # Send the assistant turn back unchanged, including any thinking blocks.
        messages.append({"role": "assistant", "content": content})

        if stop_reason != "tool_use":
            final_text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            break

        results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            name, tool_input = block.get("name"), block.get("input") or {}
            tool_calls.append((name, tool_input))
            print(f"  tool call: {name} {json.dumps(tool_input)}")
            output, is_error = run_tool(name, tool_input)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": output,
                "is_error": is_error,
            })
        if not results:
            fail("stop_reason was tool_use but no tool_use block was returned.")
        messages.append({"role": "user", "content": results})
    else:
        fail(f"No final answer after {MAX_TURNS} turns.")

    print(f"Final answer: {final_text.strip()[:300]}")

    called = {name for name, _ in tool_calls}
    if "list_files" not in called:
        fail("The model never called list_files.")
    if ("read_file", {"path": "notes/release.txt"}) not in tool_calls:
        fail("The model never read notes/release.txt.")
    if SECRET not in final_text:
        fail(f"The final answer does not contain {SECRET}.")

    print(f"PASS: {len(tool_calls)} tool calls over {turn} turns.")


if __name__ == "__main__":
    main()
