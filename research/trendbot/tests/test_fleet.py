"""Fleet of up to 10 bots: safety checks, the >5 approval pop-up flow, the HTTP API."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from research.trendbot.dashboard import FleetRuntime, make_handler
from research.trendbot.fleet import APPROVAL_WORD, Fleet, FleetError


PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "LTC/USDT",
    "TRX/USDT",
]


def _write_fleet(tmp_path, bots, **top):
    entries = []
    for i, b in enumerate(bots):
        path = tmp_path / f"bot{i}.json"
        path.write_text(
            json.dumps({"history_candles": 1200, "state_dir": str(tmp_path / f"s{i}"), **b})
        )
        entries.append({"name": b.get("_name", f"bot{i}"), "settings": path.name})
        b.pop("_name", None)
    fp = tmp_path / "fleet.json"
    fp.write_text(json.dumps({"bots": entries, **top}))
    return fp


def _ten(tmp_path, **top):
    # bot0 trades the correlated cluster; the others one non-cluster pair each (same account)
    bots = [{"pairs": PAIRS[:3]}] + [{"pairs": [p]} for p in PAIRS[3:12]]
    return _write_fleet(tmp_path, bots, **top)


def test_fleet_of_ten_loads(tmp_path):
    f = Fleet.load(_ten(tmp_path))
    assert len(f.bots) == 10 and f.approval_threshold == 5 and f.max_bots == 10


@pytest.mark.parametrize(
    ("bots", "top", "msg"),
    [
        (
            [{"pairs": [p]} for p in PAIRS[3:11]]
            + [{"pairs": PAIRS[:3]}] * 0
            + [{"pairs": ["TRX/USDT"]}, {"pairs": ["ATOM/USDT"]}, {"pairs": ["NEAR/USDT"]}],
            {},
            "maximum is 10",
        ),
        ([{"pairs": ["SOL/USDT"]}, {"pairs": ["SOL/USDT"]}], {}, "both trade SOL/USDT"),
        ([{"pairs": ["BTC/USDT", "ETH/USDT"]}, {"pairs": ["BNB/USDT"]}], {}, "cluster is split"),
        ([{"pairs": ["SOL/USDT"]}], {"max_bots": 11}, "max_bots"),
    ],
)
def test_unsafe_fleets_are_refused(tmp_path, bots, top, msg):
    with pytest.raises(FleetError, match=msg):
        Fleet.load(_write_fleet(tmp_path, bots, **top))


def test_same_pair_on_different_accounts_is_fine(tmp_path):
    fp = _write_fleet(
        tmp_path,
        [{"pairs": ["SOL/USDT"], "mode": "paper"}, {"pairs": ["SOL/USDT"], "mode": "testnet"}],
    )
    assert len(Fleet.load(fp).bots) == 2


def test_shared_state_dir_is_refused(tmp_path):
    fp = _write_fleet(
        tmp_path,
        [
            {"pairs": ["SOL/USDT"], "state_dir": str(tmp_path / "x")},
            {"pairs": ["XRP/USDT"], "state_dir": str(tmp_path / "x")},
        ],
    )
    with pytest.raises(FleetError, match="share the state dir"):
        Fleet.load(fp)


class FakeCtrl:
    def __init__(self):
        self.running = False

    def bot_status(self):
        return {"running": self.running, "can_start": True, "pid": 1, "heartbeat_age_s": 1}

    def act(self, req):
        if req["action"] == "start":
            self.running = True
            return True, "started"
        if req["action"] == "stop":
            self.running = False
            return True, "stop requested"
        return True, req["action"]


def _runtime(tmp_path):
    fleet = Fleet.load(_ten(tmp_path))
    ctrls = {n: FakeCtrl() for n in fleet.bots}
    entries = {
        n: (b.state_dir, b.settings.strategy_config(), ctrls[n]) for n, b in fleet.bots.items()
    }
    return FleetRuntime(fleet, entries), ctrls


def test_sixth_bot_needs_typed_approval(tmp_path):
    rt, ctrls = _runtime(tmp_path)
    for i in range(5):
        code, body = rt.act({"action": "start", "bot": f"bot{i}"})
        assert code == 200, body
    code, body = rt.act({"action": "start", "bot": "bot5"})
    assert code == 409 and body["needs_approval"] and not ctrls["bot5"].running
    assert body["bots"][0]["name"] == "bot5" and body["running"] == 5
    code, _ = rt.act({"action": "approve", "approval_id": body["approval_id"], "confirm": "yes"})
    assert code == 400 and not ctrls["bot5"].running
    code, ok = rt.act(
        {"action": "approve", "approval_id": body["approval_id"], "confirm": APPROVAL_WORD}
    )
    assert code == 200 and ctrls["bot5"].running and ok["message"].startswith("approved")
    # the same approval cannot be reused
    code, _ = rt.act(
        {"action": "approve", "approval_id": body["approval_id"], "confirm": APPROVAL_WORD}
    )
    assert code == 400
    log = (tmp_path / "fleet_approvals.jsonl").read_text().splitlines()
    assert json.loads(log[0])["bots"] == ["bot5"]


def test_start_all_starts_five_then_asks_for_the_rest(tmp_path):
    rt, ctrls = _runtime(tmp_path)
    code, body = rt.act({"action": "start_all"})
    assert code == 409 and sum(c.running for c in ctrls.values()) == 5
    assert [b["name"] for b in body["bots"]] == [f"bot{i}" for i in range(5, 10)]
    rt.act({"action": "approve", "approval_id": body["approval_id"], "confirm": APPROVAL_WORD})
    assert all(c.running for c in ctrls.values())
    code, body = rt.act({"action": "start", "bot": "bot0"})
    assert code == 400  # already running; and never more than 10
    rt.act({"action": "stop_all"})
    assert not any(c.running for c in ctrls.values())


def test_expired_approval_is_refused(tmp_path):
    rt, _ = _runtime(tmp_path)
    for i in range(5):
        rt.act({"action": "start", "bot": f"bot{i}"})
    _, body = rt.act({"action": "start", "bot": "bot6"})
    rt.fleet.pending[body["approval_id"]].expires = 0
    code, res = rt.act(
        {"action": "approve", "approval_id": body["approval_id"], "confirm": APPROVAL_WORD}
    )
    assert code == 400 and "expired" in res["message"]


def test_http_returns_409_for_the_popup(tmp_path):
    from http.server import ThreadingHTTPServer

    rt, _ = _runtime(tmp_path)
    for i in range(5):
        rt.act({"action": "start", "bot": f"bot{i}"})
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(None, None, 15, token="tok", runtime=rt)
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/fleet") as r:
            summary = json.loads(r.read())
        assert summary["fleet"] and summary["running"] == 5 and len(summary["bots"]) == 10
        req = urllib.request.Request(
            base + "/api/control",
            method="POST",
            data=json.dumps({"action": "start", "bot": "bot7"}).encode(),
            headers={"X-Trendbot-Token": "tok"},
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 409 and json.loads(e.value.read())["needs_approval"]
        with urllib.request.urlopen(base + "/api/snapshot?bot=bot3") as r:
            assert json.loads(r.read())["meta"]["bot_name"] == "bot3"
    finally:
        srv.shutdown()
        srv.server_close()
