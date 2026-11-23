import asyncio
import json

import httpx
import pytest

from app.agents import catalog
from app.foundry import CallContext, LiveClient, ModelError
from app.prompts import ANSWER_SCHEMA
from app.settings import Settings


def test_live_client_initializes_without_network_on_app_service(monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "test-app")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://127.0.0.1:8081/identity")
    monkeypatch.setenv("IDENTITY_HEADER", "test-only")
    settings = Settings(azure_openai_base_url="https://example.openai.azure.com/openai/v1/")

    async def open_and_close():
        await LiveClient(settings).close()

    asyncio.run(open_and_close())


def test_pinned_agent_calls_verify_server_identity_and_preserve_usage():
    settings = Settings(azure_openai_base_url="https://example.openai.azure.com/openai/v1/",
                        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/example")
    spec = catalog(settings)["support-lever"]
    usage = {"input_tokens": 1800, "input_tokens_details": {"cached_tokens": 1560}, "output_tokens": 20}
    lookups = 0
    response_agent = spec.name

    def respond(request):
        nonlocal lookups
        if request.method == "GET":
            lookups += 1
            assert request.url.path.endswith("/agents/tokecon-support-lever")
            return httpx.Response(200, json={
                "name": spec.name, "state": "enabled",
                "versions": {"latest": {"version": "7", "status": "active", "definition": spec.definition()}},
            })
        sent = json.loads(request.content)
        assert request.url.path.endswith("/openai/v1/responses")
        assert sent["agent_reference"] == {"type": "agent_reference", "name": spec.name, "version": "7"}
        assert sent["input"][0]["content"] == [{
            "type": "input_text", "text": "Stable policy prefix", "prompt_cache_breakpoint": {"mode": "explicit"},
        }]
        assert sent["store"] is False
        return httpx.Response(200, headers={"x-request-id": "transport-1"}, json={
            "id": "resp_1", "model": settings.frontier_deployment, "usage": usage, "status": "completed",
            "agent_reference": {"name": response_agent, "version": "7"}, "output": [
                {"type": "reasoning"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
            ],
        })

    async def credential():
        return "mock-token"

    async def invoke():
        nonlocal response_agent
        live = LiveClient(settings)
        await live.http.aclose()
        live.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        live.project_token = credential
        try:
            ctx = CallContext("prompt_caching", "default", "candidate", "pc-02")
            body = {"messages": [{"role": "system", "content": [
                {"type": "text", "text": "Stable policy prefix", "prompt_cache_breakpoint": {"mode": "explicit"}},
            ]}], "response_format": ANSWER_SCHEMA}
            reply = await live.invoke("chat", settings.frontier_deployment, body, ctx)
            assert (reply.agent, reply.agent_version, reply.response_id) == (spec.name, "7", "resp_1")
            assert reply.result == "answer" and reply.usage == usage and reply.request_id == "transport-1"
            response_agent = "tokecon-support-baseline"
            with pytest.raises(ModelError, match="did not confirm execution"):
                await live.invoke("chat", settings.frontier_deployment, body, ctx)
            assert lookups == 1
        finally:
            await live.close()

    asyncio.run(invoke())


@pytest.mark.parametrize("endpoint", [
    "https://example.services.ai.azure.com.evil.example/api/projects/example",
    "https://example.services.ai.azure.com/api/projects/other%2Fexample",
    "http://example.services.ai.azure.com/api/projects/example",
])
def test_project_endpoint_rejects_non_azure_token_destinations(endpoint):
    settings = Settings(azure_openai_base_url="https://example.openai.azure.com/openai/v1/",
                        foundry_endpoint="https://example.services.ai.azure.com", foundry_project_endpoint=endpoint,
                        demo_access_code="fixture-code")
    assert "FOUNDRY_PROJECT_ENDPOINT" in settings.live_problem()
