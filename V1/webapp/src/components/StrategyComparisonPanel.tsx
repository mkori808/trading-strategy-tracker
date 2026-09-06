import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { changeColor, fmtPct } from "../format";
import { Modal } from "./Modal";
import type { StrategyAccount } from "./StrategyAccountsPanel";

const COLORS = [
  "var(--series-1)",
  "var(--status-good)",
  "var(--status-warning)",
  "var(--status-critical)",
  "var(--text-secondary)",
  "#8b5cf6",
  "#06b6d4",
  "#f97316",
  "#ec4899",
];

function standardDeviation(values: number[]): number | null {
  if (values.length < 2) return null;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  return Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1));
}

function correlation(a: number[], b: number[]): number | null {
  if (a.length !== b.length || a.length < 2) return null;
  const aMean = a.reduce((sum, value) => sum + value, 0) / a.length;
  const bMean = b.reduce((sum, value) => sum + value, 0) / b.length;
  const covariance = a.reduce((sum, value, index) => sum + (value - aMean) * (b[index] - bMean), 0);
  const aScale = Math.sqrt(a.reduce((sum, value) => sum + (value - aMean) ** 2, 0));
  const bScale = Math.sqrt(b.reduce((sum, value) => sum + (value - bMean) ** 2, 0));
  return aScale && bScale ? covariance / (aScale * bScale) : null;
}

function drawdown(values: number[]): number | null {
  if (!values.length) return null;
  let peak = values[0];
  let worst = 0;
  for (const value of values) {
    peak = Math.max(peak, value);
    worst = Math.min(worst, (value / peak - 1) * 100);
  }
  return worst;
}

function holdingsOverlap(a: StrategyAccount, b: StrategyAccount): number | null {
  if (!a.positions.length || !b.positions.length) return null;
  const left = new Map(a.positions.map((position) => [position.symbol, Math.abs(position.weight ?? 0)]));
  const right = new Map(b.positions.map((position) => [position.symbol, Math.abs(position.weight ?? 0)]));
  const leftGross = [...left.values()].reduce((sum, weight) => sum + weight, 0);
  const rightGross = [...right.values()].reduce((sum, weight) => sum + weight, 0);
  if (!leftGross || !rightGross) return null;
  const symbols = new Set([...left.keys(), ...right.keys()]);
  let shared = 0;
  let union = 0;
  for (const symbol of symbols) {
    const leftWeight = (left.get(symbol) ?? 0) / leftGross;
    const rightWeight = (right.get(symbol) ?? 0) / rightGross;
    shared += Math.min(leftWeight, rightWeight);
    union += Math.max(leftWeight, rightWeight);
  }
  return union ? (shared / union) * 100 : null;
}

function totalTurnover(account: StrategyAccount): number | null {
  if (account.positionHistory.length < 2) return null;
  let total = 0;
  for (let index = 1; index < account.positionHistory.length; index += 1) {
    const prior = account.positionHistory[index - 1].holdings;
    const current = account.positionHistory[index].holdings;
    const symbols = new Set([...Object.keys(prior), ...Object.keys(current)]);
    total += .5 * [...symbols].reduce(
      (sum, symbol) => sum + Math.abs((current[symbol] ?? 0) - (prior[symbol] ?? 0)),
      0,
    );
  }
  return total * 100;
}

function fmtDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, {
    timeZone: "UTC", month: "short", day: "numeric",
  });
}

