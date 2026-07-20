// Single source of truth for the tab list/order -- shared by App.tsx (which
// owns the active-tab state and renders each tab's content) and Sidebar.tsx
// (which now renders the nav itself, moved from a top pill-bar into a
// vertical list per the user's request to match the referenced app's
// left-hand nav placement).
export type Tab = "lab" | "compare" | "symbols" | "market" | "screener" | "movers" | "monitor";

export const TABS: { key: Tab; label: string }[] = [
  { key: "lab", label: "Lab" },
  { key: "compare", label: "Compare" },
  { key: "symbols", label: "Symbols" },
  { key: "market", label: "Market" },
  { key: "screener", label: "Screener" },
  { key: "movers", label: "Movers" },
  { key: "monitor", label: "Live Monitor" },
];
