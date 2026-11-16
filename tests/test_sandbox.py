import os
import tempfile
import unittest
from pathlib import Path

from deepseek_worker.sandbox import Sandbox, SandboxError
from tests.helpers import write_files


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        write_files(self.root, {
            "README.md": "line one\nline two\nline three\n",
            "docs/guide.md": "# Guide\nSee the README.\n",
            ".env": "SECRET=1\n",
            "certs/server.pem": "secret\n",
            ".git/config": "[core]\n",
            "node_modules/pkg/index.js": "x\n",
        })
        write_files(Path(self._tmp.name), {"outside.txt": "not yours\n"})
        self.sandbox = Sandbox(self.root, "out/draft.md")

    def tearDown(self):
        self._tmp.cleanup()

    def test_rejects_paths_outside_the_project(self):
        for path in ("../outside.txt", str(Path(self._tmp.name) / "outside.txt")):
            with self.assertRaisesRegex(SandboxError, "outside the project"):
                self.sandbox.read_file(path)

    def test_rejects_symlink_that_points_outside(self):
        os.symlink(Path(self._tmp.name) / "outside.txt", self.root / "link.txt")
        with self.assertRaisesRegex(SandboxError, "outside the project"):
            self.sandbox.read_file("link.txt")

    def test_rejects_git_and_secret_files(self):
        with self.assertRaisesRegex(SandboxError, ".git"):
            self.sandbox.read_file(".git/config")
        for path in (".env", "certs/server.pem"):
            with self.assertRaisesRegex(SandboxError, "secrets"):
                self.sandbox.read_file(path)

    def test_output_path_cannot_be_a_secret_or_outside(self):
        with self.assertRaises(SandboxError):
            Sandbox(self.root, ".env")
        with self.assertRaises(SandboxError):
            Sandbox(self.root, "../escape.md")
        with self.assertRaisesRegex(SandboxError, "folder"):
            Sandbox(self.root, "docs")

    def test_list_files_skips_secrets_and_tool_folders(self):
        listed = self.sandbox.list_files().splitlines()
        self.assertEqual(listed, ["README.md", "docs/guide.md"])
        self.assertEqual(self.sandbox.list_files("*.md").splitlines(), ["README.md", "docs/guide.md"])
        self.assertEqual(self.sandbox.list_files("docs/*"), "docs/guide.md")

    def test_read_file_numbers_lines_and_pages(self):
        self.assertEqual(self.sandbox.read_file("README.md", offset=2, limit=1),
                         "     2\tline two\n(lines 2-2 of 3; pass offset to read more)")

    def test_search_finds_lines(self):
        self.assertEqual(self.sandbox.search("README", "*.md"), "docs/guide.md:2: See the README.")
        self.assertEqual(self.sandbox.search("SECRET"), "No matches.")
        with self.assertRaisesRegex(SandboxError, "regular expression"):
            self.sandbox.search("(")

    def test_write_output_writes_only_the_output_file(self):
        self.sandbox.write_output("hello")
        self.assertEqual((self.root / "out/draft.md").read_text(), "hello")


if __name__ == "__main__":
    unittest.main()
