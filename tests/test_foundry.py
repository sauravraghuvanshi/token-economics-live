import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal

import httpx
import pytest

from app.agents import catalog
from app.calls import CallSession
from app.dataset import load_dataset
from app.fake import FIXTURES
from app.foundry import CallContext, LiveClient, ModelError
from app.guards import SpendWindow
from app.levers.model_routing import route
from app.levers.prompt_caching import answer
from app.levers.response_cache import ResponseCache
from app.pricing import PriceBook
from app.prompts import ANSWER_SCHEMA
from app.settings import Settings


@asynccontextmanager
async def mock_foundry(settings, respond):
    async def credential():
        return "mock-token"

    live = LiveClient(settings)
    await live.http.aclose()
    live.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    live.project_token = live.token = credential
    try:
        yield live
    finally:
        await live.close()


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

    async def invoke():
        nonlocal response_agent
        async with mock_foundry(settings, respond) as live:
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

    asyncio.run(invoke())


@pytest.mark.parametrize("cache_mode", ["explicit", "auto"])
async def test_response_cache_lanes_send_same_prompt_with_no_cache_key(cache_mode):
    settings = Settings(azure_openai_base_url="https://example.openai.azure.com/openai/v1/",
                        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/example",
                        prompt_cache_mode=cache_mode)
    agents = catalog(settings)
    posts = []
    dataset = load_dataset()
    request = dataset.requests["response_cache"][0]
    answer_text = json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))[request.id]

    def respond(http_request):
        body = json.loads(http_request.content)
        posts.append(body)
        ref = body["agent_reference"]
        return httpx.Response(200, json={
            "id": f"resp_{len(posts)}", "model": settings.frontier_deployment, "status": "completed",
            "agent_reference": ref, "usage": {"input_tokens": 3600, "output_tokens": 150,
                                               "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}},
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(answer_text)},
            ]}],
        })

    async with mock_foundry(settings, respond) as live:
        live.versions.update({spec.name: "7" for spec in agents.values()})
        prices = PriceBook.load(FIXTURES / "pricing.json")
        for lane in ("control", "candidate"):
            calls = CallSession(live, prices, CallContext("response_cache", "fact_guarded", lane, request.id), None)
            if lane == "control":
                await answer(calls, settings, dataset.policies, request.text, "now", "ticket", cache_prompt=False)
            else:
                await ResponseCache(settings, dataset.policies, dataset.patterns).lookup(
                    calls, request.text, "now", "ticket"
                )
            assert calls.tokens["cached"] == calls.tokens["cache_write"] == 0
    assert [body["agent_reference"]["name"] for body in posts] == [
        agents["support-baseline"].name, agents["support-lever"].name,
    ]
    assert posts[0]["input"] == posts[1]["input"]
    for body in posts:
        assert body.get("prompt_cache_options") == ({"mode": "explicit"} if cache_mode == "explicit" else None)
        assert "prompt_cache_key" not in body and "prompt_cache_breakpoint" not in json.dumps(body)


