"""Verify the presenter in Chromium at projector and mobile sizes."""

import argparse
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Browser, Cookie, Page, Request, expect, sync_playwright


def sign_in(browser: Browser, base_url: str) -> list[Cookie]:
    context = browser.new_context(base_url=base_url)
    try:
        for path in ("/api/config", "/api/scoreboard"):
            result = context.request.get(path, max_redirects=0)
            if result.status not in (302, 401):
                raise AssertionError(f"Anonymous {path} returned {result.status}, expected a sign-in challenge.")

        def account_picker(request: Request) -> bool:
            target = urlsplit(request.url)
            return (request.is_navigation_request() and target.scheme == "https"
                    and target.hostname == "login.microsoftonline.com"
                    and parse_qs(target.query).get("prompt") == ["select_account"])

        page = context.new_page()
        print("Complete Microsoft sign-in in the browser. The session won't be saved to disk.", flush=True)
        with page.expect_request(account_picker):
            page.goto("/", wait_until="domcontentloaded")
        page.locator("#run").wait_for(state="visible", timeout=180_000)
        cookies = context.cookies(base_url)
        if not any(cookie["name"] == "AppServiceAuthSession" for cookie in cookies):
            raise AssertionError("Microsoft sign-in did not establish an App Service session.")
        return cookies
    finally:
        context.close()


def open_mode(page: Page, title: str, mode: str) -> None:
    page.get_by_role("navigation", name="Demo levers").get_by_role("button", name=title, exact=True).click()
    page.locator("#mode").select_option(mode)
    brief = page.get_by_role("region", name="Experiment setup")
    for label in ("Without the lever", "With the lever", "What to look for"):
        expect(brief.get_by_role("heading", name=re.compile(label))).to_be_visible()
    expect(brief.locator(".brief-part > p")).to_have_count(3)


def verify_layout(page: Page) -> None:
    if page.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
        raise AssertionError("The page scrolls horizontally.")
    if re.search(r"[\u0900-\u097f]", page.locator("body").inner_text()):
        raise AssertionError("The English presenter contains Devanagari text.")


