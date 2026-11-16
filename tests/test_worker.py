import tempfile
import unittest
from pathlib import Path

from deepseek_worker import worker
from deepseek_worker.gateway import GatewayError
from tests.helpers import ScriptedGateway, make_config, text, tool_use, write_files


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_files(self.root, {
            "AGENTS.md": "Use short sentences.\n",
            "notes/facts.md": "The launch is in March.\n",
        })

    def tearDown(self):
        self._tmp.cleanup()

    def run_worker(self, responses, **kwargs):
        self.gateway = ScriptedGateway(responses)
        options = {"task": "Write a summary.", "project_dir": str(self.root), "output_path": "out/summary.md"}
        options.update(kwargs)
        config = options.pop("config", make_config())
        return worker.run(self.gateway, config, **options)

    def test_a_full_task(self):
        result = self.run_worker([
            {"stop_reason": "tool_use", "content": [
                {"type": "thinking", "thinking": "plan", "signature": "s"},
                tool_use("a", "list_files", pattern="*.md"),
                tool_use("b", "read_file", path="notes/facts.md"),
            ]},
            {"stop_reason": "tool_use", "content": [tool_use("c", "write_output", content="Launch: March.\n")]},
            {"stop_reason": "end_turn", "content": [text("Wrote a one-line summary.")]},
        ])
        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Wrote a one-line summary.")
        self.assertEqual(result.turns, 3)
        self.assertEqual(result.files_read, ["notes/facts.md"])
        self.assertEqual((self.root / "out/summary.md").read_text(), "Launch: March.\n")

        first = self.gateway.calls[0]
        self.assertIn("Use short sentences.", first["system"])
        self.assertIn("out/summary.md", first["system"])
        # The thinking block goes back unchanged, and every tool call gets a result.
        second = self.gateway.calls[1]["messages"]
        self.assertEqual(second[1]["content"][0]["type"], "thinking")
        results = second[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["a", "b"])
        self.assertIn("AGENTS.md\nnotes/facts.md", results[0]["content"])
        self.assertIn("The launch is in March.", results[1]["content"])

    def test_context_files_are_sent_with_the_task(self):
        self.run_worker([{"stop_reason": "end_turn", "content": []}], context_files=["notes/facts.md"])
        self.assertIn("The launch is in March.", self.gateway.calls[0]["messages"][0]["content"])

    def test_bad_tool_calls_return_errors_and_the_task_continues(self):
        result = self.run_worker([
            {"stop_reason": "tool_use", "content": [tool_use("a", "read_file", path="../etc/passwd")]},
            {"stop_reason": "tool_use", "content": [tool_use("b", "write_output", content="ok")]},
            {"stop_reason": "end_turn", "content": [text("done")]},
        ])
        self.assertEqual(result.status, "done")
        tool_result = self.gateway.calls[1]["messages"][2]["content"][0]
        self.assertTrue(tool_result["is_error"])
        self.assertIn("outside the project", tool_result["content"])

    def test_refuses_to_replace_an_existing_file_unless_asked(self):
        write_files(self.root, {"out/summary.md": "keep me"})
        result = self.run_worker([])
        self.assertEqual(result.status, "failed")
        self.assertIn("already exists", result.error)
        self.assertEqual(self.gateway.calls, [])

        result = self.run_worker([
            {"stop_reason": "tool_use", "content": [tool_use("a", "write_output", content="new")]},
            {"stop_reason": "end_turn", "content": []},
        ], overwrite=True)
        self.assertEqual(result.status, "done")
        self.assertEqual((self.root / "out/summary.md").read_text(), "new")

    def test_finishing_without_writing_is_reported(self):
        result = self.run_worker([{"stop_reason": "end_turn", "content": [text("I could not.")]}])
        self.assertEqual(result.status, "no_output")
        self.assertEqual(result.summary, "I could not.")

    def test_stops_after_max_turns(self):
        loop = {"stop_reason": "tool_use", "content": [tool_use("a", "list_files")]}
        result = self.run_worker([loop, loop], config=make_config(DEEPSEEK_WORKER_MAX_TURNS="2"))
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.turns, 2)

    def test_gateway_errors_fail_the_task(self):
        result = self.run_worker([GatewayError("key rejected")])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "key rejected")

    def test_reads_past_the_context_budget_are_refused(self):
        write_files(self.root, {"big.txt": "x" * 5000 + "\n"})
        result = self.run_worker([
            {"stop_reason": "tool_use", "content": [tool_use("a", "read_file", path="big.txt")]},
            {"stop_reason": "end_turn", "content": []},
        ], config=make_config(DEEPSEEK_WORKER_CONTEXT_TOKENS="1000"))
        tool_result = self.gateway.calls[1]["messages"][2]["content"][0]
        self.assertTrue(tool_result["is_error"])
        self.assertIn("budget", tool_result["content"])
        self.assertEqual(result.status, "no_output")

    def test_rejects_a_relative_project_dir(self):
        result = self.run_worker([], project_dir="relative/path")
        self.assertEqual(result.status, "failed")
        self.assertIn("absolute path", result.error)


if __name__ == "__main__":
    unittest.main()
