"""How the bot re-launches itself (bot process, background jobs): the same way it was run.

From the source tree it runs ``python -m research.trendbot.<module>``; from the single-file
build it runs ``python trendbot.pyz <command>`` (see ``__main__.py``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# module -> command name of the single-file build
COMMANDS = {
    "live_bot": "bot",
    "dashboard": "dashboard",
    "retrain": "jobs",
    "traders": "traders",
    "dex_scan": "dex",
    "lab": "lab",
    "fetch_data": "fetch",
}


def archive() -> str | None:
    """The .pyz this code runs from, or None when it runs from the source tree."""
    return getattr(globals().get("__loader__"), "archive", None)


def command(module: str, *args: str) -> tuple[list[str], dict[str, str]]:
    """(argv, environment) to run ``module`` with ``args`` in a new process."""
    env = dict(os.environ)
    pyz = archive()
    if pyz:
        return [sys.executable, pyz, COMMANDS[module], *args], env
    repo_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(repo_root), env.get("PYTHONPATH")]))
    return [sys.executable, "-m", f"research.trendbot.{module}", *args], env
