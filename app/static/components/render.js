import { laneLabels } from "./briefs.js";
import { unitCost } from "./evidence.js";
import { byId as $, comparison, el, ist, json, latency, num, pct, pill, sourceText, usd } from "./format.js";

const svg = (tag, props) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  return node;
};
const tokenLabels = { uncached: "Uncached input", cache_write: "Cache write", cached: "Cached read", completion: "Output" };
function bar(tokens, max, labels = tokenLabels) {
  const root = svg("svg", { class: "token-stack", viewBox: `0 0 ${Math.max(max, 1)} 12`,
    preserveAspectRatio: "none", role: "img", "aria-label": Object.keys(labels)
      .map(key => `${labels[key]} ${num(tokens[key] || 0)} tokens`).join(", ") });
  let x = 0;
  for (const [key, label] of Object.entries(labels)) {
    const count = tokens[key] || 0;
    if (!count) continue;
    const rect = svg("rect", { x, y: 0, width: count, height: 12, class: key });
    const title = svg("title", {});
    title.textContent = `${label} · ${num(count)} tokens`;
    rect.append(title);
    root.append(rect);
    x += count;
  }
  return root;
}

export function layout(data) {
  const root = $("layout");
  root.replaceChildren();
  if (!data) return;
  const top = el("div", null, "layout-top");
  top.append(el("strong", "Same text, different order"), el("small", "Prompt layout · estimated tokens, not billing"));
  const grid = el("div", null, "prompt-grid");
  for (const [lane, sizes, labels, description] of [
    ["control", { uncached: data.system, variable: data.customer, policies: data.policies },
      { uncached: "Instructions", variable: "Question", policies: "Policies" },
      `Instructions → question (~${num(data.customer)}) → policies (~${num(data.policies)})`],
    ["candidate", { stable: data.prefix, variable: data.customer },
      { stable: "Stable prefix (instructions and policies)", variable: "Question" },
      `Stable prefix (~${num(data.prefix)}) → question (~${num(data.customer)})`],
  ]) {
    const child = el("div", null, "prompt-cell");
    const note = el("small");
    note.append(el("b", laneLabels[lane]), ` · ${description}`);
    const stack = bar(sizes, Object.values(sizes).reduce((a, b) => a + b, 0), labels);
    for (const node of stack.querySelectorAll("rect")) {
      if (node.classList.contains("stable")) node.classList.add("stable-block");
      if (node.classList.contains("policies")) node.classList.add("policy-block");
      if (node.classList.contains("variable")) node.classList.add("variable-block");
    }
    child.append(stack, note);
    grid.append(child);
  }
  const legend = el("div", null, "token-legend");
  legend.append(el("b", "Usage bars"));
  for (const [key, label] of Object.entries(tokenLabels)) {
    const item = el("span");
    const swatch = el("i", null, `swatch ${key}`);
    swatch.setAttribute("aria-hidden", "true");
    item.append(swatch, label);
    legend.append(item);
  }
  root.append(top, grid, legend);
}

export function agentNames(values) {
  return Array.isArray(values) ? [...new Set(values.filter(name => typeof name === "string" && name.trim()).map(name => name.trim()))] : [];
}

export function agentLine(label, values) {
  const names = agentNames(values);
  if (!names.length) return null;
  const section = el("div", null, "agent-list");
  section.append(el("small", label), ...names.map(name => el("code", name)));
  return section;
}

function reasons(row) {
  return (row.miss_reasons || []).map(reason => {
    if (reason === "unparseable") return "The answer was not in the required format.";
    if (reason.startsWith("missing policy: ")) return reason.replace("missing policy: ", "Missing policy citation: ");
    if (reason.startsWith("required: ")) return `Required detail not found: ${reason.slice(10).replace(/^\\b(.*)\\b$/, "$1")}`;
    if (reason.startsWith("forbidden: ")) return reason.replace("forbidden: ", "Included a detail the check disallows: ");
    if (reason.startsWith("unexpected decision: ")) return reason.replace("unexpected decision: ", "Decision didn’t match the expected outcome: ");
    if (reason.startsWith("FX_RATE_UNAVAILABLE:")) return `The simulated currency tool has no ${reason.split(":")[1]} rate.`;
    if (reason === "TOTAL_MISMATCH") return "The invoice total did not match the arithmetic.";
    if (reason === "SUBTOTAL_MISMATCH") return "The subtotal did not match the line items.";
    if (reason.startsWith("LINE_MISMATCH:")) return `Quantity × unit price did not match the line amount (item index ${reason.split(":")[1]}).`;
    if (reason === "REVIEWER_REJECTED") return "The reviewer did not approve the extraction.";
    return reason;
  });
}

