"""Verify an actual deployed SSE stream, measured costs, and repeated demo outcomes."""

import argparse
import getpass
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import httpx


def events(client, run_id):
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        response.raise_for_status()
        if not response.headers["content-type"].startswith("text/event-stream"):
            raise RuntimeError("The events endpoint did not return SSE.")
        name = ""
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                yield name, json.loads(line[6:])


def validate_run(client, lever, mode, count, source, check_stories, safety_checks):
    begin = time.monotonic()
    start = client.post("/api/runs", json={"lever": lever, "mode": mode, "count": count})
    start.raise_for_status()
    run_id = start.json()["run_id"]
    stream = list(events(client, run_id))
    if not stream or stream[0][0] != "run_started" or stream[-1][0] != "run_completed":
        raise RuntimeError("Foundry lane event stream did not complete.")
    if stream[0][1]["source"] != source:
        raise RuntimeError(f"Expected {source} data but received {stream[0][1]['source']}.")
    summary = stream[-1][1]["summary"]
    rows = [r for event, r in stream if event == "lane_result"]
    failures: list[str] = []
    expected_events = ["request_started", "request_started", "lane_result", "lane_result", "kpis"] * count
    if [name for name, _ in stream[1:-1] if name != "run_error"] != expected_events:
        failures.append("The stream lost or reordered request, lane, or KPI events.")
    failures.extend(body["message"] for name, body in stream if name == "run_error")
    for row in rows:
        if row["status"] != "ok":
            failures.append(f"{row['request_id']} {row['lane']}: {'; '.join(row['miss_reasons'])}")
        elif any(not call.get("raw_usage") for call in row["calls"]
                 if call["kind"] in {"chat", "router", "embedding", "decision"}):
            failures.append(f"{row['request_id']} {row['lane']}: missing usage.")
        elif row["totals"]["cost_usd"] is None or any(c["cost_usd"] is None for c in row["calls"]):
            failures.append(f"{row['request_id']} {row['lane']}: missing cost.")
        elif Decimal(row["totals"]["cost_usd"]) != sum(Decimal(c["cost_usd"]) for c in row["calls"]):
            failures.append(f"{row['request_id']} {row['lane']}: row cost does not match calls.")
    for lane in ("control", "candidate"):
        results = [r for r in rows if r["lane"] == lane]
        if [r["idx"] for r in results] != list(range(1, count + 1)):
            failures.append(f"{lane} lost or reordered a lane event.")
        else:
            if summary[lane]["accepted"] != sum(r["pass"] for r in results):
                failures.append(f"{lane} accepted count does not match the row grades.")
            if summary["complete"] and all(r["totals"]["cost_usd"] is not None for r in results):
                if Decimal(summary[lane]["cost_usd"]) != sum(Decimal(r["totals"]["cost_usd"]) for r in results):
                    failures.append(f"{lane} total does not match rows.")
    if not summary["complete"]:
        failures.append("The run has incomplete totals.")
    signal = {f"{r['request_id']}/{r['lane']}": {
        "accepted": r["pass"], "buckets": r["totals"]["tokens"], "model": [
            c.get("response_model") for c in r["calls"] if c.get("response_model")],
        "cache_action": r["lever_detail"].get("cache_action"), "wrong_hit": r["lever_detail"].get("wrong_hit"),
        "tier": r["lever_detail"].get("tier"), "similarity": r["lever_detail"].get("similarity"),
        "routing_fee_usd": r["lever_detail"].get("routing_fee_usd"),
        "stop": r["lever_detail"].get("stop"), "attempts": len(r["lever_detail"].get("attempts", [])),
        "cost_usd": r["totals"]["cost_usd"], "miss_reasons": r["miss_reasons"],
    } for r in rows}

    def see(key, condition, explanation):
        if key in signal and not condition(signal[key]):
            failures.append(explanation)

    if check_stories:
        candidate = [r for r in rows if r["lane"] == "candidate"]
        if lever == "prompt_caching":
            if summary["candidate"]["tokens"]["cached"] <= summary["control"]["tokens"]["cached"]:
                failures.append("Candidate did not read more cached tokens than the control.")
        elif mode == "foundry_router":
            if any(not r["lever_detail"].get("routing_fee_usd") or len(r["calls"]) != 1
                   or r["calls"][0]["kind"] != "router" or not r["calls"][0]["response_model"]
                   for r in candidate):
                failures.append("Foundry routing did not show a billed router call and the model that answered.")
        elif mode == "decision_router":
            for key in ("mr-03/candidate", "mr-05/candidate"):
                see(key, lambda r: r["tier"] == "frontier", f"Decision router did not escalate {key}.")
            if not any(r["tier"] == "small" for k, r in signal.items() if k.endswith("/candidate")):
                failures.append("The decision router did not send any request to the small model.")
        elif mode == "small_all":
            if any(r["lever_detail"].get("tier") != "small" for r in candidate):
                failures.append("Small-only mode used a different tier.")
        elif mode == "fact_guarded":
            for key in ("rc-02/candidate", "rc-05/candidate", "rc-08/candidate"):
                see(key, lambda r: r["cache_action"] == "hit" and r["accepted"], f"Expected a correct hit: {key}")
            for key in ("rc-03/candidate", "rc-06/candidate", "rc-09/candidate"):
                see(key, lambda r: r["cache_action"] == "store" and r["accepted"], f"Expected a correct miss: {key}")
            see("rc-10/candidate", lambda r: r["cache_action"] == "bypass", "Multi-intent request did not bypass.")
        elif mode == "naive":
            for key in ("rc-03/candidate", "rc-06/candidate"):
                see(key, lambda r: r["wrong_hit"], f"Expected a wrong similarity hit: {key}")
        elif lever == "retry_killswitch":
            expect = {"guardrails": "no new evidence", "no_retries": "no retries", "tight": "kill switch"}[mode]
            for request_id in ("inv-03", "inv-06"):
                see(f"{request_id}/control", lambda r: r["stop"] == "safety ceiling",
                    f"{request_id} control did not reach the safety ceiling.")
                see(f"{request_id}/candidate", lambda r: r["stop"] == expect,
                    f"{request_id} candidate did not stop on {expect}.")
            for request_id in ("inv-01", "inv-04"):
                see(f"{request_id}/candidate", lambda r: r["accepted"], f"{request_id} should be accepted.")
            if mode != "tight":
                for request_id in ("inv-02", "inv-05"):
                    see(f"{request_id}/candidate", lambda r: r["accepted"] == (mode == "guardrails"),
                        f"{request_id} did not demonstrate the retry trade-off.")
    elapsed = time.monotonic() - begin
    if safety_checks and count == 10 and source == "live" and elapsed >= 90:
        failures.append("Ten-request run exceeded the 90-second dry-run target.")
    return {"lever": lever, "mode": mode, "run_id": run_id, "count": count, "summary": summary,
            "elapsed_seconds": round(elapsed, 2), "signals": signal, "failures": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--code", help="Prefer DEMO_ACCESS_CODE or the hidden prompt.")
    parser.add_argument("--rounds", default=1, type=int)
    parser.add_argument("--max-counts", action="store_true")
    parser.add_argument("--source", default="live", choices=("live", "fake"))
    parser.add_argument("--assert-stories", action="store_true")
    parser.add_argument("--verify-replays", action="store_true")
    parser.add_argument("--mode", help="Run just this mode.")
    parser.add_argument("--output", type=Path)
    opts = parser.parse_args()
    if opts.rounds < 1:
        parser.error("--rounds must be at least one.")
    code = opts.code or os.getenv("DEMO_ACCESS_CODE")
    if not code and opts.source == "live":
        code = getpass.getpass("Demo access code: ")
    records = []
    with httpx.Client(base_url=opts.base_url, headers={"X-Demo-Code": code} if code else None,
                      timeout=httpx.Timeout(45)) as client:
        health = client.get("/healthz")
        health.raise_for_status()
        if health.text != "ok":
            raise SystemExit("Unexpected health response.")
        for _ in range(20):
            status = client.get("/api/status").raise_for_status().json()
            if status["mode"] == opts.source and (opts.source == "fake" or status["foundry"] == "ok"):
                break
            time.sleep(2)
        else:
            raise SystemExit(status["detail"])
        config = client.get("/api/config").raise_for_status().json()
        expected = set(config["deployments"].values())
        if opts.source == "live" and (
            len(expected) != 5 or set(status["deployments"]) != expected
            or not status["prices_verified"] or status["missing_prices"]
        ):
            raise SystemExit("Live smoke requires five responding deployments and all prices verified.")
        if not opts.mode and "tight" not in config["tabs"]["retry_killswitch"]["modes"]:
            print("Tight mode is disabled; set TIGHT_KILL_SWITCH_USD after measuring the guardrails run.")
        for i in range(opts.rounds):
            for lever, tab in config["tabs"].items():
                for mode in tab["modes"]:
                    if opts.mode and mode != opts.mode:
                        continue
                    count = tab["max" if opts.max_counts else "default"]
                    result = validate_run(client, lever, mode, count, opts.source, opts.assert_stories, True)
                    result["round"] = i + 1
                    if opts.verify_replays and result["summary"]["complete"]:
                        replay = client.post(f"/api/replays/{lever}/{mode}", params={"speed": 2})
                        replay.raise_for_status()
                        data = list(events(client, replay.json()["run_id"]))
                        if (data[0][1]["source"] != "replay" or data[0][1]["recording_source"] != opts.source
                                or data[-1][0] != "run_completed" or data[-1][1]["summary"] != result["summary"]):
                            result["failures"].append("Replay differed from the recorded run.")
                    records.append(result)
                    c, a = result["summary"]["control"], result["summary"]["candidate"]
                    print(f"{i + 1} {lever}/{mode} · {result['elapsed_seconds']}s · "
                          f"accepted {c['accepted']}/{count} → {a['accepted']}/{count} · "
                          f"{len(result['failures'])} validation issue(s)", flush=True)
                    for problem in result["failures"]:
                        print(f"  {problem}", flush=True)
    if opts.output:
        opts.output.parent.mkdir(exist_ok=True, parents=True)
        opts.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not records or any(r["failures"] for r in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