async def test_foundry_router_sends_preview_controls_and_verifies_paid_selection(tmp_path):
    settings = Settings(azure_openai_base_url="https://example.openai.azure.com/openai/v1/",
                        foundry_endpoint="https://example.services.ai.azure.com",
                        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/example")
    spec = catalog(settings)["support-lever-router"]
    baseline = catalog(settings)["support-baseline"]
    prices = PriceBook.load(FIXTURES / "pricing.json")
    spend = SpendWindow(tmp_path / "spend.json", Decimal("2"))
    settings_by_question = {
        "When does the trial end?": ("small", 0.98, "cost", "gpt-4.1-mini-2025-04-14"),
        "Can I bypass login and get a prorated refund?": ("frontier", 0.98, "quality", "gpt-6.1-sol-2026-09-29"),
        "Would that also apply here?": ("small", 0.61, "quality", "gpt-6.1-sol-2026-09-29"),
    }
    ranking_pool = {"cost": ["gpt-4.1-mini"], "quality": ["gpt-6.1-sol"]}
    selected = None
    invalid = None
    posted = []
    baseline_input = None

    def respond(request):
        nonlocal selected, baseline_input
        sent = json.loads(request.content)
        posted.append(request.url.path)
        if request.url.path.endswith("/systemone"):
            assert sent["model"] == settings.decision_deployment
            selected = settings_by_question[sent["state"]]
            tier, certainty, _, _ = selected
            return httpx.Response(200, json={"model": "microsoft-decision-1.1", "usage": {"input_tokens": 21},
                                             "answers": {"tier": {"choice": tier, "probabilities": {tier: certainty}}}})
        assert request.url.path.endswith("/openai/v1/responses")
        assert sent["store"] is False
        assert "prompt_cache_key" not in sent and "prompt_cache_breakpoint" not in json.dumps(sent["input"])
        assert sent["input"][1]["content"][0]["text"].startswith("Received now")
        assert sent["input"][1]["content"][0]["text"].endswith("Policy corpus")
        if sent["agent_reference"]["name"] == baseline.name:
            baseline_input = sent["input"]
            mode, model = None, settings.frontier_deployment
            assert "routing_config" not in sent
        else:
            assert request.headers["Foundry-Features"] == "ModelRouterControls=V1Preview"
            assert sent["agent_reference"] == {"type": "agent_reference", "name": spec.name, "version": "2"}
            assert selected is not None
            _, _, mode, model = selected
            assert sent["routing_config"] == {"mode": mode, "models": ranking_pool[mode],
                                               "session_affinity": {"strategy": "none"}}
            if mode == "cost":
                assert sent["input"] == baseline_input
        assert sent.get("prompt_cache_options") == (None if mode == "cost" else {"mode": "explicit"})
        selection = {} if invalid == "no-mode" else {"model_router_details": {"mode": mode}}
        return httpx.Response(200, json={
            "id": f"resp_{len(posted)}", "model": "gpt-6.1-sol" if invalid == "outside-pool" else model,
            "status": "completed", "agent_reference": sent["agent_reference"],
            "usage": {"input_tokens": 3600, "output_tokens": 120},
            "model_selection_details": selection,
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "answer"},
            ]}],
        })

    async with mock_foundry(settings, respond) as live:
        live.versions.update({baseline.name: "2", spec.name: "2"})
        control = CallSession(live, prices, CallContext("model_routing", "foundry_router", "control", "dynamic"),
                              spend)
        await route(control, settings, "Policy corpus", next(iter(settings_by_question)), "now", "ticket")
        for question, (_, _, mode, model) in settings_by_question.items():
            calls = CallSession(live, prices, CallContext("model_routing", "foundry_router", "candidate", "dynamic"),
                                spend)
            reply, detail = await route(calls, settings, "Policy corpus", question, "now", "ticket")
            assert reply.model == detail["answer_model"] == model
            assert detail["router_mode"] == calls.calls[1]["routing_mode"] == mode
            assert [call["kind"] for call in calls.calls] == ["decision", "router"]
            assert Decimal(detail["routing_fee_usd"]) > 0 and Decimal(detail["classification_cost_usd"]) > 0
        for invalid in ("no-mode", "outside-pool"):
            calls = CallSession(live, prices, CallContext("model_routing", "foundry_router", "candidate", "dynamic"),
                                spend)
            with pytest.raises(ModelError, match="did not confirm the cost routing pool"):
                await route(calls, settings, "Policy corpus", next(iter(settings_by_question)), "now", "ticket")
            assert [call["kind"] for call in calls.calls] == ["decision", "router"]
            assert [Decimal(value) for _, value in spend.entries[-2:]] == [
                Decimal(call["cost_usd"]) for call in calls.calls
            ]
            assert calls.cost is not None and calls.cost > 0
    assert len(posted) == 11


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
