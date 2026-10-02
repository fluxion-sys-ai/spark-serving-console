import type { ReactNode } from "react";

interface StatTileProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  children?: ReactNode; // optional sparkline
}

export function StatTile({ label, value, sub, children }: StatTileProps) {
  return (
    <div className="tile">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub !== undefined && <span className="sub">{sub}</span>}
      {children !== undefined && <div className="spark">{children}</div>}
    </div>
  );
}