function usageText(tokens) {
  return [
    tokens.uncached && `${num(tokens.uncached)} uncached`,
    tokens.cache_write && `${num(tokens.cache_write)} written`,
    tokens.cached && `${num(tokens.cached)} cached`,
    tokens.completion && `${num(tokens.completion)} output`,
    tokens.reasoning && `${num(tokens.reasoning)} reasoning`,
  ].filter(Boolean).join(" · ") || "0 model tokens";
}

function laneCard(row, name, scale, lever, phase, openInspect) {
  const cell = el("div", null, "lane");
  cell.append(el("p", laneLabels[name], "lane-name"));
  if (!row) {
    cell.append(el("p", phase, "muted lane-pending"));
    return cell;
  }
  const { lever_detail: d = {}, totals: t, calls = [] } = row;
  const tokens = t.tokens || {};
  const cacheHit = d.cache_action === "hit";
  const actualModels = [...new Set(calls.map(c => c.response_model || c.deployment).filter(Boolean))];
  if (d.answered_by && !cacheHit && !actualModels.includes(d.answered_by)) actualModels.unshift(d.answered_by);
  const modelText = cacheHit ? "No answer model called" : actualModels.join(" · ") || "No model reported";
  const head = el("div", null, "lane-head");
  const numbers = el("div", null, "receipt-numbers");
  const cost = row.status === "price_missing" ? "Price missing" : t.cost_usd == null ? "Cost unavailable" : usd(t.cost_usd, 5);
  const badge = el("strong", row.status === "error" ? "Error" : row.pass ? "✓ Pass" : "✕ Miss",
    `receipt-grade ${row.status !== "error" && row.pass ? "pass" : "miss"}`);
  const button = el("button", "Inspect");
  button.type = "button";
  button.setAttribute("aria-label", `Inspect ${row.request_id} ${name}`);
  button.setAttribute("aria-description", laneLabels[name]);
  button.onclick = () => openInspect(row, name);
  numbers.append(el("strong", cost), badge, button);
  head.append(el("span", modelText, `receipt-model${cacheHit ? "" : " tech"}`), numbers);
  cell.append(head);
  const agents = agentLine("Foundry agent", calls.map(call => call.foundry_agent));
  if (agents && !cacheHit) cell.append(agents);
  if (lever === "prompt_caching" && row.status !== "error") cell.append(bar(tokens, scale));
  if (lever === "retry_killswitch" && d.attempts) {
    const attempts = d.attempts.map(a => `Attempt ${a.number} ${a.errors.length ? "✕" : "✓"}`);
    attempts.push(d.outcome === "accepted" ? "Accepted" : d.stop);
    if (d.outcome === "handoff") attempts.push("Handoff");
    cell.append(el("p", attempts.join(" → "), "receipt-detail attempt-chain"));
    if (d.attempts.some(a => a.arithmetic_reconciliation)) {
      cell.append(el("p", "Retry amounts checked against the printed inputs.", "receipt-detail"));
    }
    if (d.attempts.some(a => a.subtotal_from_ocr)) {
      cell.append(el("p", "Subtotal verified from the printed invoice.", "receipt-detail"));
    }
    cell.append(el("small", `${calls.filter(c => c.kind === "chat" || c.kind === "decision").length} model calls`
      + ` · ${latency(t.latency_ms)} · FX tool simulated`));
  } else {
    cell.append(el("small", `${usageText(tokens)} · ${latency(t.latency_ms)}`));
  }
  if (d.routing_fee_usd != null) cell.append(el("p", `Routing fee ${usd(d.routing_fee_usd, 5)}`, "receipt-detail"));
  if (d.confidence != null) cell.append(el("p", `${d.tier} · ${pct(d.confidence * 100)} confidence · `
    + `${num(d.classified_tokens)} tokens classified · ${usd(d.classification_cost_usd, 5)}`, "receipt-detail"));
  if (d.cache_action) {
    const text = cacheHit
      ? d.wrong_hit ? `Wrong answer from cache ${d.entry}${d.similarity == null ? "" : ` · similarity ${Number(d.similarity).toFixed(2)}`}`
        : `From cache ${d.entry} · model skipped`
      : d.cache_action === "store" ? `Model used → stored ${d.entry}`
        : d.cache_action === "bypass" ? "Bypassed: no single exact pattern" : "Model used · cache miss";
    cell.append(el("p", text, `receipt-detail ${d.wrong_hit ? "miss" : cacheHit ? "pass" : ""}`));
  }
  const messages = reasons(row).filter(reason => reason !== d.stop);
  if (messages.length) cell.append(el("p", messages.join(" · "), "reason"));
  return cell;
}

