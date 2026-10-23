"""Offline speech recognition for the dashboard's voice commands (Brave, Firefox, no internet).

Chrome and Edge turn speech into text with their own online service. Brave ships the same
browser API but switches that service off, so voice there needs another engine. This one runs
on your own computer with Vosk (a small open-source speech model, about 40 MB; nothing you
say leaves the computer):

    .venv\\Scripts\\python -m pip install vosk          (Windows; macOS/Linux: .venv/bin/python)
    .venv\\Scripts\\python trendbot.pyz voice           (downloads the English model once)

The page records your voice, cuts it into a 16 kHz mono clip and posts it to the dashboard
(``POST /api/voice``), which answers with the words. Commands then run exactly as typed ones.
"""

from __future__ import annotations

import argparse
import json
import threading
from typing import Any


SAMPLE_RATE = 16_000
MAX_SECONDS = 15
MODEL_LANG = "en-us"
INSTALL_HINT = "install the offline voice engine: .venv\\Scripts\\python -m pip install vosk"

_lock = threading.Lock()
_model: Any = None
_error: str | None = None


def _load() -> Any:
    """The Vosk model, loaded once (and downloaded once, on first use)."""
    global _model, _error
    with _lock:
        if _model is not None:
            return _model
        try:
            import vosk
        except ImportError:
            _error = "not installed: " + INSTALL_HINT
            raise RuntimeError(_error) from None
        try:
            vosk.SetLogLevel(-1)
            _model = vosk.Model(lang=MODEL_LANG)
        except Exception as exc:  # no model yet and no internet, a broken download, ...
            _error = (
                f"the speech model could not be loaded ({exc}): "
                "run 'trendbot.pyz voice' once while online"
            )
            raise RuntimeError(_error) from exc
        _error = None
        return _model


def status() -> dict[str, Any]:
    """Is the offline engine usable? (Never downloads anything.)"""
    try:
        import vosk  # noqa: F401 - availability check only
    except ImportError:
        return {"installed": False, "ready": False, "hint": INSTALL_HINT}
    hint = None if _model is not None else "the model loads with the first command (a few seconds)"
    return {"installed": True, "ready": _model is not None, "error": _error, "hint": hint}


def transcribe(pcm: bytes, rate: int = SAMPLE_RATE, engine: Any = None) -> str:
    """16-bit little-endian mono PCM -> lower-case words ("" when nothing was understood)."""
    if not pcm:
        return ""
    if len(pcm) > rate * 2 * MAX_SECONDS:
        raise ValueError(f"a voice clip can be at most {MAX_SECONDS} seconds")
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if engine is not None:  # tests, or another recogniser with the same call
        return str(engine(pcm, rate)).strip().lower()
    import vosk

    rec = vosk.KaldiRecognizer(_load(), rate)
    rec.AcceptWaveform(pcm)
    return str(json.loads(rec.FinalResult()).get("text", "")).strip().lower()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="trendbot voice", description="set up offline voice commands (Vosk)"
    )
    p.parse_args(argv)
    try:
        _load()
    except RuntimeError as exc:
        print(f"voice: {exc}")
        return 1
    print("voice: the offline speech engine is ready (English). Voice now works in Brave too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