def verify_howto(page: Page, badge: str) -> None:
    opener = page.get_by_role("button", name="How to implement")
    opener.click()
    howto = page.get_by_role("dialog", name="How to implement")
    expect(howto.locator(".diff pre")).to_have_count(2)
    expect(howto.get_by_text(badge, exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(howto).not_to_be_visible()
    expect(opener).to_be_focused()


def start_run(page: Page, count: int, code: str | None, timeout: int = 5_000) -> None:
    page.locator("#count").select_option(str(count))
    page.locator("#run").click()
    if code and not page.evaluate("sessionStorage.getItem('demo-access') !== null"):
        page.get_by_label("Access code", exact=True).fill(code)
        page.get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#progress")).to_have_text(f"{count * 2}/{count * 2} lane results · complete", timeout=timeout)
    expect(page.locator("#kpis .metric-value")).to_have_count(4)
    expect(page.locator("#requests .lane-head")).to_have_count(count * 2)


def verify_fake_mode(page: Page, title: str, mode: str, count: int, code: str | None) -> None:
    open_mode(page, title, mode)
    start_run(page, count, code)
    expect(page.locator("#status")).to_have_text("FAKE DATA")

    def candidate(request_id: str):
        return page.locator(".lane").filter(
            has=page.get_by_role("button", name=f"Inspect {request_id} candidate", exact=True)
        )

    if mode == "default":
        expect(candidate("pc-01").get_by_text(re.compile(r"\d written"))).to_be_visible()
        expect(candidate("pc-02").get_by_text(re.compile(r"\d cached"))).to_be_visible()
    elif mode == "foundry_router":
        expect(candidate("mr-01").get_by_text("Routing fee", exact=False)).to_be_visible()
        expect(candidate("mr-01").get_by_text("Low-cost pool", exact=False)).to_be_visible()
        expect(candidate("mr-03").get_by_text("Quality pool", exact=False)).to_be_visible()
        expect(page.locator("#kpis").get_by_text("2 models", exact=True)).to_be_visible()
        expect(page.locator("#readout")).to_contain_text("Input-cache reads: 0 → 0; writes: 0 → 0.")
    elif mode == "decision_router":
        expect(candidate("mr-01").get_by_text("small recommendation", exact=False)).to_be_visible()
        expect(candidate("mr-03").get_by_text("frontier recommendation", exact=False)).to_be_visible()
    elif mode == "small_all":
        expect(candidate("mr-03").get_by_text("✕ Miss", exact=True)).to_be_visible()
    elif mode == "fact_guarded":
        expect(page.locator("#readout")).to_contain_text("Input-cache reads: 0 → 0; writes: 0 → 0.")
        for request_id in ("rc-02", "rc-05", "rc-08", "rc-09"):
            expect(candidate(request_id).get_by_text("model skipped", exact=False)).to_be_visible()
        expect(candidate("rc-06").get_by_text("Model used → stored", exact=False)).to_be_visible()
        expect(candidate("rc-10").get_by_text("Bypassed:", exact=False)).to_be_visible()
        page.get_by_label("Ask a question against the cache").fill("Could you clarify when an approved card refund will arrive?")
        page.get_by_role("button", name="Ask · not graded", exact=True).click()
        expect(page.locator("#follow-up-result")).to_have_text(re.compile(r"^hit .* \$0\.00000 · not graded"))
        page.get_by_role("button", name="Clear cache", exact=True).click()
        expect(page.locator("#cache-entries")).to_have_text("Cache is empty.")
    elif mode == "naive":
        for request_id in ("rc-03", "rc-06"):
            expect(candidate(request_id).get_by_text("Wrong answer from cache", exact=False)).to_be_visible()
    else:
        expected = {"guardrails": "retry limit", "no_retries": "no retries", "tight": "kill switch"}[mode]
        expect(candidate("inv-03").get_by_text(re.compile(fr"→ {expected} → Handoff"))).to_be_visible()
        if mode == "guardrails":
            expect(candidate("inv-03").get_by_text(re.compile(r"Attempt 4 .*retry limit"))).to_be_visible()
        expect(candidate("inv-03").get_by_text("FX tool simulated", exact=False)).to_be_visible()
        expect(page.locator(".lane").filter(
            has=page.get_by_role("button", name="Inspect inv-03 control", exact=True)
        ).get_by_text(re.compile(r"Attempt 6.*safety ceiling"))).to_be_visible()

    verify_howto(page, "FAKE DATA")
    verify_layout(page)


def verify_inspect(page: Page, lane: str, agent: str | None = None) -> None:
    opener = page.get_by_role("button", name=f"Inspect pc-02 {lane}", exact=True)
    opener.click()
    drawer = page.get_by_role("dialog", name=f"pc-02 · {lane}")
    expect(drawer.get_by_text("Raw usage", exact=True)).to_be_visible()
    if agent:
        expect(drawer.get_by_text(agent, exact=True)).to_be_visible()
        expect(drawer.get_by_text("Agent version:", exact=False)).to_be_visible()
        expect(drawer.get_by_text("Foundry response ID:", exact=False)).to_be_visible()
    if lane == "candidate":
        expect(drawer.get_by_text(re.compile(r"cached read.+/ 1,000,000", re.I))).to_be_visible()
    verify_layout(page)
    page.keyboard.press("Escape")
    expect(opener).to_be_focused()


def verify_scoreboard(page: Page, count: int, badge: str) -> None:
    page.get_by_role("button", name="Scoreboard", exact=True).click()
    expect(page.locator("#scoreboard tbody tr")).to_have_count(count)
    expect(page.locator("#scoreboard").get_by_text(badge, exact=True).first).to_be_visible()
    expect(page.get_by_role("heading", name="Power-user month")).to_be_visible()
    expect(page.get_by_text("Projection from one live run, not a forecast.")).to_be_visible()
    verify_layout(page)


def verify_themes(page: Page) -> None:
    page.get_by_role("heading", name="Scoreboard", exact=True).click()
    page.keyboard.press("1")
    expect(page.get_by_role("heading", name="Prompt caching", exact=True)).to_be_visible()
    page.keyboard.press("d")
    expect(page.locator("#dark")).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Larger text").click()
    expect(page.locator("#large")).to_have_attribute("aria-pressed", "true")
    verify_layout(page)


def verify_live_or_offline(page: Page, config: dict, first: bool, code: str | None, screenshots: Path | None) -> None:
    live = config["mode"] == "live"
    badge = "Foundry live" if live else "REPLAY ONLY"
    expect(page.locator("#status")).to_have_text(badge)
    for tab in config["tabs"].values():
        for mode in tab["modes"]:
            open_mode(page, tab["title"], mode)
            expect(page.locator("#replay")).to_be_enabled()
            if live:
                for lane in tab["agents"][mode].values():
                    for agent in lane:
                        expect(page.locator("#brief").get_by_text(agent, exact=True)).to_be_visible()
            else:
                expect(page.locator("#run")).to_be_disabled()
            verify_howto(page, badge)
            verify_layout(page)

    scoreboard_badge = "LIVE" if live else "REPLAY · LIVE recording"
    verify_scoreboard(page, 9, scoreboard_badge)
    if first and screenshots:
        page.screenshot(path=screenshots / "tokecon-presenter-scoreboard.png", animations="disabled")
    if first:
        open_mode(page, "Prompt caching", "default")
        page.locator("#replay").click()
        expect(page.locator("#status")).to_have_text(re.compile(r"^REPLAY · recorded .+ IST$"), timeout=15_000)
        expect(page.locator("#progress")).to_have_text(
            re.compile(r"^([1-9]\d*)/\1 lane results · complete$"), timeout=100_000,
        )
        expect(page.locator("#access")).not_to_be_visible()
        verify_inspect(page, "candidate", "tokecon-support-lever")
        if live and code:
            start_run(page, config["tabs"]["prompt_caching"]["max"], code, timeout=100_000)
            expect(page.locator("#evidence-source")).to_have_text("LIVE")
            verify_inspect(page, "control", "tokecon-support-baseline")
            verify_inspect(page, "candidate", "tokecon-support-lever")
            if screenshots:
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=screenshots / "tokecon-presenter-run.png", animations="disabled")
        elif live:
            page.locator("#run").click()
            access = page.get_by_role("dialog", name="Demo access code")
            expect(access).to_be_visible()
            access.get_by_role("button", name="Cancel", exact=True).click()
            expect(access).not_to_be_visible()
    verify_scoreboard(page, 9, scoreboard_badge)
    verify_themes(page)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--channel", choices=("msedge", "chrome"))
    parser.add_argument("--source", choices=("fake", "live", "replay-only"), default="fake")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--sign-in", action="store_true", help="Sign in interactively to App Service with Microsoft Entra.")
    args = parser.parse_args()
    base = urlsplit(args.base_url)
    if args.source == "live":
        if base.scheme != "https" or not base.hostname or not base.hostname.endswith(".azurewebsites.net"):
            parser.error("Live verification requires the authorized HTTPS App Service URL.")
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
    elif base.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Use the local Fake or replay-only application.")
    if args.sign_in and args.source != "live":
        parser.error("--sign-in is only for a hosted Live test.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.channel, headless=not args.sign_in)
        cookies = sign_in(browser, args.base_url) if args.sign_in else []
        for width, height in ((1920, 1080), (1366, 768), (390, 844)):
            context = browser.new_context(viewport={"width": width, "height": height}, base_url=args.base_url)
            if cookies:
                context.add_cookies(cookies)
            errors: list[str] = []
            # Nothing for this browser workflow may be fetched from another origin.
            def restrict(route):
                url = urlsplit(route.request.url)
                if (url.scheme, url.netloc) == (base.scheme, base.netloc):
                    route.continue_()
                else:
                    errors.append("Unexpected external browser request")
                    route.abort()

            context.route("**/*", restrict)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto("/")
            assert page.evaluate("""async () => {
              const faces = await Promise.all(['20px "IBM Plex Sans"', '20px "Newsreader"']
                .map(font => document.fonts.load(font)));
              return faces.every(group => group.length && group.every(face => face.status === "loaded"));
            }"""), "Both self-hosted fonts must load."
            config = page.request.get("/api/config").json()
            assert config["mode"] == args.source
            code = os.getenv("DEMO_ACCESS_CODE")
            mode_count = sum(len(tab["modes"]) for tab in config["tabs"].values())
            assert mode_count == 9, "Configure the full set of nine modes before verification."
            if args.source != "fake":
                verify_live_or_offline(page, config, width == 1920, code, args.screenshots)
                assert not errors, errors
                print(f"{width}x{height}: {args.source} briefs, Scoreboard, fonts and controls passed.", flush=True)
                context.close()
                continue
            expect(page.locator("#status")).to_have_text("FAKE DATA")
            expect(page.locator("#run")).to_be_enabled()
            for tab in config["tabs"].values():
                for mode in tab["modes"]:
                    verify_fake_mode(page, tab["title"], mode, tab["max"], code)

            page.get_by_role("button", name="Prompt caching", exact=True).click()
            verify_inspect(page, "candidate")
            before = page.locator("#kpis").inner_text()
            page.get_by_role("button", name="Replay last run").click()
            expect(page.locator("#status")).to_have_text(re.compile(r"^REPLAY · recorded .+ IST · FAKE DATA$"))
            expect(page.locator("#progress")).to_have_text("20/20 lane results · complete")
            assert page.locator("#kpis").inner_text() == before
            verify_scoreboard(page, 9, "REPLAY · FAKE recording")
            projection = page.locator(".projection [aria-live]")
            initial = projection.inner_text()
            page.get_by_label("Requests per power user per month", exact=False).fill("2000")
            expect(projection).not_to_have_text(initial)
            verify_themes(page)
            assert not errors, errors
            print(f"{width}x{height}: nine Fake modes, Inspect, Replay, Scoreboard and controls passed.", flush=True)
            context.close()
        browser.close()


if __name__ == "__main__":
    main()
