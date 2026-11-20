"""FastAPI routes and the local, build-free presenter interface."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .dataset import load_dataset
from .fake import FIXTURES, FakeClient
from .foundry import CallContext, LiveClient, ModelClient, ModelError
from .grading import parse_answer
from .guards import SpendLimit, authorized, check_access
from .pricing import PriceBook, usd
from .runs import TABS, Demo, Run, now
from .settings import ROOT, Settings
from .sse import stream
from .status import StatusProbe
from .telemetry import setup

STATIC = ROOT / "app" / "static"
LOG = logging.getLogger("tokecon")
AccessHeader = Annotated[str | None, Header(alias="X-Demo-Code")]


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lever: str
    mode: str
    count: int = Field(strict=True)


class AskCache(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["fact_guarded", "naive"]
    question: str = Field(min_length=1, max_length=500)


def create_app(config: Settings | None = None, client: ModelClient | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        api.state.demo = None
        api.state.problem = None
        try:
            settings = config or Settings()
            logging.basicConfig(level=settings.log_level)
            data = load_dataset()
            fake = settings.source == "fake"
            prices = PriceBook.load(FIXTURES / "pricing.json" if fake else settings.pricing_path,
                                    "" if fake else settings.pricing_override_json)
        except (ValidationError, ValueError, OSError) as exc:
            api.state.problem = "Startup configuration is invalid. Check pricing, environment variables, and fixtures."
            LOG.error("%s (%s)", api.state.problem, type(exc).__name__)
            yield
            return
        model = client
        problem = settings.live_problem() if settings.source == "live" else None
        try:
            if model is None and not problem and settings.source != "replay-only":
                model = FakeClient(settings) if fake else LiveClient(settings)
        except (ValueError, OSError) as exc:
            problem = "Model client configuration or fixtures are invalid."
            LOG.error("%s (%s)", problem, type(exc).__name__)
        try:
            setup(settings)
        except (ValueError, RuntimeError) as exc:
            LOG.error("Application Insights telemetry setup failed (%s).", type(exc).__name__)
        demo = Demo(settings, data, prices, model)
        api.state.demo = demo
        probe = StatusProbe(demo, problem)
        api.state.probe = probe
        probe.refresh()
        try:
            yield
        finally:
            for task in (demo.active, probe.task):
                if task and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            if model is not None:
                await model.close()

    api = FastAPI(title="Token Economics Live", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)

    @api.middleware("http")
    async def harden(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def demo_for(request: Request) -> Demo:
        demo = request.app.state.demo
        if demo is None:
            raise HTTPException(503, request.app.state.problem)
        return demo

    def find(request: Request, run_id: UUID) -> Run:
        run = demo_for(request).registry.get(str(run_id))
        if run is None:
            raise HTTPException(404, "Run not found or expired.")
        return run

    def idle(demo: Demo) -> None:
        if demo.busy:
            raise HTTPException(409, "A run is already active. Wait for it to finish.")

    def paid(demo: Demo, code: str | None) -> None:
        check_access(demo.settings, code)
        if demo.settings.source == "replay-only":
            raise HTTPException(503, "This app is in Replay-only mode.")
        if demo.client is None:
            raise HTTPException(503, demo.settings.live_problem() or "Foundry is unavailable.")
        idle(demo)
        if demo.settings.source == "live":
            try:
                demo.spend.check()
            except SpendLimit as exc:
                raise HTTPException(429, str(exc)) from None

    @api.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @api.get("/api/status")
    async def status(request: Request, code: AccessHeader = None) -> dict[str, Any]:
        if request.app.state.demo is None:
            return {"mode": "replay-only", "foundry": "error", "detail": request.app.state.problem,
                    "deployments": {}, "prices_verified": False, "verified_on": None, "missing_prices": []}
        probe = request.app.state.probe
        if authorized(probe.demo.settings, code):
            probe.refresh()
        return probe.payload()

    @api.get("/api/config")
    async def configuration(request: Request) -> dict[str, Any]:
        demo = demo_for(request)
        cfg = demo.settings
        tabs: dict[str, Any] = {}
        for key, tab in TABS.items():
            invoice = key == "retry_killswitch"
            modes = {k: v for k, v in tab["modes"].items()
                     if k != "tight" or cfg.tight_kill_switch_usd is not None
                     or (cfg.replay_only and demo.recordings.read(key, k))}
            workloads: list[dict[str, Any]] = (
                [{"id": r.id, "text": r.title, "category": r.kind} for r in demo.data.invoices]
                if invoice else
                [{"id": r.id, "text": r.text, "language": r.lang, "category": r.category,
                  "expected_tier": r.tier} for r in demo.data.requests[key]]
            )
            tabs[key] = {**tab, "modes": modes, "requests": workloads,
                         "recordings": [mode for mode in modes if demo.recordings.read(key, mode)],
                         "default": 3 if invoice else cfg.default_request_count,
                         "min": 3 if invoice else cfg.min_request_count, "max": 6 if invoice else cfg.max_request_count}
        return {"tabs": tabs, "mode": cfg.source, "deployments": cfg.deployments,
                "access_code_required": cfg.source == "live" or cfg.access_code_configured,
                "prompt_cache_mode": cfg.prompt_cache_mode, "pricing": demo.prices.model_dump(mode="json"),
                "prefix_layout": demo.data.prefix_layout}

    @api.post("/api/runs", status_code=202)
    async def start(request: Request, body: StartRun, code: AccessHeader = None) -> dict[str, str]:
        demo = demo_for(request)
        paid(demo, code)
        if body.lever not in TABS or body.mode not in TABS[body.lever]["modes"]:
            raise HTTPException(422, "Unknown lever or mode.")
        if body.mode == "tight" and demo.settings.tight_kill_switch_usd is None:
            raise HTTPException(422, "The tight budget is not configured.")
        lowest, highest = ((3, 6) if body.lever == "retry_killswitch" else
                           (demo.settings.min_request_count, demo.settings.max_request_count))
        if not lowest <= body.count <= highest:
            raise HTTPException(422, f"Choose between {lowest} and {highest} requests.")
        return {"run_id": demo.start(body.lever, body.mode, body.count).id}

    @api.get("/api/runs/{run_id}/events")
    async def events(request: Request, run_id: UUID, last_event_id: Annotated[str | None, Header()] = None):
        run = find(request, run_id)
        try:
            cursor = min(max(int(last_event_id or 0), 0), len(run.events))
        except ValueError:
            cursor = 0
        return StreamingResponse(stream(run, cursor), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform",
                                          "X-Accel-Buffering": "no"})

    @api.get("/api/runs/{run_id}")
    async def snapshot(request: Request, run_id: UUID) -> dict[str, Any]:
        return find(request, run_id).snapshot()

    @api.post("/api/replays/{lever}/{mode}", status_code=202)
    async def replay(request: Request, lever: str, mode: str, speed: int = 1) -> dict[str, str]:
        demo = demo_for(request)
        idle(demo)
        if lever not in TABS or mode not in TABS[lever]["modes"] or speed not in (1, 2):
            raise HTTPException(422, "Unknown recording or unsupported playback speed.")
        stored = demo.recordings.read(lever, mode)
        if stored is None:
            raise HTTPException(404, "No successful run has been recorded for this mode.")
        return {"run_id": demo.start_replay(stored, speed).id}

    @api.get("/api/replays/{lever}/{mode}/recording")
    async def download_recording(request: Request, lever: str, mode: str, code: AccessHeader = None) -> dict[str, Any]:
        demo = demo_for(request)
        check_access(demo.settings, code)
        if lever not in TABS or mode not in TABS[lever]["modes"]:
            raise HTTPException(422, "Unknown lever or mode.")
        stored = demo.recordings.read(lever, mode)
        if not stored:
            raise HTTPException(404, "Recording not found.")
        return stored

    @api.post("/api/cache/clear")
    async def clear_cache(request: Request, code: AccessHeader = None) -> dict[str, bool]:
        demo = demo_for(request)
        check_access(demo.settings, code)
        idle(demo)
        demo.cache.clear()
        return {"cleared": True}

    @api.post("/api/cache/ask")
    async def ask_cache(request: Request, body: AskCache, code: AccessHeader = None) -> dict[str, Any]:
        demo = demo_for(request)
        paid(demo, code)
        known = next((r.id for r in demo.data.requests["response_cache"] if r.text == body.question), "follow-up")
        ctx = CallContext("response_cache", body.mode, "candidate", known)
        calls = demo.session(ctx)
        task = asyncio.create_task(demo.cache.lookup(calls, body.question, now(), str(uuid4())))
        demo.active, demo.active_run_id = task, None
        try:
            result, detail = await task
        except SpendLimit as error:
            raise HTTPException(429, str(error)) from None
        except ModelError as error:
            raise HTTPException(502, str(error)) from None
        finally:
            calls.report(None)
        parsed = parse_answer(result)
        return {**detail, "graded": False, "answer": parsed.answer if parsed else result,
                "cost_usd": usd(calls.cost), "source": demo.settings.source}

    @api.get("/api/scoreboard")
    async def scoreboard(request: Request) -> dict[str, Any]:
        return {"runs": demo_for(request).recordings.scoreboard()}

    @api.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    api.mount("/static", StaticFiles(directory=STATIC), name="static")
    return api


app = create_app()
