"""Foundry v1 clients and retry policy. Requests and transport failures never log credentials."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx
import openai
from azure.core.exceptions import AzureError
from azure.identity.aio import (
    AzureCliCredential,
    DefaultAzureCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from openai import AsyncOpenAI

from .agents import AgentSpec, active_version, catalog, lane_keys
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
    agent: str | None = None
    agent_version: str | None = None
    response_id: str | None = None
    sent: dict[str, Any] | None = None


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
        404: "Foundry could not find the agent, deployment, or API endpoint.",
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


def response_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for msg in messages:
        content = msg["content"]
        parts = [{"text": content}] if isinstance(content, str) else content
        result.append({"type": "message", "role": msg["role"],
                       "content": [{**part, "type": "input_text"} for part in parts]})
    return result


def agent_reply(data: dict[str, Any], spec: AgentSpec, version: str,
                request_id: str | None, sent: dict[str, Any]) -> Reply:
    reference = data.get("agent_reference") or {}
    response_id = data.get("id")
    usage, model = data.get("usage"), data.get("model")
    if (reference.get("name") != spec.name or reference.get("version") != version
            or not isinstance(response_id, str) or not response_id):
        raise ModelError("Foundry did not confirm execution by the selected agent version.")
    if not isinstance(model, str) or not model or not isinstance(usage, dict):
        raise ModelError("The Foundry agent response is missing model or usage details.")
    messages = [part["text"] for item in (data.get("output") or [])
                if item.get("type") == "message" and item.get("role") == "assistant"
                for part in item["content"] if part.get("type") == "output_text"]
    text = "".join(messages) if data.get("status") == "completed" else ""
    return Reply(model, text, usage, request_id, agent=spec.name, agent_version=version,
                 response_id=response_id, sent=sent)


class LiveClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.credential: ManagedIdentityCredential | AzureCliCredential | DefaultAzureCredential
        if os.getenv("WEBSITE_SITE_NAME"):
            self.credential = ManagedIdentityCredential()
        elif settings.azure_tenant_id:
            self.credential = AzureCliCredential(tenant_id=settings.azure_tenant_id)
        else:
            self.credential = DefaultAzureCredential()
        self.token = get_bearer_token_provider(self.credential, settings.azure_token_scope)
        self.project_token = get_bearer_token_provider(self.credential, "https://ai.azure.com/.default")
        self.http = httpx.AsyncClient(timeout=60, follow_redirects=False)
        self.openai_http = httpx.AsyncClient(timeout=60, follow_redirects=False)
        self.openai = AsyncOpenAI(
            base_url=settings.azure_openai_base_url, api_key=self.token, max_retries=0,
            timeout=60, http_client=self.openai_http,
        )
        self.project = settings.foundry_project_endpoint.rstrip("/")
        self.agents = catalog(settings)
        self.versions: dict[str, str] = {}

    async def agent_version(self, spec: AgentSpec) -> str:
        if spec.name in self.versions:
            return self.versions[spec.name]
        try:
            token = await self.project_token()
            response = await self.http.get(
                f"{self.project}/agents/{spec.name}", params={"api-version": "v1"},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            version = active_version(response.json(), spec)
        except httpx.HTTPStatusError as exc:
            raise ModelError(response_error(exc.response.status_code), exc.response.status_code) from None
        except (httpx.RequestError, AzureError):
            raise ModelError("Foundry agent lookup or Entra authentication failed.") from None
        except ValueError:
            raise ModelError(f"Publish the expected active definition of {spec.name}.") from None
        return self.versions.setdefault(spec.name, version)

    async def invoke_agent(self, spec: AgentSpec, body: dict[str, Any]) -> Reply:
        version = await self.agent_version(spec)
        sent = {
            "agent_reference": {"type": "agent_reference", "name": spec.name, "version": version},
            "input": response_input(body["messages"]),
            "store": False,
            **body.get("extra_body", {}),
        }
        token = await self.project_token()
        response = await self.http.post(
            f"{self.project}/openai/v1/responses", json=sent, headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return agent_reply(response.json(), spec, version, response.headers.get("x-request-id"), sent)

    async def invoke(self, kind: str, deployment: str, body: dict[str, Any], ctx: CallContext) -> Reply:
        if kind in {"chat", "router"}:
            schema = body.get("response_format", {}).get("json_schema", {}).get("name")
            if ctx.lane in {"control", "candidate"} and schema != "invoice_review":
                keys = lane_keys(ctx.lever, ctx.mode)[ctx.lane]
                matching = [self.agents[key] for key in keys if self.agents[key].deployment == deployment]
                if len(matching) != 1 or matching[0].schema != body.get("response_format"):
                    raise ModelError("There is no matching Foundry agent for this lane and model.")
                return await self.invoke_agent(matching[0], body)
            # Health probes and the optional reviewer are not benchmark generation lanes.
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
