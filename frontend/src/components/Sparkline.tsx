/** A small time-series sparkline: 2px line + ~10% area wash, no axes. */

import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtClock } from "../format";
import { ChartTip } from "./charts";

export interface SparkPoint {
  ts: number;
  v: number;
}

interface SparklineProps {
  data: SparkPoint[];
  color?: string;
  height?: number;
  /** Fixed y-domain, e.g. [0, 100] for percentages; otherwise auto. */
  domain?: [number, number];
  name?: string;
  format?: (v: number) => string;
}

export function Sparkline({
  data,
  color = "var(--series-1)",
  height = 44,
  domain,
  name = "value",
  format,
}: SparklineProps) {
  if (data.length < 2) {
    return (
      <div
        className="muted"
        style={{ fontSize: 11.5, height, display: "flex", alignItems: "center" }}
      >
        collecting…
      </div>
    );
  }
  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
          <XAxis dataKey="ts" hide />
          <YAxis domain={domain ?? ["auto", "auto"]} hide />
          <Tooltip
            cursor={{ stroke: "var(--baseline)", strokeWidth: 1 }}
            content={
              <ChartTip
                formatter={(v) => (format ? format(v) : v.toLocaleString("en-US", { maximumFractionDigits: 1 }))}
                titleFormatter={(l) => (typeof l === "number" ? fmtClock(l) : String(l ?? ""))}
              />
            }
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="v"
            name={name}
            stroke={color}
            strokeWidth={2}
            fill={color}
            fillOpacity={0.1}
            isAnimationActive={false}
            dot={false}
            activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
