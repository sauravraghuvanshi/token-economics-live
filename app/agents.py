"""Foundry generation agents and their lane assignments."""

from dataclasses import dataclass
from typing import Any

from .prompts import ANSWER_SCHEMA, EXTRACT_SCHEMA, responses_format
from .settings import Settings

SUPPORT = "Use the instructions and policy context supplied for the current CloudDesk benchmark request. Reply in English."
INVOICE = "Follow the extraction instructions provided with the current invoice. Write any notes in English."


@dataclass(frozen=True)
class AgentSpec:
    name: str
    deployment: str
    description: str
    schema: dict[str, Any]
    instructions: str
    max_output_tokens: int

    def definition(self) -> dict[str, Any]:
        return {
            "kind": "prompt", "model": self.deployment, "instructions": self.instructions,
            "text": responses_format(self.schema), "max_output_tokens": self.max_output_tokens,
        }


def catalog(cfg: Settings) -> dict[str, AgentSpec]:
    agents = {
        "support-baseline": (cfg.frontier_deployment, "Support without the selected runtime lever."),
        "support-lever": (cfg.frontier_deployment, "Support with the selected lever, using the frontier model."),
        "support-lever-small": (cfg.small_deployment, "Support with the small model after routing, or the small-only test."),
        "support-lever-router": (cfg.router_deployment, "Support through the Foundry model router."),
        "invoice-baseline": (cfg.small_deployment, "Invoice extraction on the retry-until-ceiling path."),
        "invoice-lever": (cfg.small_deployment, "Invoice extraction on the bounded retry or no-retry path."),
    }
    return {
        key: AgentSpec(f"tokecon-{key}", deployment, f"Token Economics Live | {description}",
                       ANSWER_SCHEMA if key.startswith("support") else EXTRACT_SCHEMA,
                       SUPPORT if key.startswith("support") else INVOICE,
                       600 if key.startswith("support") else 700)
        for key, (deployment, description) in agents.items()
    }


def lane_keys(lever: str, mode: str) -> dict[str, tuple[str, ...]]:
    if lever == "retry_killswitch":
        return {"control": ("invoice-baseline",), "candidate": ("invoice-lever",)}
    candidate: tuple[str, ...]
    if lever != "model_routing":
        candidate = ("support-lever",)
    elif mode == "foundry_router":
        candidate = ("support-lever-router",)
    elif mode == "small_all":
        candidate = ("support-lever-small",)
    else:
        candidate = ("support-lever-small", "support-lever")
    return {"control": ("support-baseline",), "candidate": candidate}


def active_version(payload: dict[str, Any], spec: AgentSpec) -> str:
    latest = payload.get("versions", {}).get("latest", {})
    version = latest.get("version")
    definition = latest.get("definition", {})
    if (payload.get("name") != spec.name or payload.get("state") != "enabled"
            or latest.get("status") != "active" or not isinstance(version, str) or not version.isdecimal()
            or any(definition.get(key) != value for key, value in spec.definition().items())):
        raise ValueError(f"{spec.name} has no active version matching the demo configuration.")
    return version
