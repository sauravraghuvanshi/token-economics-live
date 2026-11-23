import { byId as $, comparison, el, latency, num, pair, pct, usd } from "./format.js";

const asNum = value => value == null ? "—" : num(value);
const graded = (lane, count) => lane?.done ? `${lane.accepted}/${count}` : "—";
export const unitCost = (lane, incomplete = false) => {
  if (!lane?.done || incomplete || lane.complete === false) return "—";
  return lane.accepted === 0 ? "n/a" : usd(lane.cpa_usd, 5);
};
const routeMix = lane => Object.entries(lane?.route_mix || {}).map(([tier, n]) => `${n} ${tier}`);

export function metrics(lever, data, count) {
  const c = data?.control || {}, a = data?.candidate || {};
  const quality = !c.done || c.done !== a.done ? null : a.accepted - c.accepted;
  const priced = data?.complete !== false;
  const money = (lane, render) => priced && lane.done ? render(lane) : "—";
  const signal = value => value > 0 ? "pass" : value < 0 ? "miss" : "";
  const metric = (label, left, right, note, tone) => ({ label, left, right, note, tone });
  const route = routeMix(a).join(" / ");
  const entries = [
    metric("Cost per accepted outcome", unitCost(c, !priced), unitCost(a, !priced),
      !priced ? "Incomplete costs"
        : data?.cpa_change_pct != null ? `${comparison(data.cpa_change_pct)} with the lever`
          : c.done && a.done && (!c.accepted || !a.accepted) ? "n/a when no outcomes pass" : "Total cost divided by passes",
      data?.cpa_change_pct == null ? "" : signal(-data.cpa_change_pct)),
    metric("Accepted outcomes", graded(c, count), graded(a, count),
      quality == null ? "Passed the same checks"
        : quality === 0 ? "Same number passed" : `${Math.abs(quality)} ${quality > 0 ? "more" : "fewer"} passed with the lever`,
      quality == null ? "" : signal(quality)),
    metric("Total model cost", money(c, lane => usd(lane.cost_usd)), money(a, lane => usd(lane.cost_usd)),
      !priced ? "Incomplete costs" : data?.savings_pct != null
        ? `${comparison(data.savings_pct, true)} with the lever` : "USD · configured rates",
      data?.savings_pct == null ? "" : signal(data.savings_pct)),
    {
      prompt_caching: metric("Cached input share", pct(c.cached_share_pct), pct(a.cached_share_pct), "Cache reads ÷ input tokens"),
      model_routing: metric("Model tiers used", route || "—", null, "With the lever · answer calls"),
      response_cache: metric("Answer model calls", asNum(c.model_calls), asNum(a.model_calls),
        data ? `${pair(latency(c.avg_latency_ms), latency(a.avg_latency_ms))} avg. latency` : "Excludes similarity lookups"),
      retry_killswitch: metric("Paid model calls", asNum(c.calls), asNum(a.calls),
        a.wasted_token_share_pct != null ? `${pair(pct(c.wasted_token_share_pct), pct(a.wasted_token_share_pct))} repeated-evidence token share`
          : "Extractor and reviewer calls"),
    }[lever],
  ];
  const root = $("kpis"), old = [...root.querySelectorAll(".metric-value")].map(node => node.textContent);
  const nodes = entries.map(({ label, left, right, note, tone }, i) => {
    const card = el("article", null, "metric");
    const value = el("p", null, `metric-value${right == null ? " compact" : ""}`);
    value.append(el("span", left));
    if (right != null) value.append(el("span", " → ", "metric-arrow"), el("span", right, "with-value"));
    if (value.textContent !== old[i]) value.classList.add("changed");
    card.append(el("p", label, "metric-label"), value, el("p", note, `metric-sub ${tone || ""}`));
    return card;
  });
  root.replaceChildren(...nodes);
}

const plural = (n, label, many = `${label}s`) => `${n} ${n === 1 ? label : many}`;
const select = (rows, predicate) => rows.filter(row => predicate(row.lever_detail || {}, row));
const examples = rows => rows.length ? ` (${rows.map(row => row.request_id).join(", ")})` : "";

