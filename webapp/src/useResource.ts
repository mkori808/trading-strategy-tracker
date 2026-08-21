import { useCallback, useEffect, useSyncExternalStore } from "react";

/** A module-level fetch cache shared by every consumer of the same key.
 *
 * The dashboard shows a SUMMARY of each area in a card and the FULL view in
 * the popup that card opens -- both read the same endpoint. Without a shared
 * cache the popup would re-fetch what the card already has, which for
 * /api/screener (94 symbols of fundamentals) or /api/movers is a real cost,
 * not a rounding error. Keying by URL-ish string and de-duplicating in-flight
 * promises means opening a popup is free: it renders the card's own data.
 *
 * Deliberately NOT a generic client cache -- no stale-while-revalidate, no
 * TTL eviction, no background refetch. Every value here is live market data
 * the user decides when to refresh (`refresh()`, wired to each view's
 * existing Refresh button). An automatic refetch would silently re-trigger
 * the expensive scans this cache exists to avoid.
 *
 * /api/market is the one endpoint NOT routed through here: it is fetched
 * once in App.tsx behind a StrictMode guard and passed down as props, since
 * a cold call scans the full 94-symbol research universe (~40s). That
 * predates this cache and is load-bearing -- see App.tsx's comment. */

type Entry<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  fetchedAt: number | null;
  /** In-flight request, so two components mounting in the same tick share
   * one network call instead of racing two. */
  inFlight: Promise<void> | null;
};

const EMPTY: Entry<unknown> = {
  data: null,
  error: null,
  loading: false,
  fetchedAt: null,
  inFlight: null,
};

const cache = new Map<string, Entry<unknown>>();
const listeners = new Map<string, Set<() => void>>();

function notify(key: string): void {
  listeners.get(key)?.forEach((fn) => fn());
}

function subscribe(key: string, fn: () => void): () => void {
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(fn);
  return () => {
    set.delete(fn);
  };
}

function entryFor<T>(key: string): Entry<T> {
  return (cache.get(key) as Entry<T> | undefined) ?? (EMPTY as Entry<T>);
}

function load<T>(key: string, fetcher: () => Promise<T>, force: boolean): Promise<void> {
  const current = entryFor<T>(key);
  if (current.inFlight) return current.inFlight;
  if (!force && current.fetchedAt !== null) return Promise.resolve();

  const inFlight = fetcher()
    .then((data) => {
      cache.set(key, { data, error: null, loading: false, fetchedAt: Date.now(), inFlight: null });
    })
    .catch((e: unknown) => {
      // Keep any previously loaded data on screen next to the error rather
      // than blanking the card -- a failed refresh of live quotes shouldn't
      // erase the last good reading.
      cache.set(key, {
        data: entryFor<T>(key).data,
        error: String(e),
        loading: false,
        fetchedAt: entryFor<T>(key).fetchedAt,
        inFlight: null,
      });
    })
    .finally(() => notify(key));

  cache.set(key, { ...current, loading: true, error: null, inFlight });
  notify(key);
  return inFlight;
}

export type Resource<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  fetchedAt: number | null;
  /** Re-fetch, bypassing the cache. */
  refresh: () => Promise<void>;
};

/** Read `key`, fetching once on mount if nothing has loaded it yet.
 *
 * `enabled: false` subscribes without fetching -- how a card that hasn't
 * scrolled into view (or a popup body whose card is off-screen) stays
 * mounted and ready without paying for data nobody is looking at. */
export function useResource<T>(
  key: string,
  fetcher: () => Promise<T>,
  enabled = true,
): Resource<T> {
  const entry = useSyncExternalStore(
    useCallback((fn: () => void) => subscribe(key, fn), [key]),
    useCallback(() => entryFor<T>(key), [key]),
  );

  useEffect(() => {
    if (!enabled) return;
    void load(key, fetcher, false);
    // `fetcher` is intentionally excluded: call sites pass an inline arrow
    // that changes identity every render, which would loop. `key` is the
    // real identity of the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);

  const refresh = useCallback(() => load(key, fetcher, true), [key, fetcher]);

  return {
    data: entry.data,
    error: entry.error,
    loading: entry.loading,
    fetchedAt: entry.fetchedAt,
    refresh,
  };
}

/** Publish a value into the cache from a component that fetched it by other
 * means. The live-trading view polls account + kill-switch state every 30s
 * on its own; pushing each poll here keeps the always-visible status strip
 * in step with the popup the user is reading, instead of showing a reading
 * frozen at page load. */
export function setResource<T>(key: string, data: T): void {
  cache.set(key, { data, error: null, loading: false, fetchedAt: Date.now(), inFlight: null });
  notify(key);
}

/** Drop a cached entry so the next mount re-fetches. Used after a mutation
 * makes a cached read stale (enabling automation, saving a config). */
export function invalidate(key: string): void {
  cache.delete(key);
  notify(key);
}
