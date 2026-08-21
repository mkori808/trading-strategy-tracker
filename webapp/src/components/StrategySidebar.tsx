/** DOM id of the slot that hosts the Strategies page's run configuration. */
export const STRATEGY_CONFIG_SLOT = "strategy-config-slot";

/** A sidebar that exists only on the Strategies page, only to host the run
 * configuration panel.
 *
 * StrategiesTab renders that panel here by PORTAL rather than lifting its
 * state up -- the config owns the selected strategy, the override draft,
 * running/error state and the run handler, none of which mean anything to a
 * layout component. A portal moves only the rendered output, so ownership
 * stays where the logic is. That arrangement predates the dashboard rebuild
 * and is unchanged; only the container moved out of the old global sidebar,
 * which no longer exists. */
export function StrategySidebar() {
  return (
    <aside
      className="hidden w-72 shrink-0 border-r px-4 py-6 lg:block"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div id={STRATEGY_CONFIG_SLOT} />
    </aside>
  );
}
