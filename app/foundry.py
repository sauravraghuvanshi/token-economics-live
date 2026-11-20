"""Foundry v1 clients and retry policy. Requests and transport failures never log credentials."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx
import openai
from azure.core.exceptions import AzureError
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI

from .settings import Settings


@dataclass(frozen=True)
class CallContext:
    lever: str
    mode: str
    lane: str
    request_id: str
    index: int = 0
    attempt: int = 0


@dataclass(frozen=True)
class Reply:
    model: str
    result: Any
    usage: dict[str, Any]
    request_id: str | None = None
    fixture_latency_ms: int | None = None


class ModelClient(Protocol):
    async def invoke(self, kind: str, deployment: str, body: dict[str, Any], ctx: CallContext) -> Reply: ...
    async def close(self) -> None: ...


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, attempts: int = 0):
        super().__init__(message)
        self.status = status
        self.retries = attempts


def retry_delay(headers: httpx.Headers, attempt: int) -> float:
    for key in ("retry-after-ms", "x-ms-retry-after-ms", "retry-after"):
        value = headers.get(key)
        if value is None:
            continue
        try:
            seconds = float(value) / (1000 if key.endswith("-ms") else 1)
            if 0 <= seconds < float("inf"):
                return seconds
        except ValueError:
            if key == "retry-after":
                try:
                    return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
    return 0.5 * 2**attempt


def response_error(status: int) -> str:
    return {
        400: "Foundry rejected a request parameter or the requested model capability.",
        401: "Foundry did not accept the Entra credentials.",
        403: "Foundry denied access. Check the managed identity role and resource networking.",
        404: "Foundry could not find the model deployment or API endpoint.",
        429: "Foundry rate limit reached. Retry after quota is available.",
    }.get(status, f"Foundry returned HTTP {status}.")


async def with_retry(send: Callable[[], Awaitable[Reply]]) -> tuple[Reply, int]:
    for attempt in range(3):
        try:
            async with asyncio.timeout(60):
                return await send(), attempt
        except (openai.APIStatusError, httpx.HTTPStatusError) as exc:
            status = exc.response.status_code
            if attempt < 2 and (status == 429 or 500 <= status < 600):
                await asyncio.sleep(retry_delay(exc.response.headers, attempt))
                continue
            raise ModelError(response_error(status), status, attempt) from None
        except (TimeoutError, openai.APIConnectionError, httpx.RequestError, AzureError):
            raise ModelError("Foundry connection or Entra authentication failed.", attempts=attempt) from None
    raise AssertionError("Retry loop exhausted.")


class LiveClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.credential = DefaultAzureCredential()
        self.token = get_bearer_token_provider(self.credential, settings.azure_token_scope)
        self.http = httpx.AsyncClient(timeout=60, follow_redirects=False)
        self.openai_http = httpx.AsyncClient(timeout=60, follow_redirects=False)
        self.openai = AsyncOpenAI(
            base_url=settings.azure_openai_base_url, api_key=self.token, max_retries=0,
            timeout=60, http_client=self.openai_http,
        )

    async def invoke(self, kind: str, deployment: str, body: dict[str, Any], ctx: CallContext) -> Reply:
        if kind in {"chat", "router"}:
            completion = await self.openai.chat.completions.create(model=deployment, **body)
            value = (completion.choices[0].message.content or "") if completion.choices else ""
            return Reply(completion.model, value, completion.usage.model_dump() if completion.usage else {},
                         completion._request_id)
        if kind == "embedding":
            embedding = await self.openai.embeddings.create(model=deployment, **body)
            return Reply(embedding.model, embedding.data[0].embedding if embedding.data else None,
                         embedding.usage.model_dump() if embedding.usage else {}, embedding._request_id)
        if kind == "decision":
            token = await self.token()
            response = await self.http.post(
                f"{self.settings.foundry_endpoint.rstrip('/')}/providers/microsoft/v1/systemone",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": deployment, **body},
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ModelError("Decision endpoint returned an invalid response.")
            model, usage = result.get("model"), result.get("usage")
            return Reply(model if isinstance(model, str) else "", result.get("answers"),
                         usage if isinstance(usage, dict) else {}, response.headers.get("x-request-id"))
        raise ValueError(f"Unknown Foundry call kind: {kind}")

    async def close(self) -> None:
        await self.openai.close()
        await self.http.aclose()
        await self.credential.close()
