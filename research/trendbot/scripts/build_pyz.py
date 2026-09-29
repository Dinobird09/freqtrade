"""Build the single-file version of the bot: dist/trendbot.pyz.

    python research/trendbot/scripts/build_pyz.py

The result is ONE file that holds every module and the dashboard page. Copy it anywhere and
run it with Python 3.11+::

    python trendbot.pyz bot run --settings bot.json
    python trendbot.pyz dashboard --settings bot.json
    python trendbot.pyz dashboard --fleet fleet.json

The exchange library is not bundled: ``pip install ccxt`` once on that computer (the optional
ML extras in requirements-ml.txt likewise).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipapp
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]  # research/trendbot
REPO = HERE.parents[1]


def build(out: Path | None = None) -> Path:
    out = out or REPO / "dist" / "trendbot.pyz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "src"
        pkg = stage / "research" / "trendbot"
        pkg.mkdir(parents=True)
        (stage / "research" / "__init__.py").write_text("", encoding="utf-8")
        for f in HERE.iterdir():
            if f.is_file() and f.suffix in (".py", ".html", ".md", ".json", ".txt"):
                shutil.copy2(f, pkg / f.name)
        zipapp.create_archive(
            stage,
            out,
            interpreter="/usr/bin/env python3",
            main="research.trendbot.__main__:main",
            compressed=True,
        )
    return out


if __name__ == "__main__":
    path = build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"built {path} ({path.stat().st_size / 1024:.0f} KB)")
