import type { ProvenanceBadgeLabel } from "./researchPresentation";

// Distinct from StatusPill.tsx on purpose: a provenance badge answers "how
// was this configuration arrived at" (canonical vs. preregistered vs.
// exploratory vs. selected-after-search), never "did it pass or fail" --
// mixing the two vocabularies into one pill would make an exploratory PASS
// look identical to a canonical PASS, which is exactly the "all historical
// returns presented as equivalent evidence" problem this badge exists to
// prevent. See researchPresentation.ts:classifyProvenance.
const BADGE_STYLE: Record<ProvenanceBadgeLabel, { color: string; background: string }> = {
  CANONICAL: { color: "var(--series-1)", background: "var(--series-1-wash)" },
  PREREGISTERED: { color: "var(--status-good)", background: "var(--status-good-bg)" },
  HOLDOUT: { color: "var(--status-good)", background: "var(--status-good-bg)" },
  EXPLORATORY: { color: "var(--text-muted)", background: "var(--pill-bg)" },
  "SELECTED AFTER SEARCH": { color: "var(--status-warning)", background: "var(--status-warning-bg)" },
};

export function ProvenanceBadge({ badge, title }: { badge: ProvenanceBadgeLabel; title?: string }) {
  const style = BADGE_STYLE[badge];
  return (
    <span
      title={title}
      className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide whitespace-nowrap"
      style={{ color: style.color, background: style.background }}
    >
      {badge}
    </span>
  );
}
