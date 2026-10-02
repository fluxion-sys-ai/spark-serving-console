/** Number / unit formatting helpers. */

export function fmtMib(mib: number | null | undefined): string {
  if (mib === null || mib === undefined || !Number.isFinite(mib)) return "—";
  if (mib >= 1024) return `${(mib / 1024).toFixed(1)} GiB`;
  return `${Math.round(mib)} MiB`;
}

export function fmtGib(gib: number | null | undefined): string {
  if (gib === null || gib === undefined || !Number.isFinite(gib)) return "—";
  return `${gib.toFixed(1)} GiB`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms >= 10000) return `${(ms / 1000).toFixed(1)} s`;
  if (ms >= 100) return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  if (s >= 60) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${s.toFixed(1)} s`;
}

export function fmtPct(pct: number | null | undefined, digits = 0): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "—";
  return `${pct.toFixed(digits)}%`;
}

export function fmtNum(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString("en-US");
}

/** Compact large counts: 1,284 · 12.9K · 4.2M — for token totals. */
export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
  return Math.round(n).toLocaleString("en-US");
}

/** Parameter count in billions: 1.7 B · 235 B. */
export function fmtParams(b: number | null | undefined): string {
  if (b === null || b === undefined || !Number.isFinite(b)) return "—";
  if (b < 1) return `${Math.round(b * 1000)} M`;
  return `${b >= 100 ? Math.round(b) : b.toFixed(1)} B`;
}

/** Context length in tokens: 8K · 40K · 131K. */
export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  if (n >= 1000) return `${Math.round(n / 1024)}K`;
  return String(Math.round(n));
}

/** Bytes as GiB/MiB — for download progress. */
export function fmtBytes(b: number | null | undefined): string {
  if (b === null || b === undefined || !Number.isFinite(b)) return "—";
  if (b >= 1024 ** 3) return `${(b / 1024 ** 3).toFixed(1)} GiB`;
  if (b >= 1024 ** 2) return `${Math.round(b / 1024 ** 2)} MiB`;
  return `${Math.round(b / 1024)} KiB`;
}

/** Transfer rate, e.g. "42 MiB/s". */
export function fmtRateBytes(bps: number | null | undefined): string {
  if (bps === null || bps === undefined || !Number.isFinite(bps) || bps <= 0) return "—";
  return `${fmtBytes(bps)}/s`;
}

/** Coarse duration for an ETA: 45s · 3m 12s · 1h 04m. */
export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s) || s < 0) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export function fmtTemp(c: number | null | undefined): string {
  if (c === null || c === undefined || !Number.isFinite(c)) return "—";
  return `${Math.round(c)}°C`;
}

export function fmtWatts(w: number | null | undefined): string {
  if (w === null || w === undefined || !Number.isFinite(w)) return "—";
  return `${Math.round(w)} W`;
}

/** Fraction 0..1 as an integer percent (e.g. 0.62 -> "62%"). */
export function fmtRate(frac: number | null | undefined, digits = 0): string {
  if (frac === null || frac === undefined || !Number.isFinite(frac)) return "—";
  return `${(frac * 100).toFixed(digits)}%`;
}

export function fmtClock(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false });
}

export function fmtWhen(created: string | number | null | undefined): string {
  if (created === null || created === undefined) return "—";
  const d = typeof created === "number" ? new Date(created * 1000) : new Date(created);
  if (Number.isNaN(d.getTime())) return String(created);
  return d.toLocaleString("en-US", { hour12: false, month: "short", day: "numeric" });
}

/** Signed percent change of b vs a; null when a is not positive. */
export function pctChange(a: number | null | undefined, b: number | null | undefined): number | null {
  if (a === null || a === undefined || b === null || b === undefined) return null;
  if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0) return null;
  return ((b - a) / a) * 100;
}
