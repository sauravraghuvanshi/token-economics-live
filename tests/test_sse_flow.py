import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.guards import SpendLimit, SpendWindow
from app.main import create_app
from app.settings import Settings

MODES = [
    ("prompt_caching", "default"),
    ("model_routing", "foundry_router"),
    ("model_routing", "decision_router"),
    ("model_routing", "small_all"),
    ("response_cache", "fact_guarded"),
    ("response_cache", "naive"),
    ("retry_killswitch", "guardrails"),
    ("retry_killswitch", "no_retries"),
    ("retry_killswitch", "tight"),
]


def decode(text):
    events = []
    for frame in text.strip().split("\n\n"):
        if frame.startswith(":"):
            continue
        _, name, data = frame.splitlines()
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest.fixture
def api(tmp_path):
    settings = Settings(foundry_mode="fake", data_dir=tmp_path, demo_access_code="fixture-code",
                        tight_kill_switch_usd=Decimal("0.0007"))
    with TestClient(create_app(settings)) as client:
        yield client


def start(api, lever, mode, count):
    body = {"lever": lever, "mode": mode, "count": count}
    response = api.post("/api/runs", json=body, headers={"X-Demo-Code": "fixture-code"})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def get_events(api, run_id):
    response = api.get(f"/api/runs/{run_id}/events")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    return decode(response.text)


def by_lane(events, lane):
    return [result for name, result in events if name == "lane_result" and result["lane"] == lane]


@pytest.mark.parametrize(("lever", "mode"), MODES)
def test_two_completed_runs_of_every_mode_and_replay(api, lever, mode):
    count = 6 if lever == "retry_killswitch" else 10
    previous = None
    for _ in range(2):
        run_id = start(api, lever, mode, count)
        events = get_events(api, run_id)
        assert events[0][0] == "run_started" and events[0][1]["source"] == "fake"
        assert events[-1][0] == "run_completed" and events[-1][1]["saved_replay"]
        assert [name for name, _ in events[1:-1]] == [
            "request_started", "request_started", "lane_result", "lane_result", "kpis",
        ] * count
        for lane in ("control", "candidate"):
            results = by_lane(events, lane)
            assert [r["idx"] for r in results] == list(range(1, count + 1))
            summary = events[-1][1]["summary"][lane]
            assert summary["accepted"] == sum(r["pass"] for r in results)
            assert Decimal(summary["cost_usd"]) == sum(Decimal(r["totals"]["cost_usd"]) for r in results)
            assert all(r["status"] == "ok" for r in results)
            for row in results:
                assert Decimal(row["totals"]["cost_usd"]) == sum(Decimal(c["cost_usd"]) for c in row["calls"])
        key_results = [((row["request_id"], row["lane"]), (row["pass"], row["totals"]))
                       for name, row in events if name == "lane_result"]
        stable = dict(key_results)
        if previous is not None:
            assert previous == stable
        previous = stable

    replay = api.post(f"/api/replays/{lever}/{mode}?speed=2")
    assert replay.status_code == 202
    recorded = get_events(api, replay.json()["run_id"])
    assert recorded[0][1]["source"] == "replay" and recorded[0][1]["recording_source"] == "fake"
    assert recorded[-1][1]["summary"] == events[-1][1]["summary"]
    board = api.get("/api/scoreboard").json()["runs"]
    assert board[0]["mode"] == mode and board[0]["source"] == "replay"

    candidate = by_lane(events, "candidate")
    control = by_lane(events, "control")
    if lever == "prompt_caching":
        assert candidate[0]["totals"]["tokens"]["cache_write"] > 0
        assert all(r["totals"]["tokens"]["cached"] > 0 for r in candidate[1:])
        assert not any(r["totals"]["tokens"]["cached"] for r in control)
    elif mode == "foundry_router":
        assert all(row["lever_detail"]["routing_fee_usd"] for row in candidate)
        assert {row["lever_detail"]["tier"] for row in candidate} == {"small", "frontier"}
    elif mode == "decision_router":
        assert candidate[2]["lever_detail"]["tier"] == "frontier"
        assert candidate[0]["lever_detail"]["tier"] == "small"
        assert len(candidate[0]["calls"][0]["sent"]["state"]) < 150
    elif mode == "small_all":
        assert control[2]["pass"] and not candidate[2]["pass"] and not candidate[4]["pass"]
    elif mode == "fact_guarded":
        assert all(row["pass"] for row in candidate)
        assert [row["request_id"] for row in candidate if not row["calls"]] == ["rc-02", "rc-05", "rc-08"]
        assert candidate[-1]["lever_detail"]["cache_action"] == "bypass"
    elif mode == "naive":
        assert {row["request_id"] for row in candidate if row["lever_detail"].get("wrong_hit")} >= {"rc-03", "rc-06"}
        assert all(row["calls"][0]["kind"] == "embedding" for row in candidate)
    else:
        assert len(control[2]["lever_detail"]["attempts"]) == 6
        assert control[2]["lever_detail"]["outcome"] == "safety ceiling"
        assert len(control[1]["lever_detail"]["attempts"]) == 2 and control[1]["pass"]
        assert candidate[2]["lever_detail"]["outcome"] == "handoff"
        if mode == "guardrails":
            assert len(candidate[2]["lever_detail"]["attempts"]) == 2
            assert candidate[2]["lever_detail"]["stop"] == "no new evidence"
            assert candidate[1]["pass"] and candidate[4]["pass"]
            extracts = [call["sent"]["messages"][1]["content"] for call in candidate[4]["calls"]
                        if call["kind"] == "chat"]
            assert "Validator diagnostics" not in extracts[0]
            assert "quantity 1 times unit price 6000 equals 6000.00, not the extracted amount 9000." in extracts[1]
            review, = [call for call in candidate[4]["calls"] if call["kind"] == "decision"]
            reconciliation = review["sent"]["state"]["validator_arithmetic"]
            assert [Decimal(amount) for amount in reconciliation["line_amounts_from_qty_times_unit_price"]] == [
                Decimal("42000"), Decimal("6000"),
            ]
            assert Decimal(reconciliation["total_from_subtotal_plus_tax"]) == Decimal("56640")
        else:
            assert len(candidate[2]["lever_detail"]["attempts"]) == 1
            assert not candidate[1]["pass"]


