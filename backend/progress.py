"""
Live progress of each task, for the future screen and voice.

The graph and the executor report what they are doing:

    planned -> wave -> step -> ... -> approval_needed / answering -> done

Events are kept in memory for the most recent tasks only (they are
not history: the diary in memory.py is). A client reads them with
GET /tasks/{task_id}/events, or follows them live with
GET /tasks/{task_id}/stream.
"""

import threading
import time
from collections import OrderedDict, deque


# Events that end a task's stream.
FINAL_EVENTS = ("done", "waiting_approval", "failed")

_MAX_TASKS = 200
_MAX_EVENTS = 300


class ProgressLog:
    """Recent progress events of recent tasks, safe to use from many threads."""

    def __init__(self, max_tasks: int = _MAX_TASKS, max_events: int = _MAX_EVENTS):
        self._tasks = OrderedDict()
        self._lock = threading.Lock()
        self._max_tasks = max_tasks
        self._max_events = max_events

    def emit(self, task_id: str, kind: str, message: str, data: dict | None = None) -> None:

        if not task_id:
            return

        with self._lock:
            events = self._tasks.get(task_id)

            if events is None:
                events = self._tasks[task_id] = deque(maxlen=self._max_events)

                while len(self._tasks) > self._max_tasks:
                    self._tasks.popitem(last=False)

            self._tasks.move_to_end(task_id)

            number = events[-1]["n"] + 1 if events else 1

            events.append({
                "n": number,
                "time": time.strftime("%H:%M:%S"),
                "kind": kind,
                "message": message,
                "data": data or {},
            })

    def events(self, task_id: str, after: int = 0) -> list:
        """Events of one task with a number above `after`, oldest first."""

        with self._lock:
            return [e for e in self._tasks.get(task_id, ()) if e["n"] > after]

    def known(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._tasks

    def reporter(self, task_id: str):
        """A function(kind, message, data) that reports for one task."""

        def report(kind: str, message: str, data: dict | None = None) -> None:
            self.emit(task_id, kind, message, data)

        return report


_log = ProgressLog()


def get_progress() -> ProgressLog:
    return _log
