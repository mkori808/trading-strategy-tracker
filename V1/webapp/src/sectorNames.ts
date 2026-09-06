// Sector SPDR ticker -> human-readable name, matching engine/universe.py's
// SECTOR_UNIVERSE order. Shared between SectorHeatTiles and Sidebar so
// there's one mapping, not two that can drift.
export const SECTOR_NAMES: Record<string, string> = {
  XLK: "Technology",
  XLF: "Financials",
  XLE: "Energy",
  XLV: "Health Care",
  XLY: "Consumer Discretionary",
  XLP: "Consumer Staples",
  XLI: "Industrials",
  XLB: "Materials",
  XLRE: "Real Estate",
  XLU: "Utilities",
  XLC: "Communication Services",
  SPY: "S&P 500 (benchmark)",
};

export function sectorName(symbol: string): string {
  return SECTOR_NAMES[symbol] ?? symbol;
}
