import { experiments, laneLabels, orchestrationNote, pages, responsesExample, supportAgentNote } from "./components/briefs.js";
import { metrics, readout } from "./components/evidence.js";
import { byId as $, el, ist, json, pill, sourceText, usd } from "./components/format.js";
import { agentLine, details, inspect, layout, modeLabel, rows, scoreboard } from "./components/render.js";

const state = {
  config: null, status: null, active: null, lever: "prompt_caching", tabs: {}, mode: {}, count: {},
  runs: {}, cache: {}, starting: false, cacheWorking: false, boardReplay: false,
  projection: { volume: "1000", seat: "25", pick: "" },
};
const tab = () => state.config?.tabs[state.lever];
const currentKey = () => `${state.lever}/${state.mode[state.lever]}`;
const current = () => state.runs[currentKey()] || {};
const viewSource = () => current().source || state.status?.mode || state.config?.mode;
const replayView = () => state.active?.source === "replay" || current().source === "replay" || state.boardReplay;
const busy = () => Boolean(state.active || state.starting || state.cacheWorking);
const readStored = (storage, key) => {
  try { return window[storage].getItem(key); } catch { return null; }
};
function writeStored(storage, key, value) {
  try {
    if (value) window[storage].setItem(key, value);
    else window[storage].removeItem(key);
  } catch { /* Themes and demo access can still work without browser storage. */ }
}
let memoryCode = null;
const savedCode = () => memoryCode || readStored("sessionStorage", "demo-access");

let toastTimer;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6200);
}

