"""Verify an actual deployed SSE stream, measured costs, and repeated demo outcomes."""

import argparse
import getpass
import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path

import httpx

from app.agents import catalog
from app.dataset import WORKLOAD_VERSION, load_dataset
from app.settings import Settings

MODEL_KINDS = {"chat", "router", "embedding", "decision"}
INVOICES = {row.id: row.expected for row in load_dataset().invoices}
DEVANAGARI = re.compile(r"[\u0900-\u097f]")
ATTEMPT_EVENTS = {"attempt_started", "attempt_checked"}


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


def validate_run(client, lever, mode, count, source, check_stories, safety_checks, agents=None, safety_ceiling=6):
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
    if stream[0][1].get("workload_version") != WORKLOAD_VERSION:
        failures.append("The hosted workload is not the expected version.")
    if DEVANAGARI.search(json.dumps(stream, ensure_ascii=False)):
        failures.append("The English demo stream contained Devanagari text.")
    expected_events = ["request_started", "request_started", "lane_result", "lane_result", "kpis"] * count
    if [name for name, _ in stream[1:-1] if name not in ATTEMPT_EVENTS | {"run_error"}] != expected_events:
        failures.append("The stream lost or reordered request, lane, or KPI events.")
    failures.extend(body["message"] for name, body in stream if name == "run_error")
    response_ids = set()
    attempts: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for event, row in stream:
        if event in ATTEMPT_EVENTS:
            attempts.setdefault((row["request_id"], row["lane"]), []).append((event, row["number"]))
        if event != "lane_result":
            continue
        if row["status"] != "ok":
            failures.append(f"{row['request_id']} {row['lane']}: {'; '.join(row['miss_reasons'])}")
        elif any(not call.get("raw_usage") for call in row["calls"]
                 if call["kind"] in MODEL_KINDS):
            failures.append(f"{row['request_id']} {row['lane']}: missing usage.")
        elif row["totals"]["cost_usd"] is None or any(c["cost_usd"] is None for c in row["calls"]):
            failures.append(f"{row['request_id']} {row['lane']}: missing cost.")
        elif Decimal(row["totals"]["cost_usd"]) != sum(Decimal(c["cost_usd"]) for c in row["calls"]):
            failures.append(f"{row['request_id']} {row['lane']}: row cost does not match calls.")
        elif any(value != sum(c["usage"][key] for c in row["calls"]) for key, value in row["totals"]["tokens"].items()):
            failures.append(f"{row['request_id']} {row['lane']}: row tokens do not match call usage.")
        if source == "live":
            for call in row["calls"]:
                if call["kind"] not in {"chat", "router"}:
                    continue
                agent = call.get("foundry_agent")
                version = call.get("foundry_agent_version")
                response_id = call.get("foundry_response_id")
                if (agent not in (agents or {}).get(row["lane"], []) or not version
                        or not response_id or response_id in response_ids):
                    failures.append(f"{row['request_id']} {row['lane']}: unverified Foundry agent execution.")
                response_ids.add(response_id)
        if lever == "retry_killswitch":
            chain = row["lever_detail"].get("attempts", [])
            expected_progress = [(name, step["number"]) for step in chain
                                 for name in ("attempt_started", "attempt_checked")]
            if not chain or attempts.pop((row["request_id"], row["lane"]), []) != expected_progress:
                failures.append(f"{row['request_id']} {row['lane']}: missing or out-of-order attempt progress.")
        if lever == "retry_killswitch" and row["pass"]:
            expected = INVOICES[row["request_id"]]
            output = row["lever_detail"].get("output") or {}
            if (output.get("currency") != expected.currency or output.get("total") != expected.total
                    or output.get("gstin") != expected.gstin):
                failures.append(f"{row['request_id']} {row['lane']}: accepted invoice contains incorrect fields.")
    for lane in ("control", "candidate"):
        results = [r for r in rows if r["lane"] == lane]
        if [r["idx"] for r in results] != list(range(1, count + 1)):
            failures.append(f"{lane} lost or reordered a lane event.")
        else:
            final = summary[lane]
            if final["accepted"] != sum(r["pass"] for r in results):
                failures.append(f"{lane} accepted count does not match the row grades.")
            if final["calls"] != sum(c["kind"] in MODEL_KINDS for r in results for c in r["calls"]):
                failures.append(f"{lane} paid call count does not match rows.")
            if any(value != sum(r["totals"]["tokens"][key] for r in results)
                   for key, value in final["tokens"].items()):
                failures.append(f"{lane} total tokens do not match rows.")
            if summary["complete"] and all(r["totals"]["cost_usd"] is not None for r in results):
                if Decimal(final["cost_usd"]) != sum(Decimal(r["totals"]["cost_usd"]) for r in results):
                    failures.append(f"{lane} total does not match rows.")
    if not summary["complete"] or summary.get("baseline_stop_requested") or attempts:
        failures.append("The run has incomplete totals, an unexpected stop, or late attempt events.")
    signal = {
        f"{r['request_id']}/{r['lane']}": {
            "accepted": r["pass"], "buckets": r["totals"]["tokens"], "model": [
                c.get("response_model") for c in r["calls"] if c.get("response_model")],
            "agents": [c["foundry_agent"] for c in r["calls"] if c.get("foundry_agent")],
            "agent_versions": [c["foundry_agent_version"] for c in r["calls"] if c.get("foundry_agent_version")],
            "response_ids": [c["foundry_response_id"] for c in r["calls"] if c.get("foundry_response_id")],
            "generation_calls": sum(c["kind"] in {"chat", "router"} for c in r["calls"]),
            "cache_action": r["lever_detail"].get("cache_action"), "wrong_hit": r["lever_detail"].get("wrong_hit"),
            "tier": r["lever_detail"].get("tier"), "similarity": r["lever_detail"].get("similarity"),
            "routing_fee_usd": r["lever_detail"].get("routing_fee_usd"),
            "router_mode": r["lever_detail"].get("router_mode"),
            "answer_model": r["lever_detail"].get("answer_model"),
            "classification_cost_usd": r["lever_detail"].get("classification_cost_usd"),
            "stop": r["lever_detail"].get("stop"), "attempts": len(r["lever_detail"].get("attempts", [])),
            "repeated_attempts": sum(a["repeated"] for a in r["lever_detail"].get("attempts", [])),
            "arithmetic_reconciliations": [a["arithmetic_reconciliation"]
                                           for a in r["lever_detail"].get("attempts", [])
                                           if "arithmetic_reconciliation" in a],
            "subtotals_from_ocr": [a["subtotal_from_ocr"] for a in r["lever_detail"].get("attempts", [])
                                   if "subtotal_from_ocr" in a],
            "cost_usd": r["totals"]["cost_usd"], "miss_reasons": r["miss_reasons"],
        } for r in rows
    }

    def see(key, condition, explanation):
        if key in signal and not condition(signal[key]):
            failures.append(explanation)

    if check_stories:
        control, candidate_summary = summary["control"], summary["candidate"]
        candidate = [r for r in rows if r["lane"] == "candidate"]
        if (lever == "response_cache" or mode == "foundry_router") and any(
            lane["tokens"][key] for lane in (control, candidate_summary) for key in ("cached", "cache_write")
        ):
            failures.append("Prompt-cache reads or writes contaminated the comparison.")
        if lever == "response_cache":
            if control["model_calls"] != count or control["cache_hits"] or candidate_summary["model_calls"] >= count:
                failures.append("Only the candidate should skip generation on cache hits.")
            if summary["complete"] and Decimal(candidate_summary["cost_usd"]) >= Decimal(control["cost_usd"]):
                failures.append("The answer cache did not lower measured total inference cost.")
            if any(r["lever_detail"].get("cache_action") == "hit"
                   and any(c["kind"] in {"chat", "router"} for c in r["calls"]) for r in candidate):
                failures.append("A saved answer still triggered generation.")
        if lever != "retry_killswitch" and control["accepted"] != count:
            failures.append("The support baseline did not accept every request.")
        if mode in {"default", "foundry_router", "decision_router", "fact_guarded"}:
            if candidate_summary["accepted"] != count:
                failures.append("The recommended support path did not accept every request.")
            if (source == "live" and (count == 10 or mode == "foundry_router") and summary["complete"]
                    and candidate_summary["cpa_usd"] is not None and control["cpa_usd"] is not None
                    and Decimal(candidate_summary["cpa_usd"]) >= Decimal(control["cpa_usd"])):
                failures.append("The recommended support path did not lower cost per accepted request.")
        if lever == "prompt_caching":
            if summary["candidate"]["tokens"]["cached"] <= summary["control"]["tokens"]["cached"]:
                failures.append("Candidate did not read more cached tokens than the control.")
        elif mode == "foundry_router":
            for r in candidate:
                detail, calls = r["lever_detail"], r["calls"]
                if ([c["kind"] for c in calls] != ["decision", "router"]
                        or not detail.get("routing_fee_usd") or not detail.get("classification_cost_usd")
                        or calls[1].get("routing_mode") != detail.get("router_mode")
                        or not calls[1].get("response_model") or calls[1]["response_model"] != detail.get("answer_model")):
                    failures.append(f"{r['request_id']}: expected a billed classifier and confirmed routed answer.")
            models = {re.sub(r"-\d{4}-\d{2}-\d{2}$", "", c["response_model"].lower())
                      for r in candidate for c in r["calls"] if c["kind"] == "router" and c.get("response_model")}
            if len(models) < 2 or {r["lever_detail"].get("router_mode") for r in candidate} != {"cost", "quality"}:
                failures.append("The answer workload did not use both routing pools and at least two model families.")
        elif mode == "decision_router":
            for key in ("mr-03/candidate", "mr-05/candidate", "mr-07/candidate", "mr-09/candidate"):
                see(key, lambda r: r["tier"] == "frontier", f"Decision router did not escalate {key}.")
            if not any(r["tier"] == "small" for k, r in signal.items() if k.endswith("/candidate")):
                failures.append("The decision router did not send any request to the small model.")
        elif mode == "small_all":
            if any(r["lever_detail"].get("tier") != "small" for r in candidate):
                failures.append("Small-only mode used a different tier.")
            if (summary["complete"] and Decimal(candidate_summary["cost_usd"]) >= Decimal(control["cost_usd"])):
                failures.append("Small-only mode did not lower total inference cost.")
        elif mode == "fact_guarded":
            for key in ("rc-02/candidate", "rc-05/candidate", "rc-08/candidate", "rc-09/candidate"):
                see(key, lambda r: r["cache_action"] == "hit" and r["accepted"] and not r["generation_calls"],
                    f"Expected a correct hit with no generation: {key}")
            for key in ("rc-03/candidate", "rc-06/candidate"):
                see(key, lambda r: r["cache_action"] == "store" and r["accepted"], f"Expected a correct miss: {key}")
            see("rc-10/candidate", lambda r: r["cache_action"] == "bypass", "Multi-intent request did not bypass.")
        elif mode == "naive":
            for key in ("rc-03/candidate", "rc-06/candidate"):
                see(key, lambda r: r["wrong_hit"], f"Expected a wrong similarity hit: {key}")
            if candidate_summary["accepted"] != count - (1 if count < 6 else 2):
                failures.append("Naive similarity should miss only the changed-fact samples in this batch.")
        elif lever == "retry_killswitch":
            stop, attempts_at_stop = {"guardrails": ("retry limit", 4), "no_retries": ("no retries", 1),
                                      "tight": ("kill switch", 1)}[mode]
            for request_id in ("inv-03", "inv-06"):
                see(f"{request_id}/control", lambda r: r["stop"] == "safety ceiling" and r["attempts"] == safety_ceiling,
                    f"{request_id} control did not reach the safety ceiling.")
                see(f"{request_id}/candidate", lambda r: r["stop"] == stop and not r["accepted"]
                    and r["attempts"] == attempts_at_stop,
                    f"{request_id} candidate did not hand off after {attempts_at_stop} attempt(s) with {mode}.")
            for request_id in ("inv-01", "inv-04"):
                for lane in ("control", "candidate"):
                    see(f"{request_id}/{lane}", lambda r: r["accepted"] and r["attempts"] == 1,
                        f"{request_id} should be accepted on the first attempt in {lane}.")
            for request_id in ("inv-02", "inv-05"):
                see(f"{request_id}/control", lambda r: r["accepted"] and r["attempts"] == 2,
                    f"{request_id} did not recover on the first retry in the baseline.")
                see(f"{request_id}/candidate", lambda r: r["accepted"] == (mode == "guardrails")
                    and r["attempts"] == (2 if mode == "guardrails" else 1),
                    f"{request_id} did not demonstrate the retry trade-off.")
    elapsed = time.monotonic() - begin
    if safety_checks and count == 10 and source == "live" and elapsed >= 90:
        failures.append("Ten-request run exceeded the 90-second dry-run target.")
    return {"workload_version": stream[0][1].get("workload_version"), "source": source,
            "lever": lever, "mode": mode, "run_id": run_id, "count": count, "summary": summary,
            "elapsed_seconds": round(elapsed, 2), "signals": signal, "failures": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--code", help="Prefer DEMO_ACCESS_CODE or the hidden prompt.")
    parser.add_argument("--rounds", default=1, type=int)
    parser.add_argument("--max-counts", action="store_true")
    parser.add_argument("--source", default="live", choices=("live", "fake"))
    parser.add_argument("--assert-stories", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
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
        if config.get("workload_version") != WORKLOAD_VERSION:
            raise SystemExit("The app is not serving the expected workload release.")
        expected = set(config["deployments"].values())
        expected_agents = {spec.name for spec in catalog(Settings()).values()}
        if opts.source == "live" and (
            len(expected) != 5 or set(status["deployments"]) != expected
            or set(status.get("agents", {})) != expected_agents
            or not status["prices_verified"] or status["missing_prices"]
        ):
            raise SystemExit("Live smoke requires five responding deployments, six active agents and verified prices.")
        expected_modes = {"default", "foundry_router", "decision_router", "small_all", "fact_guarded", "naive",
                          "guardrails", "no_retries", "tight"}
        configured_modes = {mode for tab in config["tabs"].values() for mode in tab["modes"]}
        if not opts.mode and opts.assert_stories and configured_modes != expected_modes:
            raise SystemExit(f"Full story verification requires all nine modes. Found {sorted(configured_modes)}.")
        for i in range(opts.rounds):
            for lever, tab in config["tabs"].items():
                for mode in tab["modes"]:
                    if opts.mode and mode != opts.mode:
                        continue
                    count = tab["max" if opts.max_counts else "default"]
                    result = validate_run(client, lever, mode, count, opts.source, opts.assert_stories, True,
                                          tab.get("agents", {}).get(mode), config["retry_attempts"]["control"])
                    result["round"] = i + 1
                    if opts.verify_replays and not result["failures"]:
                        replay = client.post(f"/api/replays/{lever}/{mode}", params={"speed": 2})
                        replay.raise_for_status()
                        data = list(events(client, replay.json()["run_id"]))
                        if (data[0][1]["source"] != "replay" or data[0][1]["recording_source"] != opts.source
                                or data[-1][0] != "run_completed" or data[-1][1]["summary"] != result["summary"]):
                            result["failures"].append("Replay differed from the recorded run.")
                    records.append(result)
                    if opts.output:
                        opts.output.parent.mkdir(exist_ok=True, parents=True)
                        opts.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
                    c, a = result["summary"]["control"], result["summary"]["candidate"]
                    print(f"{i + 1} {lever}/{mode} | {result['elapsed_seconds']}s | "
                          f"accepted {c['accepted']}/{count} -> {a['accepted']}/{count} | "
                          f"{len(result['failures'])} validation issue(s)", flush=True)
                    for problem in result["failures"]:
                        print(f"  {problem}", flush=True)
                    if opts.fail_fast and result["failures"]:
                        raise SystemExit(1)
    if not records or any(r["failures"] for r in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
