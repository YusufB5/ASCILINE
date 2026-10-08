from threading import Event, Thread

import pytest

from asciline.source_queue import SourceQueue


class Decoder:
    def __init__(self, count=10):
        self.index = 0
        self.count = count
        self.full = Event()

    def __next__(self):
        if self.index >= self.count:
            raise StopIteration
        value = self.index
        self.index += 1
        if self.index == 3:
            self.full.set()
        return None, value

    def grab(self):
        if self.index >= self.count:
            return False
        self.index += 1
        return True


@pytest.mark.parametrize("skip_n", [1, 2, 3])
@pytest.mark.parametrize("capacity", [2, 3])
def test_sampling_order_eof_and_source_drops(skip_n, capacity):
    decoder = Decoder()
    queue = SourceQueue(decoder, skip_n, capacity)
    try:
        frames = []
        while (item := queue.take()) is not None:
            frames.append(item[1])
            assert item[2] >= 0 and 0 <= item[3] < capacity
        assert frames == list(range(0, 10, skip_n))
        assert queue.take() is None
    finally:
        queue.close()


def test_pause_is_bounded_and_reset_cannot_leak_old_frames():
    decoder = Decoder(100)
    queue = SourceQueue(decoder, capacity=3)
    assert decoder.full.wait(2)
    queue.close()
    assert decoder.index == 3  # No fourth decode while consumer is paused.
    assert queue.take() is None
    decoder.index = 50  # Seek/resize only after worker has joined.
    fresh = SourceQueue(decoder)
    try:
        assert fresh.take()[1] == 50
        fresh.take()  # Drop a SOURCE frame before encoding, preserve ordering.
        assert fresh.take()[1] == 52
    finally:
        fresh.close()
    fresh.close()


def test_close_waits_for_native_read_before_decoder_can_be_reused():
    entered, release, closed = Event(), Event(), Event()

    class SlowDecoder(Decoder):
        def __next__(self):
            entered.set()
            assert release.wait(3)
            return super().__next__()

    decoder = SlowDecoder()
    queue = SourceQueue(decoder)
    assert entered.wait(2)

    def stop():
        queue.close()
        closed.set()

    thread = Thread(target=stop)
    thread.start()
    try:
        assert not closed.wait(.05)
    finally:
        release.set()
        thread.join(3)
    assert closed.is_set()
    assert queue.take() is None
    assert decoder.index == 1


def test_worker_exception_reaches_consumer_and_stops():
    class BrokenDecoder(Decoder):
        def __next__(self):
            raise RuntimeError("source failed")

    queue = SourceQueue(BrokenDecoder())
    try:
        with pytest.raises(RuntimeError, match="source failed"):
            queue.take()
    finally:
        queue.close()
