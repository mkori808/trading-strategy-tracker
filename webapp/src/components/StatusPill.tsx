const STATUS_STYLE: Record<string, { color: string; bg: string; icon: string }> = {
  "Positive expectancy - shortlist": {
    color: "var(--status-good)",
    bg: "var(--status-good-bg)",
    icon: "▲",
  },
  "Negative expectancy - drop": {
    color: "var(--status-critical)",
    bg: "var(--status-critical-bg)",
    icon: "▼",
  },
  "Positive expectancy but underperforms cash/benchmark - hold": {
    color: "var(--status-warning)",
    bg: "var(--status-warning-bg)",
    icon: "◐",
  },
  "Sample too small (<30 trades)": {
    color: "var(--status-warning)",
    bg: "var(--status-warning-bg)",
    icon: "●",
  },
  // Portfolio-engine verdicts (Dual Momentum, Pairs / Stat Arb) -- same
  // tiers as the expectancy-based ones, phrased in return terms because
  // those engines have no R-multiple trades. See
  // engine/metrics.py:portfolio_status().
  "Positive return - shortlist": {
    color: "var(--status-good)",
    bg: "var(--status-good-bg)",
    icon: "▲",
  },
  "Negative return - drop": {
    color: "var(--status-critical)",
    bg: "var(--status-critical-bg)",
    icon: "▼",
  },
  "Positive return but underperforms cash/benchmark - hold": {
    color: "var(--status-warning)",
    bg: "var(--status-warning-bg)",
    icon: "◐",
  },
};

const DEFAULT_STYLE = { color: "var(--text-muted)", bg: "transparent", icon: "○" };

export function StatusPill({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? DEFAULT_STYLE;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap"
      style={{ color: style.color, background: style.bg }}
    >
      <span aria-hidden="true" style={{ fontSize: 8 }}>
        {style.icon}
      </span>
      {status}
    </span>
  );
}
