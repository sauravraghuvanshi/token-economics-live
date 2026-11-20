export const byId = id => document.getElementById(id);
export const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== null && text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
export const num = n => Number(n).toLocaleString("en-IN");
export const usd = (x, places = 4) => x == null ? "—" : `$${Number(x).toFixed(places)}`;
export const pct = x => x == null ? "—" : `${Number(x).toFixed(1)}%`;
export const latency = n => n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n} ms`;
export const ist = value => new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "medium",
}).format(new Date(value)) + " IST";
export const json = x => typeof x === "string" ? x : JSON.stringify(x, null, 2);

const svg = (tag, props) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  return node;
};
const tokenLabels = { uncached: "Uncached input", cache_write: "Cache write", cached: "Cached read", completion: "Output" };
export function bar(tokens, max, labels = tokenLabels) {
  const root = svg("svg", { class: "token-stack", width: "100%", viewBox: `0 0 ${Math.max(max, 1)} 12`,
    preserveAspectRatio: "none", role: "img", "aria-label": Object.keys(labels)
      .map(key => `${labels[key]} ${num(tokens[key] || 0)} tokens`).join(", ") });
  let x = 0;
  for (const key of Object.keys(labels)) {
    const count = tokens[key] || 0;
    if (count) {
      const rect = svg("rect", { x, y: 0, width: count, height: 12, class: key });
      rect.append(svg("title", {}));
      rect.firstChild.textContent = `${labels[key]} · ${num(count)} tokens`;
      root.append(rect);
      x += count;
    }
  }
  return root;
}

export function layout(data) {
  const root = byId("layout");
  root.replaceChildren();
  if (!data) return;
  root.append(el("small", "Prompt layout · estimated tokens, not billing"));
  const grid = el("div", null, "prompt-grid");
  const sizes = { uncached: data.system, cached: data.policies, completion: data.customer };
  const title = { uncached: "System", cached: "Policies", completion: "Customer" };
  for (const [lane, order] of [["Control", ["uncached", "completion", "cached"]],
    ["Candidate", ["uncached", "cached", "completion"]]]) {
    const child = el("div");
    child.append(bar(sizes, data.system + data.policies + data.customer,
      Object.fromEntries(order.map(key => [key, title[key]]))));
    child.append(el("small", `${lane} · ${order.map(key => `${title[key]} (~${num(sizes[key])})`
      + (key === "cached" && lane === "Candidate" ? " · CACHED PREFIX" : "")).join(" → ")}`));
    grid.append(child);
  }
  grid.title = "Candidate request 1 usually writes the prefix; later requests read it. "
    + "The control may write a different suffix on every call in implicit mode.";
  root.append(grid, el("small", "Row legend: Uncached input (blue) · Cache write (purple) · Cached read (green) · Output (orange)"));
}

export function metrics(lever, data, count) {
  const c = data?.control || {}, a = data?.candidate || {};
  const accepted = lane => `${lane.accepted ?? "—"}/${count}`;
  const pair = (x, y, fmt) => `${fmt(x)} → ${fmt(y)}`;
  const savings = data?.savings_pct == null ? "—"
    : `${pct(Math.abs(data.savings_pct))} ${Number(data.savings_pct) < 0 ? "higher" : "lower"}`;
  const change = data?.cpa_change_pct == null ? "—"
    : `${Number(data.cpa_change_pct) > 0 ? "+" : ""}${pct(data.cpa_change_pct)}`;
  const total = pair(c.cost_usd, a.cost_usd, x => usd(x));
  const pass = `${accepted(c)} → ${accepted(a)}`;
  const calls = value => value == null ? "—" : num(value);
  const entries = {
    prompt_caching: [
      ["Total cost", total, "Control → candidate"], ["Savings", savings, "", data?.savings_pct],
      ["Cached input share", pair(c.cached_share_pct, a.cached_share_pct, pct), "Control → candidate"],
      ["Accepted", pass, "Control → candidate"],
    ],
    model_routing: [
      ["Control cost / accepted", usd(c.cpa_usd, 5), `${accepted(c)} pass`],
      ["Candidate cost / accepted", usd(a.cpa_usd, 5), `${accepted(a)} pass`],
      ["Cost / accepted change", change, "Candidate vs control", data?.cpa_change_pct == null ? null : -data.cpa_change_pct],
      ["Route mix", Object.entries(a.route_mix || {}).map(([tier, n]) => `${n} ${tier}`).join(" / ") || "—", `${accepted(a)} pass`],
    ],
    response_cache: [
      ["Model calls", pair(c.model_calls, a.model_calls, calls), "Answer model only"],
      ["Cost savings", savings, "Includes embeddings", data?.savings_pct],
      ["Accuracy", accepted(a), `Control ${accepted(c)} pass`],
      ["Avg. latency", pair(c.avg_latency_ms, a.avg_latency_ms, latency), "Control → candidate"],
    ],
    retry_killswitch: [
      ["Role LLM calls", pair(c.calls, a.calls, calls), "Extractor + reviewer"],
      ["Cost / accepted change", change, "Candidate vs control", data?.cpa_change_pct == null ? null : -data.cpa_change_pct],
      ["Wasted token share", pair(c.wasted_token_share_pct, a.wasted_token_share_pct, pct), "Repeated evidence only"],
      ["Accepted documents", pass, "Control → candidate"],
    ],
  }[lever];
  if (!entries) return;
  const root = byId("kpis"), old = [...root.querySelectorAll(".metric-value")].map(node => node.textContent);
  root.replaceChildren(...entries.map(([label, value, sub, sign], i) => {
    const card = el("article", null, "metric");
    const valueNode = el("p", value, "metric-value" + (value !== old[i] ? " changed" : "")
      + (sign > 0 ? " pass" : sign < 0 ? " miss" : ""));
    card.append(el("p", label, "metric-label"), valueNode,
      el("p", data?.complete === false && value === "—" ? "Incomplete" : sub || "USD list prices", "metric-sub"));
    return card;
  }));
}

function laneCard(row, name, scale, lever, openInspect) {
  const cell = el("div", null, "lane");
  if (!row) {
    cell.append(el("p", "Awaiting result", "muted"));
    return cell;
  }
  const { lever_detail: d, totals: t } = row;
  const models = [...new Set(row.calls.map(c => c.response_model || c.deployment).filter(Boolean))];
  if (d.answered_by && !models.includes(d.answered_by)) models.unshift(d.answered_by);
  const head = el("div", null, "lane-head");
  const chip = el("span", models.join(" · ") || (d.cache_action === "hit" ? "Model skipped" : name), "chip");
  const cost = row.status === "price_missing" ? "price missing" : usd(t.cost_usd, 5);
  const badge = el("strong", row.status === "error" ? "Error" : row.pass ? "✓ Pass" : "✕ Miss",
    row.status !== "error" && row.pass ? "pass" : "miss");
  const inspect = el("button", "Inspect");
  inspect.type = "button";
  inspect.setAttribute("aria-label", `Inspect ${row.request_id} ${name}`);
  inspect.onclick = () => openInspect(row, name);
  head.append(chip, el("strong", cost), badge, inspect);
  cell.append(head);
  if (lever === "prompt_caching") cell.append(bar(t.tokens, scale));
  if (lever === "retry_killswitch" && d.attempts) {
    cell.append(el("p", d.attempts.map(a => `Attempt ${a.number} ${a.errors.length ? "✕" : "✓"}`)
      .concat(d.stop, d.outcome === "handoff" ? "Handoff" : []).join(" → ")));
    cell.append(el("small", `${row.calls.filter(c => c.kind === "chat" || c.kind === "decision").length} model calls`
      + ` · ${latency(t.latency_ms)} · FX tool simulated`));
  } else {
    const usage = [t.tokens.uncached && `${num(t.tokens.uncached)} uncached`,
      t.tokens.cache_write && `${num(t.tokens.cache_write)} written`, t.tokens.cached && `${num(t.tokens.cached)} cached`,
      t.tokens.completion && `${num(t.tokens.completion)} output`, t.tokens.reasoning && `${num(t.tokens.reasoning)} reasoning`]
      .filter(Boolean).join(" · ");
    cell.append(el("small", `${usage || "0 model tokens"} · ${latency(t.latency_ms)}`));
  }
  if (d.routing_fee_usd != null) cell.append(el("p", `Routing fee ${usd(d.routing_fee_usd, 5)}`));
  if (d.confidence != null) cell.append(el("p", `${d.tier} · ${pct(d.confidence * 100)} conf · `
    + `${num(d.classified_tokens)} tok classified · ${usd(d.classification_cost_usd, 5)}`));
  if (d.cache_action) {
    const text = d.cache_action === "hit"
      ? d.wrong_hit ? `Wrong answer from cache ${d.entry} · sim ${d.similarity?.toFixed(2) ?? "exact"}`
        : `From cache ${d.entry} · model skipped`
      : d.cache_action === "store" ? `Model used → stored ${d.entry}`
        : d.cache_action === "bypass" ? "Bypassed: no single exact pattern" : "Model used · cache miss";
    cell.append(el("p", text, d.wrong_hit ? "miss" : d.cache_action === "hit" ? "pass" : "muted"));
  }
  if (row.miss_reasons.length) {
    const reasons = row.miss_reasons.join(" · ");
    const reason = el("p", reasons, "reason");
    reason.title = reasons;
    cell.append(reason);
  }
  return cell;
}

export function rows(config, lever, count, results, openInspect) {
  const index = new Map(results.map(row => [`${row.request_id}/${row.lane}`, row]));
  const max = Math.max(1, ...results.map(row => row.totals.tokens.prompt + row.totals.tokens.completion));
  const root = byId("requests");
  // Keep the focused Inspect button usable while sibling results arrive.
  const focused = document.activeElement?.getAttribute("aria-label");
  root.replaceChildren(...config.requests.slice(0, count).map((request, i) => {
    const card = el("article", null, "request-card");
    const header = el("div", null, "request-head");
    const question = el("p", request.text, "prompt-text");
    question.title = request.text;
    question.lang = request.language || "en";
    header.append(el("span", String(i + 1).padStart(2, "0"), "chip"), question);
    if (request.category) header.append(el("span", request.category, "chip"));
    if (request.expected_tier) header.append(el("small", `Expected: ${request.expected_tier}`));
    const lanes = el("div", null, "lane-grid");
    for (const name of ["control", "candidate"]) lanes.append(laneCard(index.get(`${request.id}/${name}`), name,
      max, lever, openInspect));
    card.append(header, lanes);
    return card;
  }));
  if (focused && focused.startsWith("Inspect ")) {
    const restore = [...root.querySelectorAll("button")].find(node => node.getAttribute("aria-label") === focused);
    restore?.focus({ preventScroll: true });
  }
}

function details(title, text) {
  const node = el("details");
  node.append(el("summary", title), el("pre", json(text)));
  return node;
}

export function inspect(row, lane, prefixCount, sourceLabel) {
  const root = byId("inspect-body");
  byId("inspect-title").textContent = `${row.request_id} · ${lane}`;
  root.replaceChildren(el("p", `${sourceLabel} · ${row.pass ? "Pass" : "Miss"} · ${usd(row.totals.cost_usd, 5)}`),
    el("p", row.answer_preview || "No answer returned."),
    el("p", row.miss_reasons.join(" · "), "miss"));
  for (const [i, call] of row.calls.entries()) {
    const article = el("article");
    article.append(el("h3", `${i + 1}. ${call.kind} · ${call.response_model || call.deployment || "simulated/local"}`));
    for (const message of call.sent?.messages || []) {
      if (Array.isArray(message.content)) {
        article.append(details(`${message.role} · cached prefix (~${num(prefixCount)} tok estimated)`, message.content));
      } else if (message.content.includes("\n\n## Policies\n\n")) {
        const [dynamic, policy] = message.content.split("\n\n## Policies\n\n");
        article.append(details(`${message.role} · variable text`, dynamic),
          details("Policy corpus (not a stable prefix in this layout)", `## Policies\n\n${policy}`));
      } else article.append(details(message.role, message.content));
    }
    if (call.sent) article.append(details("Exact request body and parameters", {
      model: call.deployment, ...Object.fromEntries(Object.entries(call.sent).filter(([k]) => k !== "extra_body")),
      ...call.sent.extra_body,
    }));
    if (call.output != null) article.append(details("Response", call.output));
    if (call.raw_usage) article.append(details("Raw usage", call.raw_usage));
    for (const item of call.calculation || []) {
      article.append(el("p", `${item.label}: ${num(item.tokens)} × ${item.per_1m_usd ?? "price missing"} / 1,000,000`
        + ` = ${item.cost_usd == null ? "price missing" : `$${item.cost_usd}`}`));
    }
    if (call.error) article.append(el("p", call.error, "miss"));
    if (call.x_request_id) article.append(el("small", `Request ${call.x_request_id} · transport retries ${call.transport_retries}`));
    root.append(article);
  }
  if (row.lever_detail.attempts) root.append(details("Evidence and handoff", row.lever_detail));
  if (row.calls.some(call => call.sent?.extra_body?.prompt_cache_key)) {
    root.append(el("p", "Cache hits may fall when more than about 15 requests per minute share one prefix and key.", "muted"));
  }
  byId("inspect").showModal();
}

