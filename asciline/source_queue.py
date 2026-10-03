"""Bounded source decode-ahead. Encoded packets/predictors never enter this queue."""
from collections import deque
from threading import Condition, Thread
import time


class SourceQueue:
    """Single owner of decoder next/grab until close() has joined the worker.

    At most capacity prepared/in-progress source frames, plus the frame currently
    being encoded by the consumer. Pause naturally stops at the capacity. Before
    seek, resize or release, close this queue, then reposition the decoder and
    create a new queue. Never discard queued frames without repositioning.
    """

    def __init__(self, decoder, skip_n=1, capacity=3):
        if capacity not in (1, 2, 3) or skip_n < 1:
            raise ValueError("capacity must be 1..3 and skip_n must be positive")
        self.decoder = decoder
        self.skip_n = skip_n
        self.capacity = capacity
        self._ready = deque()
        self._condition = Condition()
        self._stopped = False
        self._ended = False
        self._error = None
        self._thread = Thread(target=self._run, name="asciline-source", daemon=True)
        self._thread.start()

    def _run(self):
        first = True
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stopped or len(self._ready) < self.capacity)
                    if self._stopped:
                        return
                started = time.perf_counter()
                if not first:
                    for _ in range(self.skip_n - 1):
                        if not self.decoder.grab():
                            return
                try:
                    gray, bgr = next(self.decoder)
                except StopIteration:
                    return
                first = False
                item = (gray, bgr, (time.perf_counter() - started) * 1000)
                with self._condition:
                    if self._stopped:
                        return
                    self._ready.append(item)
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._error = exc
        finally:
            with self._condition:
                self._ended = True
                self._condition.notify_all()

    def take(self):
        """Blocking consumer API; call in executor. None means EOF/closed."""
        with self._condition:
            self._condition.wait_for(lambda: self._ready or self._ended or self._stopped)
            if self._stopped:
                return None
            if self._ready:
                gray, bgr, work_ms = self._ready.popleft()
                depth = len(self._ready)
                self._condition.notify_all()
                return gray, bgr, work_ms, depth
            if self._error is not None:
                raise self._error
            return None

    def close(self):
        """Wait for any native read to finish before decoder mutation/release."""
        with self._condition:
            self._stopped = True
            self._ready.clear()
            self._condition.notify_all()
        self._thread.join()
