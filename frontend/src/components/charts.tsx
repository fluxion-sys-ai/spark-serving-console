/**
 * Shared chart chrome for Recharts: custom tooltip and legend styled per the
 * dataviz spec — hairline solid grid, muted axis ink, line-key tooltip rows
 * with the value leading, legend swatches mirroring the mark.
 */

import type { CSSProperties } from "react";

// Recharts' Tooltip `content` prop types are loose; we accept the runtime shape.
export interface TipPayloadEntry {
  name?: string | number;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

export interface ChartTipProps {
  active?: boolean;
  label?: string | number;
  payload?: TipPayloadEntry[];
  formatter?: (value: number, entry: TipPayloadEntry) => string;
  titleFormatter?: (label: string | number | undefined) => string;
}

export function ChartTip({ active, label, payload, formatter, titleFormatter }: ChartTipProps) {
  if (!active || !payload || payload.length === 0) return null;
  const title = titleFormatter ? titleFormatter(label) : label !== undefined ? String(label) : "";
  return (
    <div className="chart-tip" role="status">
      {title !== "" && <div className="tip-title">{title}</div>}
      {payload.map((entry, i) => {
        const raw = typeof entry.value === "number" ? entry.value : Number(entry.value);
        const text =
          formatter && Number.isFinite(raw)
            ? formatter(raw, entry)
            : Number.isFinite(raw)
              ? raw.toLocaleString("en-US", { maximumFractionDigits: 1 })
              : String(entry.value ?? "—");
        return (
          <div className="tip-row" key={`${String(entry.dataKey ?? entry.name ?? i)}`}>
            <span className="tip-key" style={{ background: entry.color ?? "var(--muted)" }} />
            <span className="tip-name">{String(entry.name ?? "")}</span>
            <span className="tip-val">{text}</span>
          </div>
        );
      })}
    </div>
  );
}

export interface LegendEntry {
  label: string;
  color: string;
  value?: string;
  /** Mark shape the legend mirrors: rect for bars/areas, line for lines. */
  shape?: "rect" | "line";
}

export function LegendRow({ entries }: { entries: LegendEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <div className="legend-row">
      {entries.map((e) => {
        const style: CSSProperties =
          e.shape === "line"
            ? { background: e.color, height: 3, borderRadius: 1.5, width: 14 }
            : { background: e.color };
        return (
          <span className="legend-item" key={e.label}>
            <span className="swatch" style={style} />
            {e.label}
            {e.value !== undefined && <span className="lval">{e.value}</span>}
          </span>
        );
      })}
    </div>
  );
}

/** Shared axis/grid props (spread into Recharts elements). */
export const AXIS_TICK = { fill: "var(--muted)", fontSize: 11 } as const;
export const AXIS_LINE = { stroke: "var(--baseline)", strokeWidth: 1 } as const;
export const GRID_STROKE = "var(--grid)";