function measured(lever, mode, run) {
  const changed = run.results.filter(row => row.lane === "candidate");
  const { control: c, candidate: a } = run.kpis;
  switch (lever) {
    case "prompt_caching": {
      const readers = changed.filter(row => row.totals.tokens?.cached > 0);
      const read = pair(num(c.tokens?.cached || 0), num(a.tokens?.cached || 0));
      const written = pair(num(c.tokens?.cache_write || 0), num(a.tokens?.cache_write || 0));
      const first = readers.some(row => row.idx === 1) ? " The first request reported a read." : "";
      return `Token reads: ${read} (${readers.length}/${a.done} requests with the lever); writes: ${written}.${first}`;
    }
    case "model_routing": {
      const mix = routeMix(a).join(", ");
      const starts = mix ? `With the lever: ${mix}. ` : "";
      const dips = changed.filter(row => row.status !== "error" && !row.pass);
      if (mode === "small_all") return `${starts}${dips.length ? `Misses${examples(dips)}.` : "No misses in the changed lane."}`;
      if (mode === "decision_router") {
        const classified = select(changed, d => d.confidence != null);
        const escalated = select(classified, d => d.decision_tier === "small" && d.tier === "frontier");
        return `${starts}${plural(classified.length, "recorded decision")}; ${plural(escalated.length, "small recommendation")} sent to frontier.`;
      }
      const fees = select(changed, d => d.routing_fee_usd != null);
      return `${starts}${plural(fees.length, "request")} reported a routing fee.`;
    }
    case "response_cache": {
      const hits = select(changed, d => d.cache_action === "hit");
      const wrong = select(hits, d => d.wrong_hit);
      const bypassed = select(changed, d => d.cache_action === "bypass");
      const embeddings = changed.reduce((n, row) => n + (row.calls || []).filter(call => call.kind === "embedding").length, 0);
      return `${plural(hits.length, "hit")} skipped answer generation; ${plural(wrong.length, "reused answer")} failed checks${examples(wrong)}. `
        + (mode === "naive" ? `${plural(embeddings, "embedding lookup")} recorded.` : `${plural(bypassed.length, "question")} bypassed the cache.`);
    }
    case "retry_killswitch": {
      const recovered = rows => select(rows, (d, row) => row.pass && d.attempts?.length > 1).length;
      const handed = select(changed, d => d.outcome === "handoff");
      const stop = mode === "tight" ? "kill switch" : mode === "no_retries" ? "no retries" : "no new evidence";
      const stopped = select(handed, d => d.stop === stop);
      return `Recovered after a retry: ${pair(recovered(run.results.filter(row => row.lane === "control")), recovered(changed))}. `
        + `With the lever: ${plural(handed.length, "handoff")}; ${stopped.length} stopped by ${stop === "no new evidence" ? "repeated evidence" : stop === "no retries" ? "the zero-retry rule" : "the spend cap"}${examples(stopped)}.`;
    }
    default: return "";
  }
}

function take(run) {
  const d = run.kpis;
  if (run.error) return ["Run stopped", run.error, "miss"];
  if (!run.results?.length && !run.done) return run.results
    ? ["Starting", "Waiting for the first lane result.", ""]
    : ["Ready to compare", "Run both lanes to see cost, passes and the trade-off for this mode.", ""];
  if (!run.done) return ["Measuring", run.reconnecting ? "The stream was interrupted. Reconnecting to the same run…"
    : "Each request appears as its lanes finish. The verdict waits for the full batch.", ""];
  if (!d || d.complete === false || d.control.done !== run.runCount || d.candidate.done !== run.runCount
      || run.results.some(row => row.status === "error" || row.status === "price_missing")) {
    return ["Incomplete", "Some results or prices are missing. There’s no reliable cost-per-outcome verdict for this batch.", "warn"];
  }
  const accepted = d.candidate.accepted - d.control.accepted;
  const change = d.cpa_change_pct == null ? null : Number(d.cpa_change_pct);
  const quality = accepted === 0 ? "Pass count held" : `${Math.abs(accepted)} ${accepted < 0 ? "fewer" : "more"} passed with the lever`;
  if (change == null) {
    const zeroPasses = !d.control.accepted || !d.candidate.accepted;
    return ["No cost verdict", `${quality}. ${zeroPasses ? "At least one lane had no passes." : "There is no comparable baseline unit cost."}`, "warn"];
  }
  const cost = change === 0 ? "Unit cost unchanged at displayed precision" : `Unit cost ${comparison(change)}`;
  return accepted < 0 ? ["Quality fell", `${cost}; ${quality}.`, "miss"]
    : change < 0 ? ["Worth keeping in this run", `${cost}; ${quality.toLowerCase()}.`, "pass"]
      : ["No unit-cost improvement", `${cost}. ${quality}.`, "warn"];
}

export function readout(lever, mode, run) {
  const root = $("readout");
  const [label, body, tone] = take(run);
  const evidence = run.done && !run.error && run.kpis && run.results?.length
    ? measured(lever, mode, run) : "";
  const text = `${body}${evidence ? ` ${evidence}` : ""}`;
  if (root.dataset.text === `${label}: ${text}`) return;
  root.dataset.text = `${label}: ${text}`;
  root.className = `readout ${tone}`;
  root.replaceChildren(el("strong", label, "eyebrow"), el("p", text));
}
