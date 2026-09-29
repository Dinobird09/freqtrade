"""The single-file build: one .pyz that runs every command and re-launches itself."""

import importlib.util
import subprocess
import sys
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "build_pyz", Path(__file__).resolve().parents[1] / "scripts" / "build_pyz.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build = _mod.build


def test_the_pyz_runs_every_command(tmp_path):
    pyz = build(tmp_path / "trendbot.pyz")

    def run(*a):
        return subprocess.run(
            [sys.executable, str(pyz), *a], capture_output=True, text=True, timeout=120
        )

    assert "bot " in run().stdout and "dashboard" in run().stdout
    assert "run,status,flatten" in run("bot", "--help").stdout
    state = tmp_path / "state"
    state.mkdir()
    out = tmp_path / "page.html"
    res = run("dashboard", "--state-dir", str(state), "--export", str(out))
    assert res.returncode == 0, res.stderr
    assert "Chantisimo" in out.read_text()  # the page is read from inside the archive
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(pyz)!r}); "
            "from research.trendbot import launch; print(launch.command('retrain', 'collect')[0])",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert str(pyz) in probe.stdout and "jobs" in probe.stdout  # jobs re-launch the same file