const categories = { routine: "Routine", "multi-policy": "Multi-policy", trap: "Hard case",
  "deep-reasoning": "Deep reasoning", clean: "Clean invoice", recoverable: "Recoverable", runaway: "Runaway case" };

export function rows(config, lever, count, run, openInspect) {
  const results = run.results || [];
  const index = new Map(results.map(row => [`${row.request_id}/${row.lane}`, row]));
  const max = Math.max(1, ...results.map(({ totals: { tokens = {} } }) => (tokens.prompt || 0) + (tokens.completion || 0)));
  const root = $("requests");
  const focused = document.activeElement?.getAttribute("aria-label");
  root.replaceChildren(...config.requests.slice(0, count).map((request, i) => {
    const card = el("article", null, "request-card");
    const header = el("div", null, "request-head");
    const event = run.requests?.get(request.id);
    const text = event?.text || request.text;
    const question = el("p", text, "prompt-text");
    question.title = text;
    question.lang = request.language || "en";
    header.append(el("span", String(i + 1).padStart(2, "0"), "request-index"), question);
    if (request.category || request.expected_tier) {
      const meta = el("div", null, "request-meta");
      if (request.category) meta.append(el("small", categories[request.category] || request.category));
      if (request.expected_tier) meta.append(el("small", `Expected tier: ${request.expected_tier}`));
      header.append(meta);
    }
    const lanes = el("div", null, "lane-grid");
    for (const name of Object.keys(laneLabels)) {
      const phase = !run.results ? "Not run yet" : run.done ? "No result received"
        : event?.lanes.has(name) ? (run.source === "replay" ? "Playing…" : "Running…") : "Waiting in queue";
      lanes.append(laneCard(index.get(`${request.id}/${name}`), name, max, lever, phase, row => openInspect(row, name, text)));
    }
    card.append(header, lanes);
    return card;
  }));
  if (focused?.startsWith("Inspect ")) focusReceipt(focused);
}

function focusReceipt(label) {
  [...$("requests").querySelectorAll("button")].find(node => node.getAttribute("aria-label") === label)
    ?.focus({ preventScroll: true });
}

export function details(title, text) {
  const node = el("details");
  node.append(el("summary", title), el("pre", json(text)));
  return node;
}

const kinds = { chat: "Generation", router: "Routed generation", decision: "Decision call",
  embedding: "Similarity lookup (embedding)", tool: "Simulated tool", validator: "Validation (local code)" };

