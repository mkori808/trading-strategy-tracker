/** Every shared-cache key in one place (see useResource.ts).
 *
 * Centralized because the point of the cache is that two unrelated
 * components -- a dashboard card and the popup it opens -- agree on the
 * key. A typo in one of them isn't an error, it's a silent second fetch of
 * a 94-symbol scan, which is exactly the failure the cache exists to
 * prevent. Importing a constant makes that a compile error instead. */
export const KEYS = {
  liveAccount: "live/account",
  killSwitch: "live/kill-switch",
  executionConfig: "live/execution/config",
  executionSummary: "live/execution/summary",
  executionDaily: "live/execution/daily",
  movers: "movers",
  insider: "insider/recent",
  screener: "screener",
  symbols: "symbols",
} as const;
