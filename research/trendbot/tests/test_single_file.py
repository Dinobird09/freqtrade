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


def test_a_fresh_install_serves_the_dashboard(tmp_path):
    import json
    import socket
    import time
    import urllib.request

    pyz = build(tmp_path / "trendbot.pyz")
    home = tmp_path / "Trend bot"  # a new folder with a space in its name, nothing in it yet
    home.mkdir()
    example = Path(__file__).resolve().parents[1] / "bot_settings.example.json"
    (home / "bot.json").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, str(pyz), "dashboard", "--settings", "bot.json", "--port", str(port)],
        cwd=home,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        page = None
        for _ in range(100):
            assert proc.poll() is None, proc.communicate()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
                    page = r.read().decode("utf-8")
                break
            except OSError:
                time.sleep(0.2)
        assert page and "Chantisimo" in page
        state_dir = json.loads((home / "bot.json").read_text())["state_dir"]
        assert (home / state_dir).is_dir()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_the_pyz_reports_errors_instead_of_exiting_silently(tmp_path):
    pyz = build(tmp_path / "trendbot.pyz")
    res = subprocess.run(
        [sys.executable, str(pyz), "dashboard", "--state-dir", str(tmp_path / "missing")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 1 and "is not a directory" in res.stderr
