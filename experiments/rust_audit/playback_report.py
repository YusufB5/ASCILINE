"""Summarize --perf-record JSONL files; optionally compare multiple recordings."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics


STAGES = ("source_ms", "encode_ms", "handoff_ms", "send_ms", "produce_ms", "source_work_ms")


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    def percentile(q):
        return values[max(0, math.ceil(len(values) * q) - 1)]
    return {"n": len(values), "mean": statistics.fmean(values),
            "p95": percentile(.95), "p99": percentile(.99), "max": values[-1]}


def read_trace(path):
    records, warnings = [], []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise ValueError(f"Invalid record on line {index + 1}: {path}")
            warnings.append("Incomplete final line ignored (recording may still be running).")
    if not records or records[0].get("type") != "run" or records[0].get("schema") != 1:
        raise ValueError(f"Not a supported playback recording: {path}")
    if records[-1].get("type") != "end":
        warnings.append("No end footer: live or interrupted recording; tail/drop count may be missing.")
    elif records[-1]["dropped_records"]:
        warnings.append(f"Recorder dropped {records[-1]['dropped_records']} diagnostic records; results are incomplete.")
    seq = [r["seq"] for r in records if "seq" in r]
    if any(b != a + 1 for a, b in zip(seq, seq[1:])):
        warnings.append("Gaps in diagnostic sequence numbers; do not treat this as a complete capture.")
    return records, warnings


def summarize(records):
    segments = {}
    for event in records:
        key = (event.get("session"), event.get("segment"))
        if event["type"] == "stream":
            segments[key] = {"config": event, "events": []}
        if key in segments:
            segments[key]["events"].append(event)
    result = []
    for (session, segment), item in segments.items():
        config, events = item["config"], item["events"]
        frames = [e for e in events if e["type"] == "frame" and not e["preroll"]]
        clients = [e for e in events if e["type"] == "client" and e.get("current_epoch")]
        budget = 1000 / config["fps"]
        slow = [e for e in frames if e["produce_ms"] + e["send_ms"] > budget]
        dominant = Counter(max(STAGES[:4], key=lambda s: e[s]) for e in slow)
        skips = Counter(e["reason"] for e in events if e["type"] == "skip" and e["advanced"])
        # Consecutive send cadence only, excluding startup, resume and seeks.
        # This is server delivery, not the browser's rendered FPS.
        gaps, previous = [], None
        for e in events:
            if e["type"] in ("pause", "playback_ready", "seek", "stream"):
                previous = None
            if e["type"] == "frame":
                if e["preroll"]:
                    previous = None
                else:
                    if previous is not None:
                        gaps.append(e["t_ms"] - previous)
                    previous = e["t_ms"]
        result.append({
            "session": session, "segment": segment, "config": config,
            "frames": len(frames),
            "preroll_frames": sum(e["type"] == "frame" and e["preroll"] for e in events),
            "budget_ms": budget, "over_budget": len(slow), "slow_dominant": dict(dominant),
            "skips": dict(skips),
            "stages": {s: distribution([e[s] for e in frames if s in e]) for s in STAGES},
            "send_lag_ms": distribution([e["send_lag_ms"] for e in frames if e["send_lag_ms"] is not None]),
            "send_gap_ms": distribution(gaps),
            "wire_bytes": sum(e["wire_bytes"] for e in frames),
            "client_samples": len(clients),
            "hidden_samples": sum(e.get("hidden", False) for e in clients),
            "client_depth_max": max((e["depth"] for e in clients), default=None),
            "client_decode_ms": clients[-1].get("decodeMs") if clients else None,
            "client_render_ms": clients[-1].get("renderMs") if clients else None,
            "client_late_drops": max((e.get("lateDrops", 0) for e in clients), default=None),
            "client_decode_errors": max((e.get("decodeErrors", 0) for e in clients), default=None),
            "client_decoded": clients[-1].get("decoded") if clients else None,
            "client_rendered": clients[-1].get("rendered") if clients else None,
            "render_buffer": distribution([e["renderBuffer"] for e in clients if "renderBuffer" in e]),
            "display_lag_ms": distribution([(e["clock"] - e["displayTime"]) * 1000
                                            for e in clients if "clock" in e and "displayTime" in e]),
            "slowest": sorted(frames, key=lambda e: e["produce_ms"] + e["send_ms"], reverse=True)[:10],
            "events": [e for e in events if e["type"] not in ("frame", "client", "skip", "stream")],
        })
    return result


def format_report(path, records, warnings, segments):
    lines = [f"# Playback recording: {path.name}", "",
             f"Captured: {records[0].get('utc')} | Python {records[0].get('python')}", "",
             "All times below are milliseconds unless stated otherwise. Preroll is excluded from frame statistics.",
             "source_ms is the consumer's source wait/work; with decode-ahead it measures queue wait. source_work_ms is actual sampling/decode/resize work (overlaps encode when queued).",
             "encode_ms includes frame processing and transport encoding. Do not add source_work_ms to produce_ms.",
             "For pixel DCT, encode is the PixelProfile.encode call plus packet preparation, not just the DCT transform.",
             "Handoff is executor/scheduling remainder. Send is awaited WebSocket send, not network RTT.",
             "The largest stage on an over-budget frame is a lead to investigate, not proof of causation.", ""]
    lines += [f"**Warning:** {w}" for w in warnings]
    if warnings:
        lines.append("")
    for segment in segments:
        c = segment["config"]
        lag = segment["display_lag_ms"]
        lag_text = f"{lag['mean']:.2f} / {lag['p95']:.2f} / {lag['max']:.2f} ms" if lag else "unavailable"
        buffer = segment["render_buffer"]
        buffer_text = f"{buffer['mean']:.2f} / {buffer['max']:.0f} frames" if buffer else "unavailable"
        lines += [f"## {segment['session']} / segment {segment['segment']} ({c['reason']})", "",
                  f"{c['engine']} / {c['codec']} / {c['cols']}x{c['rows']} / {c['fps']:.3f} FPS / quality {c['quality']}",
                  f"Source: {c.get('source', 'unknown')} | decode-ahead: {c.get('decode_ahead', 0)}", "",
                  f"Frames: {segment['frames']} (+{segment['preroll_frames']} preroll). "
                  f"Budget: {segment['budget_ms']:.2f} ms. Over budget (produce + send): {segment['over_budget']}.",
                  f"Source skips: clock={segment['skips'].get('clock', 0)}, backlog={segment['skips'].get('backlog', 0)}.",
                  f"Largest stage on over-budget frames: {segment['slow_dominant'] or 'none'}.", "",
                  "| Stage | Mean | p95 | p99 | Max |", "| --- | ---: | ---: | ---: | ---: |"]
        for name, stats in {**segment["stages"], "send_lag_ms": segment["send_lag_ms"],
                            "send_gap_ms": segment["send_gap_ms"]}.items():
            if stats:
                lines.append(f"| {name} | {stats['mean']:.2f} | {stats['p95']:.2f} | {stats['p99']:.2f} | {stats['max']:.2f} |")
        lines += ["", f"Browser reports: {segment['client_samples']} (hidden tab: {segment['hidden_samples']}).",
                  f"Last reported epoch-average decode/draw: {segment['client_decode_ms']} / {segment['client_render_ms']} ms. "
                  f"Max pending decode depth: {segment['client_depth_max']}. "
                  f"Reported cumulative late drops/errors: {segment['client_late_drops']} / {segment['client_decode_errors']}.",
                  f"Last reported decoded/rendered counts: {segment['client_decoded']} / {segment['client_rendered']}.",
                  f"Sampled display lag mean/p95/max: {lag_text}. Ready-frame buffer mean/max: {buffer_text}.",
                  "Browser values are sampled every 250 ms, may miss the tail, and are not per-frame percentiles. "
                  "Display lag uses the player's master clock (audio or wall clock); it is not a physical A/V sync measurement.", "",
                  "### Slowest frames", "",
                  "| Run time (s) | Video PTS (s) | Frame | Source | Encode | Handoff | Send |",
                  "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for e in segment["slowest"]:
            lines.append(f"| {e['t_ms']/1000:.3f} | {e['pts']:.3f} | {e['frame']} | "
                         f"{e['source_ms']:.2f} | {e['encode_ms']:.2f} | {e['handoff_ms']:.2f} | {e['send_ms']:.2f} |")
        lines += ["", "### Playback events", ""]
        for e in segment["events"]:
            fields = {k: v for k, v in e.items() if k not in ("session", "segment", "seq", "t_ms", "type")}
            lines.append(f"- {e['t_ms']/1000:.3f}s: {e['type']} {json.dumps(fields)}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, nargs="+", help="JSONL files, or a directory to select its latest recording")
    args = parser.parse_args()
    comparison = []
    for path in args.recordings:
        if path.is_dir():
            matches = sorted(path.glob("playback-*.jsonl"), key=lambda p: p.stat().st_mtime_ns)
            if not matches:
                parser.error(f"No playback recordings in {path}")
            path = matches[-1]
        try:
            records, warnings = read_trace(path)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        segments = summarize(records)
        report = path.with_suffix(".report.md")
        report.write_text(format_report(path, records, warnings, segments), encoding="utf-8")
        path.with_suffix(".summary.json").write_text(json.dumps({"warnings": warnings, "segments": segments}, indent=2), encoding="utf-8")
        print(f"Report: {report.resolve()}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for s in segments:
            c = s["config"]
            comparison.append((path.name, s))
            print(f"  segment {s['segment']} {c['codec']} {c['cols']}x{c['rows']}: "
                  f"{s['frames']} frames, over-budget {s['over_budget']}, skips {s['skips']}; "
                  f"slow-frame dominant stages {s['slow_dominant']}")
    if len(args.recordings) > 1:
        print("\nComparison (same input, scene, settings and foreground state required; each segment is separate):")
        print("Recording / segment | source p95 | encode p95 | handoff p95 | send p95 | lag p95")
        for name, s in comparison:
            stats = [s["stages"][key] for key in STAGES[:4]] + [s["send_lag_ms"]]
            values = [f"{stat['p95']:.2f}" if stat else "n/a" for stat in stats]
            print(f"{name} / {s['segment']} | " + " | ".join(values))


if __name__ == "__main__":
    main()
