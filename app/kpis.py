"""Aggregate exact row costs. Incomplete runs never report a percentage or CPA."""

from decimal import Decimal
from typing import Any

from .pricing import percentage, usd
from .usage import Usage

D = Decimal


def lane_kpis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = Usage().json()
    call_count, accepted, wasted, latency, hits = 0, 0, 0, 0, 0
    priced = True
    total = D(0)
    tiers: dict[str, int] = {}
    for row in rows:
        value = row["totals"]["cost_usd"]
        if value is None:
            priced = False
        else:
            total += D(value)
        accepted += int(row["pass"])
        latency += row["totals"]["latency_ms"]
        call_count += sum(call["kind"] in {"chat", "router", "embedding", "decision"} for call in row["calls"])
        detail = row["lever_detail"]
        wasted += detail.get("wasted_tokens", 0)
        hits += int(detail.get("cache_action") == "hit")
        if tier := detail.get("tier"):
            tiers[tier] = tiers.get(tier, 0) + 1
        for key in tokens:
            tokens[key] += row["totals"]["tokens"][key]
    cpa = total / accepted if priced and accepted else None
    return {
        "done": len(rows), "accepted": accepted, "cost_usd": usd(total) if priced else None,
        "priced_subtotal_usd": usd(total), "cpa_usd": usd(cpa), "complete": priced, "calls": call_count,
        "tokens": tokens, "cached_share_pct": percentage(tokens["cached"], tokens["prompt"]),
        "wasted_token_share_pct": percentage(wasted, tokens["prompt"] + tokens["completion"]),
        "model_calls": sum(call["kind"] in {"chat", "router"} for row in rows for call in row["calls"]),
        "cache_hits": hits, "route_mix": tiers, "avg_latency_ms": round(latency / len(rows)) if rows else None,
    }


def kpis(results: list[dict[str, Any]]) -> dict[str, Any]:
    control, candidate = [lane_kpis([r for r in results if r["lane"] == lane])
                          for lane in ("control", "candidate")]
    complete = control["complete"] and candidate["complete"]
    savings, change, quality = None, None, None
    if control["done"] == candidate["done"] > 0:
        quality = candidate["accepted"] >= control["accepted"]
        if complete:
            a, b = D(control["cost_usd"]), D(candidate["cost_usd"])
            savings = percentage(a - b, a)
            if control["cpa_usd"] is not None and candidate["cpa_usd"] is not None:
                ac, bc = D(control["cpa_usd"]), D(candidate["cpa_usd"])
                change = percentage(bc - ac, ac)
    return {
        "control": control, "candidate": candidate, "savings_pct": savings, "cpa_change_pct": change,
        "complete": complete, "signal": {"quality_holds": quality,
                                         "keep": quality and D(change) < 0 if change is not None else None},
    }
