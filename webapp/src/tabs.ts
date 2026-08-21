// The app has exactly two destinations. Everything that used to be its own
// tab -- Market, Screener, Movers, Symbols, Live Monitor -- is now a card on
// the Dashboard that opens the full view in a popup, so the user never
// navigates away from the overview to read one number.
//
// Strategies stays a real page rather than a popup because it is a
// WORKSPACE, not a readout: it holds a selected strategy, an unsaved
// parameter-override draft, a validation run that can take minutes, and a
// chat conversation scoped to one result. State you leave and come back to
// does not belong in a dialog, and the canonical-vs-experiment disclosure
// (see CLAUDE.md's Lab tab section) needs room to stay visible.
export type Tab = "dashboard" | "strategies";

export const TABS: { key: Tab; label: string }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "strategies", label: "Strategies" },
];