async function api(url, opts = {}) {
  const response = await fetch(url, { cache: "no-store", ...opts });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const error = new Error(typeof payload?.detail === "string" ? payload.detail : `The request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function access() {
  const existing = savedCode();
  if (!state.config.access_code_required || existing) return Promise.resolve(existing);
  const modal = $("access"), input = $("access-code");
  input.value = "";
  modal.returnValue = "";
  return new Promise(resolve => {
    modal.addEventListener("close", () => {
      const code = modal.returnValue === "allow" ? input.value : null;
      input.value = "";
      if (code) {
        memoryCode = code;
        writeStored("sessionStorage", "demo-access", code);
      }
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
    if (error.status === 401) {
      memoryCode = null;
      writeStored("sessionStorage", "demo-access", null);
    }
    throw error;
  }
}

function shownSource(run) {
  if (run.source === "replay" && run.recorded_at) {
    return `REPLAY · recorded ${ist(run.recorded_at)}${run.recording_source === "fake" ? " · FAKE DATA" : ""}`;
  }
  return sourceText(run.source, run.recording_source);
}

function statusUI() {
  const status = state.status, run = current(), source = viewSource();
  let tone = source, label;
  if (source === "replay") label = shownSource(run);
  else if (source === "fake" || source === "replay-only") {
    label = sourceText(source);
    if (source === "replay-only") tone = "replay";
  } else {
    tone = status?.foundry === "ok" ? "live" : "offline";
    label = tone === "live" ? "Foundry live" : status ? "Foundry offline" : "Connecting…";
  }
  const badge = $("status");
  if (badge.textContent !== label) badge.textContent = label;
  badge.className = `pill ${tone}`;
  badge.title = source === "replay" ? "Playback of a saved run. No new model calls."
    : [status?.detail, status?.prices_verified && `List prices verified ${status.verified_on || ""}.`].filter(Boolean).join(" ");

  const messages = [];
  if (status?.unreachable || (source === "live" && status?.foundry !== "ok")) {
    messages.push(["Foundry status", status?.detail || "Checking the connection.", "miss"]);
  }
  if (source !== "fake" && run.recording_source !== "fake" && (run.prices_verified ?? status?.prices_verified) === false
      && (source !== "replay" || run.prices_verified === false)) {
    messages.push(["Pricing", "Prices not verified: fill config/pricing.json.", "warn"]);
  }
  const missing = source === "replay" ? [] : status?.missing_prices || [];
  if (missing.length || run.results?.some(result => result.status === "price_missing")) {
    messages.push(["Pricing", `Price missing: totals and savings are incomplete${missing.length ? ` · ${missing.join(", ")}` : ""}.`, "miss"]);
  }
  if (source === "fake" || run.recording_source === "fake") {
    messages.push(["Data source", "Synthetic answers, token counts, timings and test prices. No Azure calls.", "muted"]);
  }
  const banners = $("banners");
  const text = JSON.stringify(messages);
  if (banners.dataset.messages === text) return;
  banners.dataset.messages = text;
  banners.replaceChildren(...messages.map(([title, detail, css]) => {
    const p = el("p", null, `banner ${css}`);
    p.append(el("strong", `${title} · `), detail);
    return p;
  }));
}

function buttons() {
  if (!tab()) return;
  const playing = busy(), offline = state.config.mode === "live" && state.status?.foundry !== "ok";
  const isReplayOnly = state.config.mode === "replay-only";
  $("count").disabled = playing || isReplayOnly;
  $("mode").disabled = playing;
  $("run").disabled = playing || isReplayOnly || offline;
  $("run").title = isReplayOnly ? "This server only plays recordings." : offline ? "Foundry is unavailable. Try a recording." : "";
  const label = `Run ${state.count[state.lever]} ${state.lever === "retry_killswitch" ? "documents" : "requests"}`;
  if ($("run").textContent !== label) $("run").textContent = label;
  $("run").setAttribute("aria-label", label);
  const recorded = tab().recordings?.includes(state.mode[state.lever]);
  $("replay").disabled = playing || !recorded;
  $("replay").title = recorded ? "Play back the saved batch. No access code or model calls." : "This mode has no saved recording.";
}

function portalLink() {
  const value = state.config?.foundry_agents_url;
  if (!value || state.config.mode !== "live" || replayView()) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    const link = el("a", "View agents in Foundry ↗");
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch { return null; }
}

function brief() {
  const page = pages[state.lever], mode = state.mode[state.lever], experiment = experiments[mode];
  if (!experiment) return;
  const agents = current().source !== "replay" && state.config.mode === "live" ? tab().agents?.[mode] : null;
  const grid = el("div", null, "brief-grid");
  for (const [index, title, copy, lane] of [
    ["01", laneLabels.control, page.baseline, "control"],
    ["02", laneLabels.candidate, experiment.change, "candidate"],
    ["03", "What to look for", experiment.watch, null],
  ]) {
    const section = el("div", null, `brief-part${lane === "candidate" ? " lever-part" : ""}`);
    const h = el("h2");
    const n = el("i", index);
    n.setAttribute("aria-hidden", "true");
    h.append(n, title);
    if (lane === "candidate" && experiment.counterexample) h.append(el("span", "Counterexample", "tag"));
    const names = lane && agentLine("Configured Foundry agent", agents?.[lane]);
    let text = state.lever === "response_cache" && names ? copy.replace("answer model", "answer agent") : copy;
    if (lane === "control" && state.lever === "retry_killswitch") {
      text += ` Baseline ceiling: ${state.config.retry_attempts.control} attempts per invoice.`;
    }
    if (lane === "control" && state.lever === "response_cache" && state.config.prompt_cache_mode === "auto") {
      text = "Generate every answer from scratch. This deployment can still cache input tokens; check the reported usage.";
    }
    section.append(h, el("p", text));
    if (names) section.append(names);
    grid.append(section);
  }
  $("brief").replaceChildren(grid);
  $("foundry-portal").replaceChildren(...[portalLink()].filter(Boolean));
}

function cachePanel(run) {
  const root = $("cache-panel");
  const visible = state.lever === "response_cache";
  $("comparison").classList.toggle("has-cache", visible);
  root.hidden = !visible;
  if (!visible) return;
  const key = state.mode.response_cache, local = run.source === "replay" ? {} : state.cache[key] || {};
  if (!root.firstChild) {
    const form = el("form"), label = el("label", "Ask a question against the cache"), input = el("input");
    input.id = "follow-up";
    input.placeholder = "Try another wording of a demo question";
    input.required = true;
    input.maxLength = 500;
    label.append(input);
    const ask = el("button", "Ask · not graded");
    ask.type = "submit";
    form.append(label, ask);
    form.onsubmit = event => {
      event.preventDefault();
      const mode = state.mode.response_cache;
      cacheAction(async () => {
        const result = await post("/api/cache/ask", { mode, question: input.value }, true);
        if (result) state.cache[mode] = result;
      });
    };
    const clear = el("button", "Clear cache");
    clear.id = "clear-cache";
    clear.type = "button";
    clear.onclick = () => cacheAction(async () => {
      if (await post("/api/cache/clear", {}, true)) {
        state.cache = { fact_guarded: { entries: [] }, naive: { entries: [] } };
        toast("Response cache cleared.");
      }
    });
    const title = el("div", null, "cache-title");
    title.append(el("h2", "Answer cache"), clear);
    const note = el("p", null, "muted"); note.id = "cache-note";
    const entries = el("div"); entries.id = "cache-entries";
    const response = el("p"); response.id = "follow-up-result";
    response.setAttribute("role", "status");
    root.append(title, note, form, response, entries);
  }
  const frozen = state.config.mode === "replay-only" || run.source === "replay";
  const actionsBlocked = frozen || busy();
  $("clear-cache").disabled = actionsBlocked;
  $("follow-up").disabled = actionsBlocked;
  root.querySelector("button[type=submit]").disabled = actionsBlocked;
  $("cache-note").textContent = frozen ? "Recorded entries only. Follow-up and clearing are disabled during playback."
    : "Each run starts with an empty cache. A follow-up uses the current cache and may cost money.";
  const entries = local.entries || run.entries || [];
  const entryRoot = $("cache-entries");
  if (entryRoot.renderedEntries !== entries) {
    entryRoot.renderedEntries = entries;
    entryRoot.replaceChildren(...(entries.length ? entries.map(entry => {
      const item = el("article"), disclosure = el("details");
      disclosure.append(el("summary", `${entry.id} · ${entry.hits} ${entry.hits === 1 ? "hit" : "hits"}`),
        el("code", entry.key), el("p", entry.question, "muted"), el("p", entry.answer));
      item.append(disclosure);
      return item;
    }) : [el("p", "Cache is empty.", "muted")]));
  }
  $("follow-up-result").textContent = local.answer
    ? `${local.cache_action} · ${local.key || "no key"} · ${usd(local.cost_usd, 5)} · not graded — ${local.answer}` : "";
}

async function cacheAction(action) {
  if (busy() || current().source === "replay") return;
  state.cacheWorking = true;
  buttons();
  cachePanel(current());
  try { await action(); } catch (error) { toast(error.message); }
  finally {
    state.cacheWorking = false;
    buttons();
    cachePanel(current());
  }
}

function renderRun() {
  if (!tab()) return;
  const run = current(), count = run.runCount || state.count[state.lever];
  const source = $("evidence-source");
  source.hidden = !run.source;
  source.className = `pill ${run.source || ""}`;
  source.textContent = run.source === "replay" ? `Viewing ${sourceText(run.source, run.recording_source)}` : sourceText(run.source);
  metrics(state.lever, run.kpis, count);
  readout(state.lever, state.mode[state.lever], run);
  rows(tab(), state.lever, count, run, (row, lane, question) => inspect(row, lane, question,
    state.config.prefix_layout?.prefix || 0, run.source, shownSource(run)));
  const progress = run.results ? `${run.results.length}/${2 * count} lane results`
    + ` · ${run.error ? "stopped" : run.done ? "complete" : run.reconnecting ? "reconnecting" : "streaming"}`
    : "";
  if ($("progress").textContent !== progress) $("progress").textContent = progress;
  const meter = $("run-meter");
  meter.hidden = !run.results;
  meter.max = 2 * count;
  meter.value = run.results?.length || 0;
  retryMonitor(run);
  cachePanel(run);
  buttons();
  statusUI();
}

function retryMonitor(run) {
  const liveRun = state.active === run && run.source !== "replay";
  const finishedBaseline = run.results?.some(row => row.lane === "control" && row.idx === run.runCount);
  $("retry-monitor").hidden = state.lever !== "retry_killswitch" || !run.results
    || (run.done && !run.baseline_stop_requested);
  if ($("retry-monitor").hidden) return;
  const stop = $("stop-baseline");
  stop.hidden = !liveRun;
  stop.setAttribute("aria-disabled", String(Boolean(run.stopping || run.baseline_stop_requested || finishedBaseline)));
  const label = run.baseline_stop_requested ? "Stop requested" : run.stopping ? "Sending stop…"
    : finishedBaseline ? "Baseline finished" : "Stop baseline retries";
  if (stop.textContent !== label) stop.textContent = label;
  const note = run.baseline_stop_requested && run.done
    ? "This run did not replace the Scoreboard or the last recording."
    : run.baseline_stop_requested
      ? "The current attempt can finish. Later baseline invoices get one try, and the lever lane keeps running."
      : run.source === "replay" ? "Playing recorded attempts."
        : finishedBaseline ? "The baseline has finished. The lever lane is still running."
          : "Stop new retries in the baseline without interrupting the lever lane.";
  if ($("stop-note").textContent !== note) $("stop-note").textContent = note;
  for (const lane of Object.keys(laneLabels)) {
    const node = $(`retry-${lane}`), attempt = run.lastAttempts?.[lane];
    let text = "Waiting for the first invoice.";
    if (attempt) {
      const { request_id, number, limit, errors, repeated } = attempt;
      const label = number === 1 ? "Initial attempt" : `Retry ${number - 1} of ${limit - 1}`;
      const outcome = attempt.stop || (errors == null ? "running"
        : errors.length ? repeated ? "same error" : "checks failed" : "checks passed");
      node.className = errors?.length ? "warn" : errors ? "pass" : "";
      text = `${request_id.toUpperCase()} · ${label} · ${outcome}`;
    } else node.className = "muted";
    if (node.textContent !== text) node.textContent = text;
  }
}

async function stopBaseline() {
  const run = current();
  if (state.lever !== "retry_killswitch" || state.active !== run || run.source === "replay"
      || $("stop-baseline").getAttribute("aria-disabled") === "true") return;
  run.stopping = true;
  retryMonitor(run);
  try {
    const result = await post(`/api/runs/${encodeURIComponent(run.id)}/stop-baseline`, {}, true);
    if (result) run.baseline_stop_requested = result.baseline_stop_requested;
  } catch (error) { toast(error.message); }
  finally {
    run.stopping = false;
    if (run === current()) retryMonitor(run);
  }
}

const setOptions = (select, values, active) => {
  select.replaceChildren(...values.map(([value, name]) => {
    const option = el("option", name);
    option.value = value;
    return option;
  }));
  select.value = active;
};

let boardSequence = 0;
async function refreshBoard() {
  const root = $("scoreboard"), sequence = ++boardSequence;
  root.setAttribute("aria-busy", "true");
  try {
    const data = await api("/api/scoreboard");
    if (sequence === boardSequence && state.lever === "scoreboard") {
      scoreboard(root, data.runs, state.config.tabs, state.projection);
    }
  } catch (error) {
    if (sequence === boardSequence && state.lever === "scoreboard") {
      root.replaceChildren(el("p", `The Scoreboard could not load. ${error.message}`, "empty-note"));
    }
  } finally { if (sequence === boardSequence) root.removeAttribute("aria-busy"); }
}

function selectTab(key) {
  const isBoard = key === "scoreboard";
  state.boardReplay = isBoard && replayView();
  state.lever = key;
  for (const [lever, button] of Object.entries(state.tabs)) {
    if (lever === key) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  $("bench").hidden = isBoard;
  $("scoreboard").hidden = !isBoard;
  $("title").textContent = isBoard ? "Scoreboard" : tab().title;
  $("subtitle").textContent = pages[key].intro;
  const index = String(Object.keys(state.tabs).indexOf(key) + 1).padStart(2, "0");
  $("eyebrow").textContent = isBoard ? "Run history · Contoso CloudDesk" : `Experiment ${index} / 04 · Contoso CloudDesk`;
  $("howto-btn").disabled = isBoard;
  if (isBoard) {
    statusUI();
    if (!$("scoreboard").hasChildNodes()) $("scoreboard").append(el("p", "Loading the latest runs…", "empty-note"));
    refreshBoard();
    return;
  }
  setOptions($("mode"), Object.entries(tab().modes).map(([m, label]) => [m, modeLabel(m, label)]), state.mode[key]);
  setOptions($("count"), Array.from({ length: tab().max - tab().min + 1 }, (_, i) =>
    [tab().min + i, tab().min + i]), state.count[key]);
  $("count-label").textContent = key === "retry_killswitch" ? "Documents" : "Requests";
  $("receipt-note").textContent = pages[key].receipt;
  $("receipts-title").textContent = key === "retry_killswitch" ? "Invoice receipts" : "Request receipts";
  const prompt = key === "prompt_caching" ? state.config.prefix_layout : null;
  $("receipt-area").classList.toggle("has-layout", Boolean(prompt));
  layout(prompt);
  brief();
  renderRun();
}

function connect(id, key, run, lever, mode) {
  const events = new EventSource(`/api/runs/${encodeURIComponent(id)}/events`);
  const isShown = () => key === currentKey();
  let renderFrame = 0, recovering = false;
  const update = () => {
    if (!isShown() || renderFrame) return;
    renderFrame = requestAnimationFrame(() => {
      renderFrame = 0;
      if (isShown()) renderRun();
    });
  };
  const on = (name, callback) => events.addEventListener(name, event => {
    run.reconnecting = false;
    callback(JSON.parse(event.data));
    update();
  });
  function finish() {
    if (run.done) return;
    events.close();
    state.active = null;
    run.done = true;
    buttons();
    if (isShown() && document.activeElement === $("stop-baseline")) $("run").focus({ preventScroll: true });
    if (state.lever === "scoreboard") refreshBoard();
    if (run.source !== "replay") checkStatus();
    update();
  }
  on("run_started", data => Object.assign(run, data, { runCount: data.count }));
  on("request_started", data => {
    const request = run.requests.get(data.request_id) || { text: data.text, lanes: new Set() };
    request.lanes.add(data.lane);
    run.requests.set(data.request_id, request);
  });
  const trackAttempt = data => {
    run.attempts.set(`${data.request_id}/${data.lane}`, data);
    run.lastAttempts[data.lane] = data;
  };
  on("attempt_started", trackAttempt);
  on("attempt_checked", trackAttempt);
  on("baseline_stop_requested", () => { run.baseline_stop_requested = true; });
  on("lane_result", data => {
    const i = run.results.findIndex(row => row.request_id === data.request_id && row.lane === data.lane);
    if (i < 0) run.results.push(data);
    else run.results[i] = data;
    if (data.lever_detail?.entries) run.entries = data.lever_detail.entries;
    const last = run.lastAttempts[data.lane];
    if (last?.request_id === data.request_id) {
      run.lastAttempts[data.lane] = { ...last, stop: data.lever_detail?.stop || "finished" };
    }
  });
  on("kpis", data => { run.kpis = data; });
  on("run_completed", data => {
    run.kpis = data.summary;
    run.baseline_stop_requested = data.summary.baseline_stop_requested;
    const saved = state.config.tabs[lever].recordings;
    if (data.saved_replay && !saved.includes(mode)) saved.push(mode);
    finish();
  });
  on("run_error", data => {
    toast(data.message);
    if (data.fatal) {
      run.error = data.message;
      finish();
    }
  });
  events.onerror = async () => {
    if (run.done || recovering) return;
    run.reconnecting = recovering = true;
    update();
    try {
      const snapshot = await api(`/api/runs/${encodeURIComponent(id)}`);
      if (run.done || !snapshot.done) return;
      run.results = snapshot.results;
      run.kpis = snapshot.summary;
      run.runCount = snapshot.count;
      run.recorded_at = snapshot.recorded_at;
      run.recording_source = snapshot.recording_source;
      run.baseline_stop_requested = snapshot.baseline_stop_requested;
      run.entries = [...run.results].reverse().find(row => row.lever_detail?.entries)?.lever_detail.entries;
      if (!run.kpis) run.error = "The run ended before a summary could be saved.";
      finish();
    } catch (error) {
      if (!run.done && (error.status === 404 || events.readyState === EventSource.CLOSED)) {
        run.error = "The result stream could not be recovered. Try another run or a recording.";
        finish();
      }
    } finally { recovering = false; }
  };
}

async function start(replay = false) {
  if (busy() || !tab() || (replay ? $("replay") : $("run")).disabled) return;
  const lever = state.lever, mode = state.mode[lever], count = state.count[lever], key = currentKey();
  state.starting = true;
  buttons();
  cachePanel(current());
  try {
    const result = await (replay ? post(`/api/replays/${lever}/${mode}`, {})
      : post("/api/runs", { lever, mode, count }, true));
    if (!result) return;
    const run = { id: result.run_id, source: replay ? "replay" : state.config.mode, results: [],
      requests: new Map(), attempts: new Map(), lastAttempts: {}, runCount: count, done: false };
    state.active = run;
    state.runs[key] = run;
    if (lever === "response_cache" && !replay) delete state.cache[mode];
    connect(result.run_id, key, run, lever, mode);
    if (currentKey() === key) {
      brief();
      renderRun();
    }
  } catch (error) {
    toast(error.message);
  } finally {
    state.starting = false;
    buttons();
    cachePanel(current());
  }
}

let openingHowTo = false;
async function howto() {
  if (!tab() || openingHowTo || document.querySelector("dialog[open]")) return;
  const key = currentKey(), lever = state.lever, mode = state.mode[lever], page = pages[lever], experiment = experiments[mode];
  openingHowTo = true;
  try {
    const snippet = await api(`/static/howto/${lever}-${mode}.json`);
    if (key !== currentKey() || document.querySelector("dialog[open]")) return;
    const run = current();
    const grid = el("div", null, "diff");
    for (const name of Object.keys(laneLabels)) {
      const column = el("div");
      column.append(el("h3", laneLabels[name]), el("pre", json(snippet[name])));
      grid.append(column);
    }
    const steps = el("section", null, "implementation-steps"), list = el("ol");
    list.append(...experiment.steps.map(s => el("li", s)));
    steps.append(el("h3", "Start here"), list);
    const body = $("howto-body");
    body.replaceChildren(pill(viewSource(), run.source ? shownSource(run) : $("status").textContent),
      el("h3", `${tab().title} · ${modeLabel(mode, tab().modes[mode])}`), steps, el("p", page.acceptance));
    body.append(el("p", snippet.note || ""));
    const context = el("div");
    const agents = state.config.mode === "live" && !replayView() ? tab().agents?.[mode] : null;
    for (const name of Object.keys(laneLabels)) {
      const names = agentLine(`${laneLabels[name]} · configured Foundry agent`, agents?.[name]);
      if (names) context.append(names);
    }
    if (context.childElementCount) {
      body.append(context, el("p", lever === "response_cache" ? supportAgentNote : orchestrationNote));
      const link = portalLink();
      if (link) body.append(link);
    }
    const livePath = el("p");
    livePath.append("In Live, the app sends generation to ", el("code", "/openai/v1/responses"),
      " with an ", el("code", "agent_reference"), " that names the published agent version. Azure reports cache reads and writes in ",
      el("code", "input_tokens_details"), " when available. Inspect a receipt for the raw usage and confirmed response ID.");
    body.append(livePath, details("Advanced · call a published Foundry agent", responsesExample),
      el("p", "The recipes below show the underlying request or app logic.", "muted"), grid);
    if (snippet.shared) body.append(details("Advanced · shared connection code for the recipes", snippet.shared));
    const modal = $("howto");
    modal.scrollTop = 0;
    modal.showModal();
  } catch (error) { toast(error.message); }
  finally { openingHowTo = false; }
}

let statusPending = false;
async function checkStatus() {
  if (statusPending || replayView() || state.config?.mode === "replay-only") return;
  statusPending = true;
  try {
    const code = savedCode();
    state.status = await api("/api/status", { headers: code ? { "X-Demo-Code": code } : {} });
  } catch {
    state.status = { foundry: "error", mode: state.config?.mode || "live", unreachable: true,
      detail: "Unable to reach the app.", missing_prices: [] };
  } finally { statusPending = false; }
  statusUI();
  buttons();
}

function toggle(key, button, on) {
  const root = document.documentElement;
  if (root.dataset[key] === on) delete root.dataset[key];
  else root.dataset[key] = on;
  $(button).setAttribute("aria-pressed", String(root.dataset[key] === on));
  writeStored("localStorage", key, root.dataset[key]);
}

$("dark").onclick = () => toggle("theme", "dark", "dark");
$("large").onclick = () => toggle("large", "large", "true");
$("howto-btn").onclick = howto;
$("mode").onchange = e => { state.mode[state.lever] = e.target.value; brief(); renderRun(); };
$("count").onchange = e => {
  state.count[state.lever] = Number(e.target.value);
  delete state.runs[currentKey()];
  brief();
  renderRun();
};
$("run-form").onsubmit = e => { e.preventDefault(); start(); };
$("replay").onclick = () => start(true);
$("stop-baseline").onclick = stopBaseline;
document.addEventListener("keydown", event => {
  if (event.ctrlKey || event.altKey || event.metaKey || event.repeat || document.querySelector("dialog[open]")
      || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) || event.target.isContentEditable) return;
  const key = event.key.toLowerCase(), target = Object.keys(state.tabs)[Number(key) - 1];
  if (target) { event.preventDefault(); selectTab(target); }
  if (key === "r") start();
  if (key === "p") start(true);
  if (key === "h") howto();
  if (key === "d") $("dark").click();
  if (key === "+") $("large").click();
});
for (const [key, button] of [["theme", "dark"], ["large", "large"]]) {
  const value = readStored("localStorage", key);
  if (value) {
    document.documentElement.dataset[key] = value;
    $(button).setAttribute("aria-pressed", "true");
  }
}

try {
  const [, config] = await Promise.all([checkStatus(), api("/api/config")]);
  state.config = config;
  for (const [key, data] of Object.entries({ ...config.tabs, scoreboard: { title: "Scoreboard" } })) {
    const button = el("button", data.title);
    button.type = "button";
    button.dataset.index = String(Object.keys(state.tabs).length + 1).padStart(2, "0");
    button.setAttribute("aria-label", data.title);
    button.onclick = () => selectTab(key);
    state.tabs[key] = button;
    $("tabs").append(button);
    if (data.modes) {
      state.mode[key] = Object.keys(data.modes)[0];
      state.count[key] = data.default;
    }
  }
  selectTab("prompt_caching");
  setInterval(checkStatus, 5000);
} catch (error) {
  $("startup-error").textContent = `The lab could not load. ${error.message} Reload to try again.`;
  $("startup-error").hidden = false;
}
