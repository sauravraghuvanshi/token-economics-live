import re
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
ROUTER_POOLS = {
    "cost": ("gpt-4.1-mini",),
    # Keep hard cases on the baseline's model; cold GPT-4.1 inputs erased routing savings.
    "quality": ("gpt-6.1-sol",),
}
MODEL_VERSION_SUFFIX = re.compile(r"-[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ModelError("Decision model returned an invalid probability.")
    return float(value)


async def classify(calls: CallSession, cfg: Settings, text: str) -> tuple[bool, dict[str, Any]]:
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
    return tier == "small" and confidence >= cfg.decision_confidence_min, {
        "decision_tier": tier, "confidence": confidence,
        "classified_tokens": calls.calls[-1]["usage"]["prompt"],
        "classification_cost_usd": calls.calls[-1]["cost_usd"],
    }


async def route(calls: CallSession, cfg: Settings, policies: str, text: str, timestamp: str,
                ticket: str) -> tuple[Reply, dict[str, Any]]:
    lane, mode = calls.context.lane, calls.context.mode
    detail: dict[str, Any] = {"tier": "frontier"}
    deployment, kind = cfg.frontier_deployment, "chat"
    router_mode = None
    if lane == "candidate":
        if mode == "small_all":
            deployment, detail["tier"] = cfg.small_deployment, "small"
        else:
            small, decision = await classify(calls, cfg, text)
            detail.update(decision)
            if mode == "foundry_router":
                deployment, kind = cfg.router_deployment, "router"
                router_mode = "cost" if small else "quality"
            elif small:
                deployment, detail["tier"] = cfg.small_deployment, "small"
    # Break the shared prefix in both guided lanes. The router does not accept
    # explicit cache controls, even when its pool contains only the frontier model.
    fresh_input = mode == "foundry_router"
    pool = ROUTER_POOLS[router_mode] if router_mode else ()
    controls = {"routing_config": {
        "mode": router_mode, "models": list(pool), "session_affinity": {"strategy": "none"},
    }} if router_mode else None
    reply = await answer(calls, cfg, policies, text, timestamp, ticket, deployment=deployment,
                         kind=kind, policies_first=not fresh_input, cache_prompt=not fresh_input,
                         explicit_supported=False, additional=controls)
    detail["answer_model"] = reply.model
    if router_mode:
        family = MODEL_VERSION_SUFFIX.sub("", reply.model.lower())
        if (reply.routing_mode or "").lower() != router_mode or family not in pool:
            raise ModelError(f"Foundry did not confirm the {router_mode} routing pool. "
                             "Check preview support on the published router agent.")
        detail.update(router_mode=reply.routing_mode, eligible_models=list(pool),
                      tier="small" if router_mode == "cost" else "frontier")
        for line in calls.calls[-1]["calculation"]:
            if line["label"] == "router fee (all input)":
                detail["routing_fee_usd"] = line["cost_usd"]
    return reply, detail