export function inspect(row, lane, question, prefixCount, source, sourceLabel) {
  const modal = $("inspect");
  const root = $("inspect-body");
  $("inspect-title").textContent = `${row.request_id} · ${laneLabels[lane]}`;
  $("inspect-accessible-title").textContent = `${row.request_id} · ${lane}`;
  const intro = el("section", null, "inspect-summary");
  intro.append(pill(source, sourceLabel));
  const result = row.status === "error" ? "Run error" : row.pass ? "Passed the checks" : "Did not pass";
  const cost = row.totals.cost_usd != null ? usd(row.totals.cost_usd, 5) : "Cost unavailable";
  intro.append(el("p", question, "muted"), el("p", `${result} · ${cost} · ${latency(row.totals.latency_ms)}`),
    el("p", row.answer_preview || "No answer returned."));
  for (const reason of reasons(row)) intro.append(el("p", reason, "miss"));
  root.replaceChildren(intro);
  const calls = row.calls || [];
  if (row.lever_detail?.cache_action === "hit") {
    root.append(el("p", calls.length ? "A saved answer was returned. The answer agent was not called; any lookup calls are listed below."
      : "A saved answer was returned without any model calls."));
  }
  for (const [i, call] of calls.entries()) {
    const article = el("article", null, "inspect-call");
    const heading = el("h3", `${i + 1}. ${kinds[call.kind] || call.kind}`);
    if (call.response_model || call.deployment) heading.append(" · ", el("code", call.response_model || call.deployment));
    article.append(heading);
    const agent = agentLine("Foundry agent", [call.foundry_agent]);
    if (agent) article.append(agent);
    const addID = (label, value) => {
      if (!value) return;
      const p = el("p", `${label}: `);
      p.append(el("code", value));
      article.append(p);
    };
    addID("Agent version", call.foundry_agent_version);
    addID("Foundry response ID", call.foundry_response_id);
    const input = call.sent?.messages || call.sent?.input;
    for (const message of Array.isArray(input) ? input.filter(item => item?.role) : []) {
      const title = message.role === "system" ? "System instructions" : message.role === "user" ? "User message" : message.role;
      const text = Array.isArray(message.content) ? message.content.map(part => part.text || "").join("\n") : message.content;
      const policyMarker = "\n\n## Policies\n\n";
      if (typeof text === "string" && text.includes(policyMarker) && message.role === "system") {
        article.append(details(`${title} · stable prefix (~${num(prefixCount)} tokens estimated)`, message.content));
      } else if (typeof text === "string" && text.includes(policyMarker)) {
        const index = text.indexOf(policyMarker);
        article.append(details(`${title} · variable text`, text.slice(0, index)), details("Policy text", text.slice(index).trimStart()));
      } else article.append(details(title, message.content));
    }
    if (call.sent) article.append(details("Exact request body and parameters", call.sent.agent_reference ? call.sent : {
      model: call.deployment, ...Object.fromEntries(Object.entries(call.sent).filter(([k]) => k !== "extra_body")),
      ...call.sent.extra_body,
    }));
    if (call.output != null) article.append(details("Response", call.output));
    if (call.raw_usage != null) article.append(details("Raw usage", call.raw_usage));
    for (const item of call.calculation || []) {
      article.append(el("p", `${item.label}: ${num(item.tokens)} × ${item.per_1m_usd ?? "price missing"} / 1,000,000`
        + ` = ${item.cost_usd == null ? "price missing" : `$${item.cost_usd}`}`));
    }
    if (call.error) article.append(el("p", call.error, "miss"));
    if (call.x_request_id) addID("Service request ID", call.x_request_id);
    if (call.transport_retries != null) article.append(el("small", `Transport retries: ${call.transport_retries}`, "muted"));
    root.append(article);
  }
  if (row.lever_detail?.attempts) root.append(details("Evidence and handoff", row.lever_detail));
  if (row.miss_reasons?.length) root.append(details("Raw failed checks", row.miss_reasons));
  if (calls.some(call => call.sent?.prompt_cache_key || call.sent?.extra_body?.prompt_cache_key)) {
    root.append(el("p", "Cache hits may fall when more than about 15 requests per minute share one prefix and key.", "muted"));
  }
  modal.addEventListener("close", () => focusReceipt(`Inspect ${row.request_id} ${lane}`), { once: true });
  modal.scrollTop = 0;
  modal.showModal();
}

const modeNames = { small_all: "Small model for all", naive: "Naive similarity", decision_router: "Decision router · before policies" };
export const modeLabel = (key, name) => modeNames[key] || name;
const scoreLabel = (run, tabs) => modeLabel(run.mode, tabs[run.lever]?.modes[run.mode] || run.mode);
const scoreSource = run => sourceText(run.source, run.results_source);
const pairCell = (left, right) => {
  const grid = el("div", null, "stat-pair");
  grid.append(el("span", left), el("span", "→"), el("span", right));
  return grid;
};

