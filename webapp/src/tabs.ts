// Single source of truth for the tab list/order -- shared by App.tsx (which
// owns the active-tab state and renders each tab's content) and Sidebar.tsx
// (which now renders the nav itself, moved from a top pill-bar into a
// vertical list per the user's request to match the referenced app's
// left-hand nav placement).
export type Tab = "strategies" | "symbols" | "market" | "screener" | "movers" | "monitor";

export const TABS: { key: Tab; label: string }[] = [
  { key: "strategies", label: "Strategies" },
  { key: "symbols", label: "Symbols" },
  { key: "market", label: "Market" },
  { key: "screener", label: "Screener" },
  { key: "movers", label: "Movers" },
  { key: "monitor", label: "Live Monitor" },
];
