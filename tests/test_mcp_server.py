import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepseek_worker.delegation_log import DelegationLog
from deepseek_worker.gateway import GatewayError
from deepseek_worker.mcp_server import Server
from tests.helpers import ScriptedGateway, make_config, text, tool_use, write_files

REPO = Path(__file__).resolve().parent.parent


def request(request_id, method, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


class ServerProcessTest(unittest.TestCase):
    """Runs server.py the way Claude Code does: a subprocess speaking over stdin and stdout."""

    def test_protocol_over_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {key: value for key, value in os.environ.items() if not key.startswith("DEEPSEEK_WORKER_")}
            env["DEEPSEEK_WORKER_LOG"] = str(Path(tmp) / "log.jsonl")
            messages = [
                request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "0"}}),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                request(2, "tools/list"),
                request(3, "tools/call", {"name": "delegate", "arguments": {
                    "task": "x", "project_dir": tmp, "output_path": "out.md"}}),
                request(4, "no/such/method"),
            ]
            stdin = "".join(json.dumps(m) + "\n" for m in messages) + "not json\n"
            completed = subprocess.run([sys.executable, str(REPO / "server.py")], input=stdin,
                                       capture_output=True, text=True, env=env, cwd=tmp, timeout=30)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        replies = {}
        for line in completed.stdout.splitlines():
            reply = json.loads(line)
            replies[reply["id"]] = reply

        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "deepseek-worker")
        tools = {tool["name"]: tool for tool in replies[2]["result"]["tools"]}
        self.assertEqual(set(tools), {"delegate", "record_outcome"})
        self.assertIn("no internet access", tools["delegate"]["description"])
        self.assertIn("128,000 tokens", tools["delegate"]["description"])
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertIn("not set up (no api key, base_url, model)", replies[3]["result"]["content"][0]["text"])
        self.assertEqual(replies[4]["error"]["code"], -32601)
        self.assertEqual(replies[None]["error"]["code"], -32700)


    def test_reads_utf8_even_when_the_system_encoding_is_not(self):
        # Windows pipes default to a code page such as cp1252, where the byte 0x81 in "ā" is undefined.
        name = "outil-ā-عربي"
        stdin = json.dumps(request(1, "tools/call", {"name": name, "arguments": {}}), ensure_ascii=False) + "\n"
        env = {key: value for key, value in os.environ.items() if not key.startswith("DEEPSEEK_WORKER_")}
        env["PYTHONIOENCODING"] = "cp1252"
        env.pop("PYTHONUTF8", None)
        completed = subprocess.run([sys.executable, str(REPO / "server.py")], input=stdin.encode("utf-8"),
                                   capture_output=True, env=env, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertNotIn(b"\r\n", completed.stdout)
        reply = json.loads(completed.stdout.decode("utf-8"))
        self.assertIn(name, reply["result"]["content"][0]["text"])


class ServerToolsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        write_files(self.root, {"README.md": "Demo\n"})
        self.log_path = Path(self._tmp.name) / "state" / "log.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def make_server(self, responses):
        return Server(make_config(), ScriptedGateway(responses), DelegationLog(self.log_path), io.StringIO())

    def log_entries(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def test_delegate_then_record_outcome(self):
        server = self.make_server([
            {"stop_reason": "tool_use", "content": [tool_use("a", "write_output", content="draft")]},
            {"stop_reason": "end_turn", "content": [text("Wrote a draft.")]},
        ])
        reply = server.handle("tools/call", {"name": "delegate", "arguments": {
            "task": "Draft it.", "project_dir": str(self.root), "output_path": "docs/draft.md"}})
        self.assertFalse(reply["isError"])
        message = reply["content"][0]["text"]
        self.assertIn("done in", message)
        self.assertIn("Output: docs/draft.md", message)
        self.assertIn("Worker's summary: Wrote a draft.", message)

        entry = self.log_entries()[0]
        self.assertEqual((entry["event"], entry["status"], entry["task"]), ("delegation", "done", "Draft it."))
        self.assertIn(entry["id"], message)

        reply = server.handle("tools/call", {"name": "record_outcome", "arguments": {
            "delegation_id": entry["id"], "outcome": "edited", "note": "fixed a date"}})
        self.assertFalse(reply["isError"])
        outcome = self.log_entries()[1]
        self.assertEqual((outcome["event"], outcome["outcome"], outcome["note"]), ("outcome", "edited", "fixed a date"))

    def test_a_down_gateway_tells_claude_to_do_the_task_itself(self):
        server = self.make_server([GatewayError("Could not reach the gateway")])
        reply = server.handle("tools/call", {"name": "delegate", "arguments": {
            "task": "Draft it.", "project_dir": str(self.root), "output_path": "docs/draft.md"}})
        self.assertTrue(reply["isError"])
        message = reply["content"][0]["text"]
        self.assertIn("Problem: Could not reach the gateway", message)
        self.assertIn("Do the task yourself now. Do not retry the delegation.", message)

    def test_record_outcome_checks_its_input(self):
        server = self.make_server([])
        reply = server.handle("tools/call", {"name": "record_outcome", "arguments": {
            "delegation_id": "missing", "outcome": "accepted"}})
        self.assertTrue(reply["isError"])
        reply = server.handle("tools/call", {"name": "record_outcome", "arguments": {
            "delegation_id": "missing", "outcome": "great"}})
        self.assertIn("must be one of", reply["content"][0]["text"])

    def test_delegate_reports_missing_arguments(self):
        reply = self.make_server([]).handle("tools/call", {"name": "delegate", "arguments": {"task": "x"}})
        self.assertTrue(reply["isError"])
        self.assertIn("project_dir, output_path", reply["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
