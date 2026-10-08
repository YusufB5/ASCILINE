import json
from pathlib import Path
from threading import Event

import pytest

from asciline.perf_trace import PlaybackTrace
from experiments.rust_audit.playback_report import read_trace, summarize


def test_recording_is_unique_and_drained_on_close(tmp_path):
    first = PlaybackTrace(tmp_path)
    second = PlaybackTrace(tmp_path)
    assert first.path != second.path
    for index in range(100):
        first.record("frame", frame=index)
    first.close()
    second.close()
    records, warnings = read_trace(first.path)
    assert not warnings
    assert [r["frame"] for r in records if r["type"] == "frame"] == list(range(100))
    assert records[-1]["dropped_records"] == 0
    assert records[-1]["last_seq"] == 101
    first.close()  # Shutdown paths may both close the same recorder.
    first.record("ignored")
    assert read_trace(first.path)[0] == records


def test_slow_disk_drops_diagnostics_without_blocking_producer(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original_open = Path.open

    class SlowFile:
        def __init__(self, file):
            self.file = file

        def write(self, text):
            entered.set()
            assert release.wait(5)
            return self.file.write(text)

        def flush(self):
            self.file.flush()

        def close(self):
            self.file.close()

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda self, *a, **kw: SlowFile(original_open(self, *a, **kw)))
        trace = PlaybackTrace(tmp_path, capacity=2)
    try:
        assert entered.wait(2)
        # Writer is blocked. All calls must return without waiting for it.
        for index in range(5):
            trace.record("frame", frame=index)
        assert trace.dropped == 3
    finally:
        release.set()
        trace.close()
    records, warnings = read_trace(trace.path)
    assert records[-1]["dropped_records"] == 3
    assert any("dropped 3" in warning for warning in warnings)


def test_report_separates_epochs_preroll_and_pause_gaps(tmp_path):
    records = []

    def add(kind, t, segment=1, **fields):
        records.append(dict(type=kind, t_ms=t, session="test", segment=segment, **fields))

    def stream(t, segment):
        add("stream", t, segment, fps=60, codec="dct", engine="rust", cols=464, rows=256,
            quality=70, reason="start" if segment == 1 else "seek")

    def frame(t, segment, source, preroll=False):
        add("frame", t, segment, frame=1, pts=.1, preroll=preroll, source_ms=source,
            encode_ms=6, handoff_ms=1, send_ms=.1, produce_ms=source+7,
            wire_bytes=100, send_lag_ms=None if preroll else 3)

    stream(0, 1)
    frame(1, 1, 200, True)
    frame(20, 1, 5)
    frame(40, 1, 25)
    add("pause", 41, paused=True)
    add("playback_ready", 2000, clock=.1)
    frame(2020, 1, 5)
    add("skip", 2021, advanced=True, reason="clock")
    add("client", 2022, current_epoch=False, depth=100, decodeErrors=100)
    add("client", 2023, current_epoch=True, depth=0, decodeErrors=0)
    stream(2040, 2)
    frame(2041, 2, 300, True)
    frame(2060, 2, 10)
    result = summarize(records)
    assert len(result) == 2
    first, second = result
    assert first["frames"] == 3 and first["preroll_frames"] == 1
    assert first["stages"]["source_ms"]["max"] == 25
    assert first["over_budget"] == 1
    assert first["slow_dominant"] == {"source_ms": 1}
    assert first["send_gap_ms"]["max"] == 20
    assert first["skips"] == {"clock": 1}
    assert first["client_decode_errors"] == 0
    assert second["stages"]["source_ms"]["max"] == 10


def test_partial_recordings_are_readable_but_marked_incomplete(tmp_path):
    path = tmp_path / "partial.jsonl"
    path.write_text(json.dumps({"type": "run", "schema": 1, "seq": 1}) + '\n{"type":', encoding="utf-8")
    records, warnings = read_trace(path)
    assert len(records) == 1
    assert len(warnings) == 2
    path.write_text('{broken}\n{}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid record"):
        read_trace(path)


def test_disk_failure_does_not_escape_into_playback(tmp_path, monkeypatch):
    class BrokenFile:
        def write(self, text):
            raise OSError("disk full")

        def close(self):
            pass

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda *a, **kw: BrokenFile())
        trace = PlaybackTrace(tmp_path)
    trace.close()
    assert trace.error == "disk full"
    trace.record("frame", frame=1)  # Recording has stopped; playback can continue.
