"""Opt-in playback diagnostics. No disk writes on the frame-producing path."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import platform
from queue import Empty, Full, Queue
import sys
from threading import Event, Lock, Thread
import time
from uuid import uuid4


class PlaybackTrace:
    """One bounded, non-blocking recorder per server; events contain no frame data.

    Call record from the server event loop. A slow disk drops diagnostic records,
    never video frames. A footer makes that loss visible to the report reader.
    """

    def __init__(self, directory, *, capacity=8192):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.path = directory / f"playback-{stamp}-{uuid4().hex[:8]}.jsonl"
        self._file = self.path.open("x", encoding="utf-8")
        self._queue = Queue(maxsize=capacity)
        self._stop = Event()
        self._lock = Lock()
        self._started = time.perf_counter()
        self._sequence = 0
        self.dropped = 0
        self.error = None
        self._thread = Thread(target=self._write, name="asciline-perf-trace", daemon=True)
        self.record("run", schema=1, utc=datetime.now(timezone.utc).isoformat(),
                    python=sys.version.split()[0], platform=platform.platform())
        self._thread.start()

    def record(self, kind, **fields):
        with self._lock:
            if self._stop.is_set() or self.error is not None:
                return
            self._sequence += 1
            record = dict(fields, type=kind, seq=self._sequence,
                          t_ms=(time.perf_counter() - self._started) * 1000)
            try:
                self._queue.put_nowait(record)
            except Full:
                self.dropped += 1

    def _write(self):
        try:
            last_flush = time.perf_counter()
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    item = self._queue.get(timeout=0.25)
                except Empty:
                    item = None
                if item is not None:
                    self._file.write(json.dumps(item, allow_nan=False, separators=(",", ":")) + "\n")
                if time.perf_counter() - last_flush >= 0.5:
                    self._file.flush()
                    last_flush = time.perf_counter()
            self._file.write(json.dumps({"type": "end", "dropped_records": self.dropped,
                                        "last_seq": self._sequence}) + "\n")
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)
            logging.getLogger(__name__).warning("Playback recording stopped: %s", exc)
        finally:
            try:
                self._file.close()
            except OSError:
                pass

    def close(self):
        with self._lock:
            self._stop.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            logging.getLogger(__name__).warning("Playback trace still flushing: %s", self.path)


def new_session_id():
    return uuid4().hex[:12]
