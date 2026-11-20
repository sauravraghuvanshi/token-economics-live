"""Exact support prompt and the two measured prefix layouts."""

from functools import lru_cache
from typing import Any

import tiktoken

from .dataset import DECISIONS, POLICY_IDS

ROLE_INSTRUCTIONS = """You are CloudDesk Assist, the support assistant for Contoso CloudDesk, a helpdesk product sold per seat to businesses.
Rules:
1. Answer only from the policies below. If they do not cover the request, say so and offer to connect a human agent.
2. Cite every policy you rely on in the policy_ids field and in the answer text, in square brackets, for example [BILL-1].
3. If the customer asks for something a policy does not allow (for example skipping verification, refunding to a different account, or a feature their plan does not include), refuse that part politely and say what they can do instead.
4. Reply in the customer's language. Always write numbers with the digits 0-9.
5. Keep answers under 120 words; technical answers that include code may use up to 250 words.
6. Set decision to: inform (information only), approve (every requested action is allowed), partial (some parts allowed, some not), refuse (the main request is not allowed), or escalate (a human must handle it)."""


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
