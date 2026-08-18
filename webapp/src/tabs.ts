// Single source of truth for the tab list/order -- shared by App.tsx (which
// owns the active-tab state and renders each tab's content) and Sidebar.tsx
// (which renders the nav itself as two grouped sections rather than one flat
// list -- "Research" is the strategize-and-backtest workflow, "Market" is
// the live-monitoring toolset. They're different modes of using the app, not
// just six equally-weighted destinations, so the nav says so.
export type Tab = "strategies" | "symbols" | "market" | "screener" | "movers" | "monitor";

export type TabGroup = "Research" | "Market";

export const TABS: { key: Tab; label: string; group: TabGroup }[] = [
  { key: "strategies", label: "Strategies", group: "Research" },
  { key: "symbols", label: "Symbols", group: "Market" },
  { key: "market", label: "Market", group: "Market" },
  { key: "screener", label: "Screener", group: "Market" },
  { key: "movers", label: "Movers", group: "Market" },
  { key: "monitor", label: "Live Monitor", group: "Market" },
];

export const TAB_GROUPS: TabGroup[] = ["Research", "Market"];
