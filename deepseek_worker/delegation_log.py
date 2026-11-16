"""A local record of each delegation and how Claude judged the result.

One JSON object per line. It stays on this machine and is never sent anywhere.
"""

import json
import sys
import threading
import uuid
from datetime import datetime, timezone

OUTCOMES = ("accepted", "edited", "rejected")
MAX_TASK_CHARS = 300


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DelegationLog:
    def __init__(self, path, now=_now):
        self.path = path
        self._now = now
        self._lock = threading.Lock()

    @staticmethod
    def new_id():
        return uuid.uuid4().hex[:10]

    def record_delegation(self, delegation_id, project_dir, task, result):
        self._append({
            "event": "delegation",
            "id": delegation_id,
            "time": self._now(),
            "project": project_dir,
            "task": task[:MAX_TASK_CHARS],
            "output_path": result.output_path,
            "status": result.status,
            "turns": result.turns,
            "files_read": len(result.files_read),
            "duration_seconds": result.duration_seconds,
            "error": result.error,
        })

    def record_outcome(self, delegation_id, outcome, note=""):
        """Returns False when the id is not in the log."""
        if not self._has_delegation(delegation_id):
            return False
        self._append({"event": "outcome", "id": delegation_id, "time": self._now(),
                      "outcome": outcome, "note": note})
        return True

    def _has_delegation(self, delegation_id):
        try:
            with self.path.open(encoding="utf-8") as lines:
                for line in lines:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("event") == "delegation" and entry.get("id") == delegation_id:
                        return True
        except OSError:
            pass
        return False

    def _append(self, entry):
        # A failed log write must never fail the delegation itself.
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as log:
                    log.write(json.dumps(entry) + "\n")
        except OSError as error:
            print(f"deepseek-worker: could not write the log at {self.path}: {error}", file=sys.stderr)
