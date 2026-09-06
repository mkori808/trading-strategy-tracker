import {
  api,
  type InsiderRecentResponse,
  type MoversResponse,
  type ScreenerResponse,
  type SymbolsResponse,
} from "./api";
import { useResource } from "./useResource";
import { KEYS } from "./resourceKeys";

/** Shared reads used by BOTH a dashboard card and the popup that card
 * opens. They live here rather than beside their views so a component file
 * exports only components (React Fast Refresh requirement), and so the two
 * call sites can't drift onto different cache keys -- see useResource.ts.
 *
 * `enabled: false` subscribes without fetching, for a component that wants
 * to read or refresh an entry someone else is responsible for loading. */

export function useMovers(enabled = true) {
  return useResource<MoversResponse>(KEYS.movers, () => api.movers(), enabled);
}

export function useInsider(enabled = true) {
  return useResource<InsiderRecentResponse>(KEYS.insider, () => api.insiderRecent(), enabled);
}

export function useScreener(enabled = true) {
  return useResource<ScreenerResponse>(KEYS.screener, () => api.screener(), enabled);
}

export function useSymbols(enabled = true) {
  return useResource<SymbolsResponse>(KEYS.symbols, () => api.listSymbols(), enabled);
}
