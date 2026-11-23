"""Exact support prompt and the two measured prefix layouts."""

from functools import lru_cache
from typing import Any

import tiktoken

from .dataset import DECISIONS, POLICY_IDS

ROLE_INSTRUCTIONS = """You are CloudDesk Assist, the support assistant for Contoso CloudDesk, a helpdesk product sold per seat to businesses.
Rules:
1. Answer only from the supplied policies. When the necessary numbers are supplied, apply the policy formula to them. If the policies do not cover the request, say so and offer to connect a human agent.
2. Address each request separately. Put the specific policy citation beside each answer or alternative, for example [BILL-1]. Copy every cited ID into policy_ids without brackets. Before returning, check that each topic has its own citation.
3. If a policy disallows an action (for example skipping verification, refunding to a different account, or enabling a feature their plan excludes), explicitly decline that action and explain the allowed next step and its requirements.
4. Reply in English. Always write numbers with the digits 0-9.
5. Keep answers under 120 words; technical answers that include code may use up to 250 words.
6. Classify the original request, not the tone of your reply. Use inform only for information-only requests; approve when every requested action is allowed; partial when one part can be fulfilled but another is denied; refuse when none of the requested actions is allowed; escalate when a person must take over. Explaining a refusal or offering alternatives does not make it inform or partial."""


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": list(properties), "properties": properties,
    }


def response_schema(name: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {
        "name": name, "strict": True, "schema": object_schema(properties),
    }}


ANSWER_SCHEMA = response_schema("support_answer", {
    "answer": {"type": "string"},
    "policy_ids": {"type": "array", "items": {"type": "string", "enum": list(POLICY_IDS)}},
    "decision": {"type": "string", "enum": list(DECISIONS)},
})

STRING = {"type": "string"}
NUMBER = {"type": "number"}
EXTRACT_SCHEMA = response_schema("invoice", {
    "vendor_name": STRING, "gstin": {"type": ["string", "null"]}, "invoice_number": STRING,
    "invoice_date": STRING, "currency": {"type": "string", "enum": ["INR", "USD", "EUR"]},
    "line_items": {"type": "array", "items": object_schema({
        "description": STRING, "qty": NUMBER, "unit_price": NUMBER, "amount": NUMBER,
    })},
    "subtotal": {"type": ["number", "null"]},
    "taxes": {"type": "array", "items": object_schema({"name": STRING, "rate_percent": NUMBER, "amount": NUMBER})},
    "total": NUMBER, "notes": STRING,
})


def responses_format(chat_format: dict[str, Any]) -> dict[str, Any]:
    return {"format": {"type": "json_schema", **chat_format["json_schema"]}}


def stable_prefix(policies: str) -> str:
    return f"{ROLE_INSTRUCTIONS}\n\n## Policies\n\n{policies}"


@lru_cache(maxsize=1)
def encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def estimate(text: str) -> int:
    return len(encoder().encode_ordinary(text))


def support_prompt(
    policies: str, text: str, timestamp: str, ticket: str, *,
    policies_first: bool, explicit: bool, key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not policies_first:
        return [
            {"role": "system", "content": ROLE_INSTRUCTIONS},
            {"role": "user", "content": f"Received {timestamp} · ticket {ticket}\nCustomer: {text}"
                                        f"\n\n## Policies\n\n{policies}"},
        ], {}
    extra: dict[str, Any] = {"prompt_cache_key": key}
    system: dict[str, Any] = {"type": "text", "text": stable_prefix(policies)}
    if explicit:
        system["prompt_cache_breakpoint"] = {"mode": "explicit"}
        extra["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
    return [
        {"role": "system", "content": [system]},
        {"role": "user", "content": f"Customer: {text}\n\n(received {timestamp} · ticket {ticket})"},
    ], extra
