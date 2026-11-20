"""Verify the Fake presenter workflow in Chromium at both projector sizes."""

import argparse
import os
import re
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect, sync_playwright


def verify_mode(page: Page, title: str, mode: str, count: int, code: str | None) -> None:
    page.get_by_role("navigation", name="Demo levers").get_by_role("button", name=title, exact=True).click()
    page.locator("#mode").select_option(mode)
    page.locator("#count").select_option(str(count))
    page.locator("#run").click()
    if code and not page.evaluate("sessionStorage.getItem('demo-access') !== null"):
        page.get_by_label("Access code", exact=True).fill(code)
        page.get_by_role("button", name="Continue", exact=True).click()
    expect(page.locator("#progress")).to_have_text(f"{count * 2}/{count * 2} lane results · complete")
    expect(page.locator("#status")).to_have_text("FAKE DATA")
    expect(page.locator("#kpis .metric-value")).to_have_count(4)
    expect(page.locator("#requests .lane-head")).to_have_count(count * 2)

    def candidate(request_id: str):
        return page.locator(".lane").filter(
            has=page.get_by_role("button", name=f"Inspect {request_id} candidate", exact=True)
        )

    if mode == "default":
        expect(candidate("pc-01").get_by_text(re.compile(r"\d written"))).to_be_visible()
        expect(candidate("pc-02").get_by_text(re.compile(r"\d cached"))).to_be_visible()
    elif mode == "foundry_router":
        expect(candidate("mr-01").get_by_text("Routing fee", exact=False)).to_be_visible()
    elif mode == "decision_router":
        expect(candidate("mr-01").get_by_text("small ·", exact=False)).to_be_visible()
        expect(candidate("mr-03").get_by_text("frontier ·", exact=False)).to_be_visible()
    elif mode == "small_all":
        expect(candidate("mr-03").get_by_text("✕ Miss", exact=True)).to_be_visible()
    elif mode == "fact_guarded":
        for request_id in ("rc-02", "rc-05", "rc-08"):
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
        expected = {"guardrails": "no new evidence", "no_retries": "no retries", "tight": "kill switch"}[mode]
        expect(candidate("inv-03").get_by_text(re.compile(fr"→ {expected} → Handoff"))).to_be_visible()
        expect(candidate("inv-03").get_by_text("FX tool simulated", exact=False)).to_be_visible()
        expect(page.locator(".lane").filter(
            has=page.get_by_role("button", name="Inspect inv-03 control", exact=True)
        ).get_by_text(re.compile(r"Attempt 6.*safety ceiling"))).to_be_visible()

    page.get_by_role("button", name="How to implement").click()
    howto = page.get_by_role("dialog", name="How to implement")
    expect(howto.locator(".diff pre")).to_have_count(2)
    expect(howto.get_by_text("FAKE DATA", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(howto).not_to_be_visible()
    if page.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
        raise AssertionError(f"Horizontal overflow in {title}/{mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--channel", choices=("msedge", "chrome"))
    args = parser.parse_args()
    base = urlsplit(args.base_url)
    if base.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Use a local Fake instance, not the live presenter app.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.channel)
        for width, height in ((1366, 768), (1920, 1080)):
            context = browser.new_context(viewport={"width": width, "height": height}, base_url=args.base_url)
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
            expect(page.locator("#status")).to_have_text("FAKE DATA")
            expect(page.locator("#run")).to_be_enabled()
            config = page.request.get("/api/config").json()
            assert config["mode"] == "fake"
            code = os.getenv("DEMO_ACCESS_CODE")
            mode_count = sum(len(tab["modes"]) for tab in config["tabs"].values())
            assert mode_count == 9, "Set TIGHT_KILL_SWITCH_USD=0.0007 on the Fake server."
            for tab in config["tabs"].values():
                for mode in tab["modes"]:
                    verify_mode(page, tab["title"], mode, tab["max"], code)

            page.get_by_role("button", name="Prompt caching", exact=True).click()
            page.get_by_role("button", name="Inspect pc-02 candidate", exact=True).click()
            drawer = page.get_by_role("dialog", name="pc-02 · candidate")
            expect(drawer.get_by_text("Raw usage", exact=True)).to_be_visible()
            expect(drawer.get_by_text(re.compile(r"cached read.+/ 1,000,000", re.I))).to_be_visible()
            page.keyboard.press("Escape")
            before = page.locator("#kpis").inner_text()
            page.get_by_role("button", name="Replay last run").click()
            expect(page.locator("#status")).to_have_text(re.compile(r"^REPLAY · recorded .+ IST · FAKE DATA$"))
            expect(page.locator("#progress")).to_have_text("20/20 lane results · complete")
            assert page.locator("#kpis").inner_text() == before
            page.get_by_role("button", name="Scoreboard", exact=True).click()
            expect(page.locator("#scoreboard tbody tr")).to_have_count(9)
            expect(page.get_by_text("REPLAY · FAKE recording", exact=True)).to_be_visible()
            expect(page.get_by_role("heading", name="Power-user month")).to_be_visible()
            expect(page.get_by_text("Projection from one live run, not a forecast.")).to_be_visible()
            projection = page.locator(".projection [aria-live]")
            initial = projection.inner_text()
            page.get_by_label("Requests per power user per month", exact=False).fill("2000")
            expect(projection).not_to_have_text(initial)
            page.get_by_role("heading", name="Scoreboard", exact=True).click()
            page.keyboard.press("1")
            expect(page.get_by_role("heading", name="Prompt caching", exact=True)).to_be_visible()
            page.keyboard.press("d")
            expect(page.locator("#dark")).to_have_attribute("aria-pressed", "true")
            page.get_by_role("button", name="Larger text").click()
            expect(page.locator("#large")).to_have_attribute("aria-pressed", "true")
            assert not errors, errors
            print(f"{width}×{height}: nine Fake modes, Inspect, Replay, Scoreboard and controls passed.")
            context.close()
        browser.close()


if __name__ == "__main__":
    main()
