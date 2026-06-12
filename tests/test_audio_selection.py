from fastapi.testclient import TestClient

import stream_server


def muted_entry(video):
    return {
        "video": video,
        "mode": 1,
        "vol": 0,
        "pixel": False,
        "cols": 200,
        "rows": 0,
    }


def test_indexed_audio_does_not_use_global_current_index():
    client = TestClient(stream_server.app)
    stream_server.app.state.queue = [
        muted_entry("missing-a.mp4"),
        muted_entry("missing-b.mp4"),
    ]
    stream_server.app.state.current_index = 1

    response = client.get("/audio/0")

    assert response.status_code == 204


def test_indexed_audio_rejects_out_of_range_index():
    client = TestClient(stream_server.app)
    stream_server.app.state.queue = [muted_entry("missing-a.mp4")]
    stream_server.app.state.current_index = 0

    response = client.get("/audio/99")

    assert response.status_code == 404
