// Three real destinations. Most former tabs -- Market, Screener, Movers,
// Symbols, Live Monitor -- are cards on the Dashboard that open the full
// view in a popup, so the user never navigates away from the overview to
// read one number.
//
// Strategies stays a real page rather than a popup because it is a
// WORKSPACE, not a readout: it holds a selected strategy, an unsaved
// parameter-override draft, a validation run that can take minutes, and a
// chat conversation scoped to one result. State you leave and come back to
// does not belong in a dialog, and the canonical-vs-experiment disclosure
// (see CLAUDE.md's Lab tab section) needs room to stay visible.
//
// Research earned its own tab for a different reason (2026-09-05): its
// Dashboard card+popup had grown into a full per-strategy leaderboard
// (name, mode, maturity, forward return) that duplicated most of what the
// Strategy accounts panel already showed, just with less detail (no dollar
// equity, no live ticking, no click-through). Keeping both on the home page
// was showing the same ~5 strategies twice a few inches apart. Research
// Status is the deeper, browse-oriented view (evidence stage, causal chain,
// what's blocking the rest) and reads better as a destination you navigate
// to, not a popup layered over a home page it was quietly repeating.
export type Tab = "dashboard" | "research" | "strategies";

export const TABS: { key: Tab; label: string }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "research", label: "Research" },
  { key: "strategies", label: "Strategies" },
];