export function StrategyComparisonPanel({ accounts }: { accounts: StrategyAccount[] }) {
  const [open, setOpen] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);

  const selected = accounts.filter((account) => selectedKeys.includes(account.key));
  const comparison = useMemo(() => {
    if (selected.length < 2) return null;
    const maps = selected.map((account) => new Map(account.history.map((point) => [point.date, point.equity])));
    const dates = [...maps[0].keys()]
      .filter((date) => maps.every((map) => map.has(date)))
      .sort();
    if (dates.length < 2) return { dates, chart: [], metrics: [] };
    const equitySeries = maps.map((map) => dates.map((date) => map.get(date)!));
    const returns = equitySeries.map((values) => values.slice(1).map((value, index) => value / values[index] - 1));
    const chart = dates.map((date, dateIndex) => Object.fromEntries([
      ["date", date],
      ...selected.map((account, strategyIndex) => [
        account.key,
        Number((equitySeries[strategyIndex][dateIndex] / equitySeries[strategyIndex][0] * 100).toFixed(3)),
      ]),
    ]));
    const baseline = selected[0];
    const metrics = selected.map((account, index) => ({
      account,
      returnPct: (equitySeries[index].at(-1)! / equitySeries[index][0] - 1) * 100,
      maxDrawdownPct: drawdown(equitySeries[index]),
      dailyVolatilityPct: (standardDeviation(returns[index]) ?? 0) * 100,
      worstDayPct: returns[index].length ? Math.min(...returns[index]) * 100 : null,
      correlation: index === 0 ? 1 : correlation(returns[index], returns[0]),
      overlapPct: index === 0 ? 100 : holdingsOverlap(account, baseline),
      turnoverPct: totalTurnover(account),
    }));
    return { dates, chart, metrics };
  }, [selected]);

  const openComparison = () => {
    const defaults = accounts.slice(0, 5).map((account) => account.key);
    const hourly = accounts.find((account) => account.key === "dm_optimized_63d_hourly");
    setSelectedKeys(hourly && !defaults.includes(hourly.key) ? [...defaults, hourly.key] : defaults);
    setOpen(true);
  };

  return (
    <>
      <button type="button" onClick={openComparison} className="rounded-md px-3 py-1.5 text-xs font-medium text-white" style={{ background: "var(--series-1)" }}>
        Compare strategies
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="Strategy comparison" subtitle="Same settled dates · normalized to 100" size="xl">
        <div className="space-y-5">
          <div>
            <h3 className="text-xs font-semibold">Strategies</h3>
            <div className="mt-2 flex flex-wrap gap-2">
              {accounts.map((account) => {
                const active = selectedKeys.includes(account.key);
                return <button key={account.key} type="button" onClick={() => setSelectedKeys((keys) => active ? keys.filter((key) => key !== account.key) : [...keys, account.key])} className="rounded-full border px-3 py-1 text-xs" style={{ borderColor: active ? "var(--series-1)" : "var(--border)", color: active ? "var(--series-1)" : "var(--text-secondary)", background: active ? "var(--series-1-wash)" : "transparent" }}>{account.name}</button>;
              })}
            </div>
          </div>

          {selected.length < 2 ? (
            <p className="rounded-md border px-3 py-3 text-sm" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>Select at least two strategies.</p>
          ) : !comparison || comparison.dates.length < 2 ? (
            <p className="rounded-md border px-3 py-3 text-sm" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>These strategies do not yet have two settled sessions in common.</p>
          ) : (
            <>
              <div className="rounded-lg border p-3" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
                <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2"><h3 className="text-sm font-semibold">Aligned normalized growth</h3><span className="text-xs" style={{ color: "var(--text-muted)" }}>{comparison.dates[0]} through {comparison.dates.at(-1)} · {comparison.dates.length} sessions</span></div>
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={comparison.chart} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="var(--gridline)" vertical={false} />
                    <XAxis dataKey="date" tickFormatter={fmtDate} tick={{ fontSize: 10, fill: "var(--text-muted)" }} minTickGap={28} />
                    <YAxis domain={["auto", "auto"]} width={45} tick={{ fontSize: 10, fill: "var(--text-muted)" }} />
                    <Tooltip labelFormatter={(value) => fmtDate(String(value))} />
                    {selected.map((account, index) => <Line key={account.key} type="monotone" dataKey={account.key} name={account.name} stroke={COLORS[index % COLORS.length]} strokeWidth={2} dot={false} />)}
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--border)" }}>
                <table className="w-full min-w-[900px] text-sm">
                  <thead><tr className="border-b" style={{ borderColor: "var(--gridline)", color: "var(--text-muted)" }}><th className="px-3 py-2 text-left font-medium">Strategy</th><th className="px-3 py-2 text-right font-medium">Return</th><th className="px-3 py-2 text-right font-medium">Max drawdown</th><th className="px-3 py-2 text-right font-medium">Daily volatility</th><th className="px-3 py-2 text-right font-medium">Worst day</th><th className="px-3 py-2 text-right font-medium">Correlation vs first</th><th className="px-3 py-2 text-right font-medium">Holdings overlap vs first</th><th className="px-3 py-2 text-right font-medium">Recorded turnover</th></tr></thead>
                  <tbody>{comparison.metrics.map((metric) => <tr key={metric.account.key} className="border-b last:border-0" style={{ borderColor: "var(--gridline)" }}><td className="px-3 py-2 font-medium">{metric.account.name}</td><td className="px-3 py-2 text-right" style={{ color: changeColor(metric.returnPct) }}>{fmtPct(metric.returnPct)}</td><td className="px-3 py-2 text-right" style={{ color: changeColor(metric.maxDrawdownPct) }}>{fmtPct(metric.maxDrawdownPct)}</td><td className="px-3 py-2 text-right">{metric.dailyVolatilityPct.toFixed(2)}%</td><td className="px-3 py-2 text-right" style={{ color: changeColor(metric.worstDayPct) }}>{fmtPct(metric.worstDayPct)}</td><td className="px-3 py-2 text-right">{metric.correlation === null ? "—" : metric.correlation.toFixed(2)}</td><td className="px-3 py-2 text-right">{metric.overlapPct === null ? "—" : `${metric.overlapPct.toFixed(1)}%`}</td><td className="px-3 py-2 text-right">{metric.turnoverPct === null ? "—" : `${metric.turnoverPct.toFixed(1)}%`}</td></tr>)}</tbody>
                </table>
              </div>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>All performance figures use only the intersection of settled dates. Daily volatility is descriptive and not annualized. Recorded turnover requires at least two immutable rebalance snapshots.</p>
            </>
          )}
        </div>
      </Modal>
    </>
  );
}
