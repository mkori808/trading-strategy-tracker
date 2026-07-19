import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistoryRow } from "../api";

interface MetricPoint {
  time: string;
  value: number;
}

interface MetricSpec {
  key: string;
  label: string;
  accessor: (r: HistoryRow) => number | null;
  format: (v: number) => string;
}

const METRICS: MetricSpec[] = [
  {
    key: "winRate",
    label: "Win Rate",
    accessor: (r) => r.winRate,
    format: (v) => `${(v * 100).toFixed(1)}%`,
  },
  {
    key: "expectancyR",
    label: "Expectancy (R)",
    accessor: (r) => r.expectancyR,
    format: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(3)}`,
  },
  {
    key: "profitFactor",
    label: "Profit Factor",
    accessor: (r) => r.profitFactor,
    format: (v) => v.toFixed(2),
  },
  {
    key: "maxDrawdownPct",
    label: "Max Drawdown",
    accessor: (r) => r.maxDrawdownPct,
    format: (v) => `${v.toFixed(1)}%`,
  },
  {
    key: "sharpe",
    label: "Sharpe (vs. rf)",
    accessor: (r) => r.sharpe,
    format: (v) => v.toFixed(2),
  },
  {
    key: "alphaPct",
    label: "Alpha vs. buy & hold",
    accessor: (r) => r.alphaPct,
    format: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`,
  },
];

function formatAxisDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatTooltipDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function MetricTooltip({
  active,
  payload,
  format,
}: {
  active?: boolean;
  payload?: { payload: MetricPoint }[];
  format: (v: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div
      className="rounded-md border px-3 py-2 text-xs shadow-sm"
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      <div style={{ color: "var(--text-muted)" }}>{formatTooltipDate(point.time)}</div>
      <div
        className="mt-0.5 flex items-center gap-1.5 font-semibold tabular-nums"
        style={{ color: "var(--text-primary)" }}
      >
        <span
          aria-hidden="true"
          style={{ width: 10, height: 2, background: "var(--series-1)", display: "inline-block" }}
        />
        {format(point.value)}
      </div>
    </div>
  );
}

function MetricFacet({ spec, rows }: { spec: MetricSpec; rows: HistoryRow[] }) {
  const points: MetricPoint[] = rows
    .map((r) => {
      const v = spec.accessor(r);
      return v === null ? null : { time: r.runAt, value: v };
    })
    .filter((p): p is MetricPoint => p !== null);

  return (
    <div
      className="rounded-lg border p-3"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        {spec.label}
      </div>
      {points.length < 2 ? (
        <div
          className="flex h-32 items-center justify-center text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          Not enough runs yet
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={128}>
          <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--gridline)" strokeDasharray="0" vertical={false} />
            <XAxis
              dataKey="time"
              tickFormatter={formatAxisDate}
              stroke="var(--baseline)"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              width={40}
              stroke="var(--baseline)"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              tickLine={false}
              tickFormatter={spec.format}
              domain={["auto", "auto"]}
            />
            <Tooltip
              content={<MetricTooltip format={spec.format} />}
              cursor={{ stroke: "var(--baseline)", strokeWidth: 1 }}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke="var(--series-1)"
              strokeWidth={2}
              dot={{ r: 3, fill: "var(--series-1)", stroke: "var(--surface-1)", strokeWidth: 1 }}
              activeDot={{ r: 5, fill: "var(--series-1)", stroke: "var(--surface-1)", strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export function MetricsHistoryChart({ rows }: { rows: HistoryRow[] }) {
  if (rows.length === 0) {
    return (
      <div
        className="flex h-32 items-center justify-center rounded-lg border text-sm"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
      >
        No prior runs to compare yet.
      </div>
    );
  }

  // History rows come back newest-first; charts read left-to-right chronologically.
  const chronological = [...rows].reverse();

  return (
    <div>
      <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        Performance across previous runs ({chronological.length})
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {METRICS.map((spec) => (
          <MetricFacet key={spec.key} spec={spec} rows={chronological} />
        ))}
      </div>
    </div>
  );
}