function projection(runs, tabs, prefs) {
  const card = el("section", null, "projection");
  const about = el("div");
  about.append(el("span", "Illustrative seat economics", "eyebrow"), el("h2", "Power-user month"),
    el("p", "Projection from one live run, not a forecast."),
    el("p", "Multiply cost per accepted outcome by the number of successful requests you want to deliver."));
  const output = el("div", null, "projection-results");
  if (!runs.length) {
    output.append(el("p", "Complete a run or play a recording to estimate monthly cost."));
    card.append(about, output);
    return card;
  }
  const controls = el("div", null, "projection-controls"), pick = el("select"), quantity = el("input"), price = el("input");
  quantity.type = price.type = "number";
  quantity.min = price.min = "0";
  quantity.value = prefs.volume;
  price.value = prefs.seat;
  price.step = "0.01";
  output.setAttribute("aria-live", "polite");
  runs.forEach(run => {
    const option = el("option", `${tabs[run.lever]?.title || run.lever} · ${scoreLabel(run, tabs)} · ${scoreSource(run)}`);
    option.value = `${run.lever}/${run.mode}`;
    pick.append(option);
  });
  if (runs.some(run => `${run.lever}/${run.mode}` === prefs.pick)) pick.value = prefs.pick;
  for (const [title, control] of [["Run", pick], ["Requests per power user per month · your assumption", quantity],
    ["Seat price, USD per month · illustrative", price]]) {
    const label = el("label", title);
    label.append(control);
    controls.append(label);
  }
  function update() {
    Object.assign(prefs, { volume: quantity.value, seat: price.value, pick: pick.value });
    const r = runs[pick.selectedIndex], n = quantity.valueAsNumber, seat = price.valueAsNumber;
    const render = name => {
      const value = r.summary[name]?.cpa_usd;
      const valid = value != null && r.summary.complete !== false && n >= 0 && Number.isFinite(n);
      const monthly = valid ? Number(value) * n : NaN;
      const node = el("div");
      node.append(el("small", laneLabels[name]), el("strong", Number.isFinite(monthly) ? usd(monthly, 2) : "n/a"));
      if (seat > 0 && Number.isFinite(monthly / seat)) node.append(el("small", `${pct(monthly * 100 / seat)} of seat price`));
      return node;
    };
    const results = el("div", null, "stat-pair");
    results.append(render("control"), el("span", "→"), render("candidate"));
    const note = r.source === "fake" ? "Synthetic values for illustration only."
      : r.source === "replay" ? "Uses the saved values, not a new measurement." : "Model usage only; excludes hosting and human work.";
    output.replaceChildren(pill(r.source, `Source: ${scoreSource(r)}`), results, el("small", note));
    if (r.summary.candidate.accepted < r.summary.control.accepted) {
      output.append(el("small", "Fewer outcomes passed with the lever in this run.", "quality-note miss"));
    }
  }
  for (const node of [pick, quantity, price]) node.addEventListener("input", update);
  about.append(controls);
  card.append(about, output);
  update();
  return card;
}

export function scoreboard(root, runs, tabs, prefs) {
  root.replaceChildren(projection(runs, tabs, prefs));
  if (!runs.length) {
    root.append(el("p", "No results yet. Each mode will show its latest completed run here.", "empty-note"));
    return;
  }
  const header = el("div", null, "section-head");
  header.append(el("h2", "Latest run by mode"), el("p", "Without the lever → With the lever · USD"));
  const ledger = el("section", null, "score-ledger");
  const group = el("div", null, "table-wrap");
  group.setAttribute("role", "region");
  group.setAttribute("aria-label", "Latest run metrics by mode");
  group.tabIndex = 0;
  const table = el("table"), head = el("thead"), tr = el("tr");
  const labels = ["Experiment / mode", "Batch", "Accepted", "Cost per accepted", "Unit-cost change",
    "Total cost", "Total-cost change", "Avg. latency", "Time / source"];
  for (const label of labels) {
    const th = el("th", label);
    th.scope = "col";
    tr.append(th);
  }
  head.append(tr);
  const tbody = el("tbody");
  for (const r of runs) {
    const row = el("tr");
    const { control: c, candidate: a } = r.summary;
    const complete = r.summary.complete !== false;
    const name = el("div", tabs[r.lever]?.title || r.lever);
    name.append(el("small", scoreLabel(r, tabs)));
    const time = el("div"), timestamp = r.recorded_at || r.started_at;
    const date = el("div", ist(timestamp, true));
    date.title = ist(timestamp);
    time.append(date, pill(r.source, scoreSource(r)));
    const money = lane => complete ? usd(lane.cost_usd) : "—";
    const quality = el("div");
    quality.append(pairCell(c.accepted, a.accepted));
    const net = a.accepted - c.accepted;
    if (net && c.done === a.done) quality.append(el("small", `${Math.abs(net)} ${net < 0 ? "fewer" : "more"} passed`, `quality-note ${net < 0 ? "miss" : "pass"}`));
    const values = [name, r.count, quality, pairCell(unitCost(c, !complete), unitCost(a, !complete)),
      complete ? comparison(r.summary.cpa_change_pct) : "Incomplete",
      pairCell(money(c), money(a)), complete ? comparison(r.summary.savings_pct, true) : "—",
      pairCell(latency(c.avg_latency_ms), latency(a.avg_latency_ms)), time];
    for (const [i, value] of values.entries()) {
      const td = el("td", null, i === 3 || i === 4 ? "score-unit" : i === 8 ? "score-source" : "");
      td.dataset.label = labels[i];
      td.append(value);
      row.append(td);
    }
    tbody.append(row);
  }
  table.append(head, tbody);
  group.append(table);
  ledger.append(header, group);
  root.append(ledger);
}
