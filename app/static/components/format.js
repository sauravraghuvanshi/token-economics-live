export const byId = id => document.getElementById(id);
export function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
export const num = n => Number(n).toLocaleString("en-IN");
export const usd = (x, places = 4) => x == null ? "—" : `$${Number(x).toFixed(places)}`;
export const pct = x => x == null ? "—" : `${Number(x).toFixed(1)}%`;
export const latency = n => n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${n} ms`;
const timeFormat = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "medium",
});
const shortTimeFormat = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short", hourCycle: "h23",
});
export function ist(value, compact = false) {
  const time = new Date(value);
  return value && Number.isFinite(time.getTime()) ? `${(compact ? shortTimeFormat : timeFormat).format(time)} IST` : "Time unavailable";
}
export const json = x => typeof x === "string" ? x : JSON.stringify(x, null, 2);
export const pair = (a, b) => `${a} → ${b}`;
export const comparison = (change, invert = false) => {
  if (change == null) return "—";
  const n = Number(change) * (invert ? -1 : 1);
  return n === 0 ? "No change" : `${pct(Math.abs(n))} ${n < 0 ? "lower" : "higher"}`;
};
export const sourceText = (source, recording) => source === "fake" ? "FAKE DATA" : source === "live" ? "LIVE"
  : source === "replay" ? `REPLAY${recording ? ` · ${recording.toUpperCase()} recording` : ""}`
    : source === "replay-only" ? "REPLAY ONLY" : "";
export const pill = (source, label = sourceText(source)) => el("span", label,
  `pill ${source === "replay-only" ? "replay" : source || ""}`);
