from typing import Any

from ..calls import CallSession
from ..foundry import Reply
from ..prompts import ANSWER_SCHEMA, support_prompt
from ..settings import Settings


async def answer(
    calls: CallSession, settings: Settings, policies: str, question: str, timestamp: str, ticket: str,
    *, deployment: str | None = None, kind: str = "chat", policies_first: bool = True,
    explicit_supported: bool = True, additional: dict[str, Any] | None = None,
) -> Reply:
    messages, extra = support_prompt(
        policies, question, timestamp, ticket, policies_first=policies_first,
        explicit=explicit_supported and settings.prompt_cache_mode == "explicit", key=settings.prompt_cache_key,
    )
    if additional:
        extra.update(additional)
    body = {"messages": messages, "response_format": ANSWER_SCHEMA, "max_completion_tokens": 600}
    if extra:
        body["extra_body"] = extra
    return await calls.invoke(kind, deployment or settings.frontier_deployment, body)
