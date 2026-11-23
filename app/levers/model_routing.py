from typing import Any

from ..calls import CallSession
from ..foundry import ModelError, Reply
from ..settings import Settings
from .prompt_caching import answer

TIER_QUESTION = {
    "type": "choice",
    "instructions": "Which model tier does this customer request need to be answered correctly?",
    "criteria": {
        "small": "A single, simple question that one policy lookup answers.",
        "frontier": ("Several requests at once, a request to bypass or bend a rule, or a case requiring a "
                     "calculation or step-by-step reasoning."),
    },
}


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ModelError("Decision model returned an invalid probability.")
    return float(value)


async def route(calls: CallSession, cfg: Settings, policies: str, text: str, timestamp: str,
                ticket: str) -> tuple[Reply, dict[str, Any]]:
    lane, mode = calls.context.lane, calls.context.mode
    detail: dict[str, Any] = {"tier": "frontier"}
    deployment, kind = cfg.frontier_deployment, "chat"
    if lane == "candidate":
        if mode == "foundry_router":
            deployment, kind = cfg.router_deployment, "router"
        elif mode == "small_all":
            deployment, detail["tier"] = cfg.small_deployment, "small"
        elif mode == "decision_router":
            if not cfg.decision_deployment:
                raise ModelError("Configure a Decision deployment for this mode.")
            judgment = await calls.invoke("decision", cfg.decision_deployment,
                                          {"state": text, "questions": {"tier": TIER_QUESTION}})
            try:
                tier = judgment.result["tier"]["choice"]
                confidence = probability(judgment.result["tier"]["probabilities"][tier])
            except (KeyError, TypeError):
                raise ModelError("Decision model did not return a routable choice.") from None
            if tier not in {"small", "frontier"}:
                raise ModelError("Decision model returned an unknown tier.")
            chosen = tier == "small" and confidence >= cfg.decision_confidence_min
            deployment, detail["tier"] = (cfg.small_deployment, "small") if chosen else (
                cfg.frontier_deployment, "frontier")
            detail |= {"decision_tier": tier, "confidence": confidence,
                       "classified_tokens": calls.calls[-1]["usage"]["prompt"],
                       "classification_cost_usd": calls.calls[-1]["cost_usd"]}
    # GPT-4.1 rejects explicit breakpoint fields. Use one compatible layout
    # for BOTH lanes and all eligible models in this benchmark.
    reply = await answer(calls, cfg, policies, text, timestamp, ticket, deployment=deployment,
                         kind=kind, explicit_supported=False)
    if kind == "router":
        detail["tier"] = ("small" if reply.model.lower().startswith("gpt-4.1-mini") else
                          "mid" if reply.model.lower().startswith("gpt-4.1") else "frontier")
        for line in calls.calls[-1]["calculation"]:
            if line["label"] == "router fee (all input)":
                detail["routing_fee_usd"] = line["cost_usd"]
    return reply, detail
