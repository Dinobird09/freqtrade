"""Offline voice: a recorded clip goes to the dashboard and comes back as words."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from research.trendbot import voice
from research.trendbot.config import StrategyConfig
from research.trendbot.dashboard import Controller, FleetRuntime, make_handler


def test_transcribe_checks_the_clip_and_uses_the_engine():
    heard = []

    def engine(pcm, rate):
        heard.append((len(pcm), rate))
        return "  Jarvis STATUS "

    assert voice.transcribe(b"\0\0" * 1600, engine=engine) == "jarvis status"
    assert heard == [(3200, 16000)]
    assert voice.transcribe(b"", engine=engine) == ""
    with pytest.raises(ValueError, match="at most 15 seconds"):
        voice.transcribe(b"\0" * (16000 * 2 * 16), engine=engine)
    assert voice.status()["installed"] in (True, False)  # never downloads anything


def test_the_dashboard_transcribes_a_clip(tmp_path):
    ctl = Controller(tmp_path, None)
    rt = FleetRuntime(None, {"bot": (tmp_path, StrategyConfig(), ctl)})
    handler = make_handler(
        None, None, 15, token="tok", runtime=rt, voice_engine=lambda pcm, rate: "earnings today"
    )
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body, token="tok"):
        req = urllib.request.Request(
            base + "/api/voice?rate=16000", data=body, headers={"X-Trendbot-Token": token}
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert post(b"\0\0" * 8000) == (200, {"ok": True, "text": "earnings today"})
        assert post(b"\0\0" * 8000, token="bad")[0] == 403  # only the dashboard page may use it
        assert post(b"\0" * (16000 * 2 * 16))[0] == 400
        with urllib.request.urlopen(base + "/api/voice/status") as r:
            assert "installed" in json.loads(r.read())
    finally:
        srv.shutdown()