def test_access_single_active_resume_and_follow_up(api):
    assert api.get("/healthz").text == "ok"
    assert api.get("/api/status").json()["mode"] == "fake"
    body = {"lever": "response_cache", "mode": "fact_guarded", "count": 5}
    assert api.post("/api/runs", json=body).status_code == 401
    assert api.post("/api/cache/clear").status_code == 401
    assert api.post("/api/cache/ask", json={"question": "When?", "mode": "fact_guarded"}).status_code == 401
    active = start(api, **body)
    second = api.post("/api/runs", json=body, headers={"X-Demo-Code": "fixture-code"})
    assert second.status_code == 409
    first = get_events(api, active)
    second = api.get(f"/api/runs/{active}/events", headers={"Last-Event-ID": "4"})
    assert decode(second.text) == first[4:]
    ask = api.post("/api/cache/ask", json={
        "question": "Could you clarify when an approved card refund will arrive?", "mode": "fact_guarded",
    }, headers={"X-Demo-Code": "fixture-code"}).json()
    assert ask["cache_action"] == "hit" and ask["cost_usd"] == "0" and not ask["graded"]
    assert api.post("/api/cache/clear", headers={"X-Demo-Code": "fixture-code"}).json()["cleared"]


def test_rolling_spend_survives_restart(tmp_path):
    spend = SpendWindow(tmp_path / "spent.json", Decimal("0.01"))
    spend.check()
    spend.record(Decimal("0.01"))
    with pytest.raises(SpendLimit, match="Hourly demo spend"):
        SpendWindow(spend.path, spend.limit).check()


def test_replay_only_plays_saved_fixture_without_initializing_models(api, tmp_path, monkeypatch):
    start_id = start(api, "prompt_caching", "default", 5)
    original = get_events(api, start_id)[-1][1]["summary"]
    response = api.get("/api/replays/prompt_caching/default/recording", headers={"X-Demo-Code": "fixture-code"})
    assert response.status_code == 200 and response.json()["source"] == "fake"
    # Keep the synthetic test recording out of the committed live replay directory.
    (tmp_path / "prompt_caching-default.json").write_text(response.text, encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay-only initialized a model client")

    monkeypatch.setattr("app.main.LiveClient", forbidden)
    monkeypatch.setattr("app.main.FakeClient", forbidden)
    with TestClient(create_app(Settings(replay_only=True, data_dir=tmp_path, replay_dir=tmp_path))) as offline:
        assert offline.get("/api/status").json()["mode"] == "replay-only"
        body = {"lever": "prompt_caching", "mode": "default", "count": 5}
        assert offline.post("/api/runs", json=body).status_code == 503
        replay = offline.post("/api/replays/prompt_caching/default?speed=2")
        assert replay.status_code == 202
        played = get_events(offline, replay.json()["run_id"])
        assert played[0][1]["source"] == "replay"
        assert played[0][1]["recording_source"] == "fake"
        assert played[-1][1]["summary"] == original
