import pytest

from asciline.commands import PlaybackCommands


def drain(queue):
    result = []
    while not queue.empty():
        result.append(queue.get_nowait())
    return result


def test_pending_seeks_collapse_even_with_telemetry_between_them():
    queue = PlaybackCommands()
    assert queue.put_nowait(dict(type="seek", time=10, requestId=1)) == 0
    queue.put_nowait(dict(type="buffer", depth=0))
    queue.put_nowait(dict(type="playback-ready", requestId=1))
    assert queue.put_nowait(dict(type="seek", time=40, requestId=2)) == 1
    assert queue.put_nowait(dict(type="seek", time=25, requestId=3)) == 1
    assert drain(queue) == [dict(type="buffer", depth=0), dict(type="playback-ready", requestId=1),
                            dict(type="seek", time=25, requestId=3)]


@pytest.mark.parametrize("barrier", ["pause", "reinit", "filter", "disconnect", "unknown"])
def test_controls_remain_ordering_barriers(barrier):
    queue = PlaybackCommands()
    items = [dict(type="seek", time=10), dict(type=barrier), dict(type="seek", time=25)]
    for item in items:
        assert queue.put_nowait(item) == 0
    assert drain(queue) == items


def test_running_seek_is_not_cancelled_but_pending_seeks_are_replaced():
    queue = PlaybackCommands()
    queue.put_nowait(dict(type="seek", time=10))
    running = queue.get_nowait()
    queue.put_nowait(dict(type="seek", time=40))
    assert queue.put_nowait(dict(type="seek", time=25)) == 1
    assert running["time"] == 10
    assert drain(queue) == [dict(type="seek", time=25)]
