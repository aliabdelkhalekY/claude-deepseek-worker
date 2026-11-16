"""The only file access the worker has: read inside the project, write one output file."""

import fnmatch
import os
import re
from pathlib import Path

SKIPPED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next",
}
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "*.keystore", "*.jks", "id_rsa*", "id_ed25519*", "id_ecdsa*",
)
MAX_READ_LINES = 2000
MAX_READ_CHARS = 100_000
MAX_LIST_RESULTS = 500
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILE_BYTES = 1_000_000


class SandboxError(Exception):
    """The worker asked for something it is not allowed or able to do."""


def _is_secret(name):
    # Case does not matter: on macOS and Windows, .ENV is the same file as .env.
    return any(fnmatch.fnmatchcase(name.lower(), pattern) for pattern in SECRET_PATTERNS)


class Sandbox:
    def __init__(self, root, output_path):
        self.root = Path(root).resolve()
        self.output = self.resolve(output_path)
        if self.output.is_dir():
            raise SandboxError(f"output_path {output_path} is a folder, not a file.")

    def resolve(self, path):
        if not path:
            raise SandboxError("A path is required.")
        # resolve() follows symlinks, so a link that points outside the project is caught here.
        candidate = (self.root / path).resolve()
        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            raise SandboxError(f"{path} is outside the project.") from None
        if ".git" in (part.lower() for part in relative.parts):
            raise SandboxError(f"{path} is inside .git, which the worker cannot use.")
        if _is_secret(relative.name):
            raise SandboxError(f"{path} may hold secrets, so the worker cannot use it.")
        return candidate

    def relative(self, path):
        return path.relative_to(self.root).as_posix()

    def _walk(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIPPED_DIRS)
            for name in sorted(filenames):
                if not _is_secret(name):
                    yield Path(os.path.relpath(os.path.join(dirpath, name), self.root)).as_posix()

    @staticmethod
    def _matches(relative, pattern):
        return fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative.rsplit("/", 1)[-1], pattern)

    def list_files(self, pattern="*"):
        results = []
        for relative in self._walk():
            if self._matches(relative, pattern):
                results.append(relative)
                if len(results) >= MAX_LIST_RESULTS:
                    results.append(f"(stopped at {MAX_LIST_RESULTS} files; use a narrower pattern)")
                    break
        return "\n".join(results) or "No files match."

    def read_file(self, path, offset=1, limit=MAX_READ_LINES):
        target = self.resolve(path)
        if not target.is_file():
            raise SandboxError(f"{path} is not a file.")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise SandboxError(f"{path} is not a text file.") from None
        lines = text.splitlines()
        start = max(int(offset), 1)
        chunk = lines[start - 1:start - 1 + max(min(int(limit), MAX_READ_LINES), 1)]
        output = "\n".join(f"{number:>6}\t{line}" for number, line in enumerate(chunk, start))
        if len(output) > MAX_READ_CHARS:
            output = output[:MAX_READ_CHARS] + f"\n(cut at {MAX_READ_CHARS} characters; read a smaller range)"
        end = start + len(chunk) - 1
        if end < len(lines):
            output += f"\n(lines {start}-{end} of {len(lines)}; pass offset to read more)"
        return output or "(empty file)"

    def search(self, pattern, file_pattern="*"):
        try:
            regex = re.compile(pattern)
        except re.error as error:
            raise SandboxError(f"Not a valid regular expression: {error}") from None
        matches = []
        for relative in self._walk():
            if not self._matches(relative, file_pattern):
                continue
            path = self.root / relative
            try:
                if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{relative}:{number}: {line.strip()[:200]}")
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        matches.append(f"(stopped at {MAX_SEARCH_RESULTS} matches; narrow the search)")
                        return "\n".join(matches)
        return "\n".join(matches) or "No matches."

    def write_output(self, content):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {self.relative(self.output)}."