export function scoreboard(root, runs, tabs) {
  root.replaceChildren();
  if (!runs.length) {
    root.append(el("p", "Complete a run or play a recording to populate the Scoreboard.", "muted"));
    return;
  }
  const group = el("div", null, "table-wrap"), table = el("table"), head = el("thead"), tr = el("tr");
  for (const label of ["Lever / mode", "N", "Cost C → A", "Savings", "Accepted C → A", "CPA C → A",
    "CPA change", "Latency C → A", "Time / source"]) {
    const th = el("th", label);
    th.scope = "col";
    tr.append(th);
  }
  head.append(tr);
  table.append(head);
  const tbody = el("tbody");
  const label = r => `${tabs[r.lever]?.title || r.lever} / ${tabs[r.lever]?.modes[r.mode] || r.mode}`;
  for (const r of runs) {
    const row = el("tr");
    const c = r.summary.control, a = r.summary.candidate;
    const values = [label(r), r.count, `${usd(c.cost_usd)} → ${usd(a.cost_usd)}`, pct(r.summary.savings_pct),
      `${c.accepted} → ${a.accepted}`, `${usd(c.cpa_usd, 5)} → ${usd(a.cpa_usd, 5)}`, pct(r.summary.cpa_change_pct),
      `${latency(c.avg_latency_ms)} → ${latency(a.avg_latency_ms)}`];
    for (const value of values) row.append(el("td", value));
    const time = el("td");
    time.append(el("div", ist(r.recorded_at || r.started_at)),
      el("span", r.source === "replay" ? `REPLAY · ${r.results_source.toUpperCase()} recording` : r.source.toUpperCase(),
        `pill ${r.source}`));
    row.append(time);
    tbody.append(row);
  }
  table.append(tbody);
  group.append(table);
  root.append(group);
  const card = el("section", null, "projection");
  const controls = el("div", null, "controls");
  const pick = el("select"), quantity = el("input"), price = el("input"), output = el("p");
  quantity.type = price.type = "number";
  quantity.min = price.min = "0";
  quantity.value = "1000";
  price.value = "25";
  price.step = "0.01";
  output.setAttribute("aria-live", "polite");
  runs.forEach((run, i) => {
    const option = el("option", `${label(run)} · ${run.source.toUpperCase()}`);
    option.value = i;
    pick.append(option);
  });
  for (const [title, control] of [["Run", pick], ["Requests per power user per month · your assumption", quantity],
    ["Seat price, USD per month · illustrative", price]]) {
    const labelNode = el("label", title);
    labelNode.append(control);
    controls.append(labelNode);
  }
  const update = () => {
    const r = runs[Number(pick.value)], n = Number(quantity.value), seat = Number(price.value);
    const project = lane => r.summary[lane].cpa_usd == null || n < 0 || !Number.isFinite(n)
      ? "n/a" : usd(Number(r.summary[lane].cpa_usd) * n, 2)
        + (seat > 0 ? ` (${pct(Number(r.summary[lane].cpa_usd) * n * 100 / seat)} of seat price)` : "");
    output.textContent = `Control ${project("control")} → Candidate ${project("candidate")}`;
  };
  card.append(el("h2", "Power-user month"), el("p", "Projection from one live run, not a forecast."),
    el("small", "Fake and recorded runs remain explicitly labelled when used as inputs."), controls, output);
  for (const node of [pick, quantity, price]) node.addEventListener("input", update);
  update();
  root.append(card);
}
