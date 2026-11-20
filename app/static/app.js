import { byId as $, el, inspect, ist, json, layout, metrics, rows, scoreboard, usd } from "./components/render.js";

const state = { config: null, status: null, active: null, lever: "prompt_caching", tabs: {}, mode: {}, count: {},
  runs: {}, cache: {}, starting: false };
const modes = () => state.config.tabs[state.lever]?.modes;
const tab = () => state.config.tabs[state.lever];
const current = () => state.runs[`${state.lever}/${state.mode[state.lever]}`] || {};
const source = () => current().source || state.status?.mode;
const setOptions = (select, values, active) => {
  select.replaceChildren(...values.map(([value, name]) => {
    const option = el("option", name);
    option.value = value;
    return option;
  }));
  select.value = active;
};
let toastTimer;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5300);
}

async function api(url, opts = {}) {
  const response = await fetch(url, { cache: "no-store", ...opts });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function access() {
  const existing = sessionStorage.getItem("demo-access");
  if (!state.config.access_code_required || existing) return Promise.resolve(existing);
  const modal = $("access"), input = $("access-code");
  input.value = "";
  modal.returnValue = "";
  return new Promise(resolve => {
    modal.addEventListener("close", () => {
      const code = modal.returnValue === "allow" ? input.value : null;
      input.value = "";
      if (code) sessionStorage.setItem("demo-access", code);
      resolve(code);
    }, { once: true });
    modal.showModal();
  });
}

async function post(path, body, protectedAction = false) {
  const code = protectedAction ? await access() : null;
  if (protectedAction && state.config.access_code_required && !code) return null;
  try {
    return await api(path, { method: "POST", headers: { "Content-Type": "application/json",
      ...(code ? { "X-Demo-Code": code } : {}) }, body: JSON.stringify(body) });
  } catch (error) {
    if (error.status === 401) sessionStorage.removeItem("demo-access");
    throw error;
  }
}

function provenance() {
  const run = current(), status = state.status;
  if (source() === "replay") return run.recorded_at
    ? `REPLAY · recorded ${ist(run.recorded_at)}${run.recording_source === "fake" ? " · FAKE DATA" : ""}` : "REPLAY";
  if (source() === "replay-only") return "REPLAY ONLY";
  if (source() === "fake") return "FAKE DATA";
  if (status?.foundry === "ok") return "Foundry live";
  return status ? "Foundry offline" : "Connecting…";
}

function statusUI() {
  const status = state.status;
  const pill = $("status");
  const live = source() === "live" && status?.foundry === "ok";
  pill.textContent = provenance();
  pill.className = "pill " + (source()?.startsWith("replay") ? "replay" : source() === "fake" ? "fake" : live ? "live" : "offline");
  pill.title = status?.detail || "";
  const messages = [];
  if (status?.mode === "live" && status.foundry !== "ok") messages.push([status.detail, "miss"]);
  if (source() !== "fake" && current().recording_source !== "fake"
      && (current().prices_verified ?? status?.prices_verified) === false) {
    messages.push(["Prices not verified: fill config/pricing.json", "warn"]);
  }
  if (status?.missing_prices.length || current().results?.some(result => result.status === "price_missing")) {
    messages.push([`Price missing: totals and savings are incomplete${status?.missing_prices.length
      ? " · " + status.missing_prices.join(", ") : ""}.`, "miss"]);
  }
  if (source() === "fake") messages.push(["FAKE DATA · synthetic usage, latency, answers and fixture prices. No Azure calls.", "muted"]);
  $("banners").replaceChildren(...messages.map(([text, tone]) => el("p", text, `banner ${tone}`)));
}

function buttons() {
  const playing = Boolean(state.active || state.starting), data = tab();
  const isReplayOnly = state.config.mode === "replay-only";
  $("count").disabled = playing || isReplayOnly;
  $("mode").disabled = playing;
  $("run").disabled = playing || isReplayOnly || (state.config.mode === "live" && state.status?.foundry !== "ok");
  $("run").textContent = state.lever === "retry_killswitch"
    ? `Run ${state.count[state.lever]} documents` : `Run ${state.count[state.lever]} requests`;
  $("replay").disabled = playing || !data?.recordings?.includes(state.mode[state.lever]);
  $("howto-btn").disabled = state.lever === "scoreboard";
}

function cachePanel(run) {
  const root = $("cache-panel");
  const visible = state.lever === "response_cache";
  $("comparison").classList.toggle("has-cache", visible);
  root.hidden = !visible;
  if (!visible) return;
  const key = state.mode.response_cache, local = state.cache[key] || {};
  if (!root.firstChild) {
    const form = el("form"), label = el("label", "Ask a question against the cache"), input = el("input");
    input.id = "follow-up";
    input.placeholder = "Synthetic demo questions only";
    input.required = true;
    input.maxLength = 500;
    label.append(input);
    const ask = el("button", "Ask · not graded");
    ask.type = "submit";
    form.append(label, ask);
    form.onsubmit = async event => {
      event.preventDefault();
      const mode = state.mode.response_cache;
      try {
        const result = await post("/api/cache/ask", { mode, question: input.value }, true);
        if (result) {
          state.cache[mode] = result;
          cachePanel(current());
        }
      } catch (error) { toast(error.message); }
    };
    const clear = el("button", "Clear cache");
    clear.id = "clear-cache";
    clear.type = "button";
    clear.onclick = async () => {
      try {
        const reply = await post("/api/cache/clear", {}, true);
        if (reply) {
          state.cache = { fact_guarded: { entries: [] }, naive: { entries: [] } };
          cachePanel(current());
          toast("Response cache cleared.");
        }
      } catch (error) { toast(error.message); }
    };
    const entries = el("div"); entries.id = "cache-entries";
    const response = el("p", null, "muted"); response.id = "follow-up-result";
    root.append(el("h2", "Cache entries"), clear, entries, form, response);
  }
  const frozen = state.active || state.starting || state.config.mode === "replay-only" || run.source === "replay";
  $("clear-cache").disabled = Boolean(frozen);
  $("follow-up").disabled = Boolean(frozen);
  root.querySelector("button[type=submit]").disabled = Boolean(frozen);
  const entries = local.entries || run.entries || [];
  $("cache-entries").replaceChildren(...(entries.length ? entries.map(entry => {
    const item = el("article");
    item.append(el("span", `${entry.id} · ${entry.key}`, "chip"), el("p", entry.question),
      el("p", entry.answer), el("small", `${entry.hits} hits`));
    return item;
  }) : [el("p", "Cache is empty.", "muted")]));
  $("follow-up-result").textContent = local.answer
    ? `${local.cache_action} · ${local.key || "no key"} · ${usd(local.cost_usd, 5)} · not graded — ${local.answer}` : "";
}

function renderRun() {
  const run = current();
  const count = run.runCount || state.count[state.lever];
  metrics(state.lever, run.kpis, count);
  rows(tab(), state.lever, count, run.results || [], (row, lane) => inspect(row, lane,
    state.config.prefix_layout?.prefix || 0, provenance()));
  $("progress").textContent = run.results ? `${run.results.length}/${2 * count} lane results`
    + (run.done ? " · complete" : " · streaming") : "";
  cachePanel(run);
  buttons();
  statusUI();
}

async function selectTab(key) {
  state.lever = key;
  for (const [lever, button] of Object.entries(state.tabs)) {
    if (lever === key) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  const isBoard = key === "scoreboard";
  $("bench").hidden = isBoard;
  $("scoreboard").hidden = !isBoard;
  $("title").textContent = isBoard ? "Scoreboard" : tab().title;
  $("subtitle").textContent = isBoard ? "Compare the cost of accepted outcomes." : tab().subtitle;
  $("eyebrow").textContent = `0${Object.keys(state.tabs).indexOf(key) + 1} / BENCHMARK · CONTOSO CLOUDDESK`;
  $("howto-btn").disabled = isBoard;
  if (isBoard) {
    statusUI();
    try { scoreboard($("scoreboard"), (await api("/api/scoreboard")).runs, state.config.tabs); }
    catch (error) { toast(error.message); }
    return;
  }
  setOptions($("mode"), Object.entries(modes()), state.mode[key]);
  setOptions($("count"), Array.from({ length: tab().max - tab().min + 1 }, (_, i) =>
    [tab().min + i, tab().min + i]), state.count[key]);
  $("count-label").textContent = key === "retry_killswitch" ? "Documents" : "Requests";
  layout(key === "prompt_caching" ? state.config.prefix_layout : null);
  renderRun();
}

function connect(id, key, run, lever, mode) {
  const events = new EventSource(`/api/runs/${id}/events`);
  const isShown = () => key === `${state.lever}/${state.mode[state.lever]}`;
  const on = (name, callback) => events.addEventListener(name, event => {
    callback(JSON.parse(event.data));
    if (isShown()) renderRun();
  });
  function finish() {
    events.close();
    state.active = null;
    run.done = true;
    buttons();
    if (state.lever === "scoreboard") selectTab("scoreboard");
    checkStatus();
  }
  on("run_started", data => Object.assign(run, data, { runCount: data.count }));
  on("lane_result", data => {
    run.results.push(data);
    if (data.lever_detail?.entries) run.entries = data.lever_detail.entries;
  });
  on("kpis", data => { run.kpis = data; });
  on("run_completed", data => {
    run.kpis = data.summary;
    const saved = state.config.tabs[lever].recordings;
    if (data.saved_replay && !saved.includes(mode)) saved.push(mode);
    finish();
  });
  on("run_error", data => {
    toast(data.message);
    if (data.fatal) finish();
  });
  events.onerror = async () => {
    if (run.done) return;
    try {
      const snapshot = await api(`/api/runs/${id}`);
      if (snapshot.done) {
        run.results = snapshot.results;
        run.kpis = snapshot.summary;
        finish();
        if (isShown()) renderRun();
      }
    } catch (error) {
      if (error.status === 404) { toast(error.message); finish(); }
    }
  };
}

async function start(replay = false) {
  if (state.active || state.starting || state.lever === "scoreboard" || !state.config) return;
  if ((replay ? $("replay") : $("run")).disabled) return;
  const lever = state.lever, mode = state.mode[lever], count = state.count[lever];
  const key = `${lever}/${mode}`;
  state.starting = true;
  buttons();
  try {
    const result = await (replay ? post(`/api/replays/${lever}/${mode}`, {})
      : post("/api/runs", { lever, mode, count }, true));
    if (!result) return;
    state.active = result.run_id;
    const run = { results: [], runCount: count, done: false };
    state.runs[key] = run;
    if (lever === "response_cache") delete state.cache[mode];
    connect(result.run_id, key, run, lever, mode);
    if (state.lever === lever) renderRun();
  } catch (error) {
    toast(error.message);
  } finally {
    state.starting = false;
    buttons();
  }
}

async function howto() {
  if (state.lever === "scoreboard" || !state.config) return;
  try {
    const snippet = await api(`/static/howto/${state.lever}-${state.mode[state.lever]}.json`);
    const grid = el("div", null, "diff");
    for (const [label, code] of [["Control", snippet.control], ["Candidate", snippet.candidate]]) {
      const column = el("div");
      column.append(el("h3", label), el("pre", json(code)));
      grid.append(column);
    }
    const body = $("howto-body");
    body.replaceChildren(el("p", provenance(), "pill"), el("p", snippet.note || ""), grid);
    if (snippet.shared) {
      const details = el("details");
      details.append(el("summary", "Entra-authenticated Foundry clients"), el("pre", snippet.shared));
      body.append(details);
    }
    $("howto").showModal();
  } catch (error) { toast(error.message); }
}

async function checkStatus() {
  try {
    const code = sessionStorage.getItem("demo-access");
    state.status = await api("/api/status", { headers: code ? { "X-Demo-Code": code } : {} });
  } catch {
    state.status = { foundry: "error", mode: "live", detail: "Unable to reach the app.", missing_prices: [] };
  }
  statusUI();
  if (state.config) buttons();
}

function toggle(key, button, on) {
  const root = document.documentElement;
  if (root.dataset[key] === on) delete root.dataset[key];
  else root.dataset[key] = on;
  $(button).setAttribute("aria-pressed", String(root.dataset[key] === on));
  localStorage.setItem(key, root.dataset[key] || "");
}

$("dark").onclick = () => toggle("theme", "dark", "dark");
$("large").onclick = () => toggle("large", "large", "true");
$("howto-btn").onclick = howto;
$("mode").onchange = e => { state.mode[state.lever] = e.target.value; renderRun(); };
$("count").onchange = e => {
  state.count[state.lever] = Number(e.target.value);
  delete state.runs[`${state.lever}/${state.mode[state.lever]}`];
  renderRun();
};
$("run-form").onsubmit = e => { e.preventDefault(); start(); };
$("replay").onclick = () => start(true);
document.addEventListener("keydown", event => {
  if (event.ctrlKey || event.altKey || event.metaKey || event.repeat || document.querySelector("dialog[open]")
      || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
  const key = event.key.toLowerCase(), target = Object.keys(state.tabs)[Number(key) - 1];
  if (target) { event.preventDefault(); selectTab(target); }
  if (key === "r") start();
  if (key === "p") start(true);
  if (key === "h") howto();
  if (key === "d") $("dark").click();
  if (key === "+") $("large").click();
});
for (const [key, button] of [["theme", "dark"], ["large", "large"]]) {
  const value = localStorage.getItem(key);
  if (value) {
    document.documentElement.dataset[key] = value;
    $(button).setAttribute("aria-pressed", "true");
  }
}

await checkStatus();
try {
  state.config = await api("/api/config");
  for (const [key, data] of Object.entries({ ...state.config.tabs, scoreboard: { title: "Scoreboard" } })) {
    const button = el("button", data.title);
    button.type = "button";
    button.onclick = () => selectTab(key);
    state.tabs[key] = button;
    $("tabs").append(button);
    if (data.modes) {
      state.mode[key] = Object.keys(data.modes)[0];
      state.count[key] = data.default;
    }
  }
  await selectTab("prompt_caching");
  setInterval(checkStatus, 5000);
} catch (error) { toast(error.message); }
