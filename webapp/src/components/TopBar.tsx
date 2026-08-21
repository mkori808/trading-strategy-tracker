import { TABS, type Tab } from "../tabs";

/** Replaces the old left sidebar's six-item, two-group nav. With the app
 * down to two destinations, a 240px column spent on two buttons was mostly
 * empty space -- and the market snapshot it also carried (SPY, sectors,
 * breadth) now lives in the dashboard's status strip and market card, where
 * it isn't duplicated on every screen.
 *
 * Sticky, because the popups are opened from cards further down a scrolling
 * page and the way back to Strategies shouldn't require scrolling up. */
export function TopBar({
  activeTab,
  onSelectTab,
}: {
  activeTab: Tab;
  onSelectTab: (tab: Tab) => void;
}) {
  return (
    <header
      className="sticky top-0 z-40 border-b backdrop-blur"
      style={{ borderColor: "var(--border)", background: "color-mix(in srgb, var(--page) 88%, transparent)" }}
    >
      <div className="mx-auto flex w-full max-w-7xl items-center gap-6 px-6 py-3">
        <div className="text-sm font-bold" style={{ color: "var(--text-primary)" }}>
          Trading Strategy Lab
        </div>

        <nav className="flex items-center gap-1" aria-label="Main">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => onSelectTab(t.key)}
              aria-current={activeTab === t.key ? "page" : undefined}
              className="rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
              style={{
                background: activeTab === t.key ? "var(--pill-bg-active)" : "transparent",
                color: activeTab === t.key ? "#ffffff" : "var(--text-secondary)",
              }}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
