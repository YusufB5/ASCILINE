"""Event-loop-owned playback commands with conservative seek coalescing."""
from collections import deque


class PlaybackCommands:
    """Keep the newest pending seek within a run of seek/telemetry commands.

    An already-running seek is not cancelled. Pause, reinit, filter, disconnect
    and unknown commands are ordering barriers: never move a seek across them.
    """

    def __init__(self):
        self._items = deque()

    def empty(self):
        return not self._items

    def get_nowait(self):
        return self._items.popleft()

    def put_nowait(self, command):
        removed = 0
        if command.get("type") == "seek":
            tail = []
            while self._items and self._items[-1].get("type") in ("seek", "buffer", "playback-ready"):
                item = self._items.pop()
                if item.get("type") == "seek":
                    removed += 1
                else:
                    tail.append(item)
            self._items.extend(reversed(tail))
        self._items.append(command)
        return removed
