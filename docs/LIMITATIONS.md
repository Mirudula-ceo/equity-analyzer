# Data sources, limitations and remaining data requirements

## Sources investigated
| Data | Source used | Status |
|---|---|---|
| Daily NSE prices (10+ yrs), dividends, splits/bonuses | Yahoo Finance via `yfinance` (unofficial) | Automated |
| Annual financial statements | Yahoo via `yfinance` | Automated, but only ~4 most recent years, no announcement dates, latest (possibly restated) numbers |
| Quarterly results | Yahoo via `yfinance` | ~5 quarters, often incomplete for cash flow; stored but not used for scoring |
| Current Nifty 50 / 500 constituents | niftyindices.com public CSV (fallback: built-in list) | Automated |
| Historical index constituents | none free & reliable | **Unavailable** → survivorship bias |
| Shareholding pattern, promoter pledge | NSE/BSE websites | **Not automated** – protected by anti-bot measures, which this project does not bypass. Manual CSV import supported |
| Corporate announcements, order book, customer concentration | Exchange filings / annual reports (PDF) | **Not automated** – needs human reading |
| Benchmark | NIFTYBEES.NS adjusted close (Nifty 50 ETF) as total-return proxy; ^NSEI price index also stored | Automated (proxy, not the official TRI) |
| Delisted companies | not in Yahoo | **Unavailable** |

Not used: screener.in / Tijori / Trendlyne exports (require login/subscription), NSE JSON APIs (anti-bot).

## To run a true full 27-metric backtest you would need
1. Point-in-time fundamentals for 10+ years with actual announcement dates (e.g. CMIE Prowess, Capitaline,
   ACE Equity, or Refinitiv/Bloomberg – all paid).
2. Historical Nifty 50 / 500 membership by date (NSE Indices data products, paid) – removes survivorship bias.
3. Prices for delisted/merged companies (same paid vendors).
4. Quarterly shareholding & pledge history (BSE/NSE bulk data via a licensed vendor, or manual collection).
5. Human-reviewed, dated evidence for the six qualitative metrics.

## Simplifications in the backtest
- Signals at the rebalance-day close, trades at next day's open, 10 bps slippage each way.
- Today's charge rates (STT, stamp, exchange, SEBI, GST, DP) applied to all years; brokerage configurable (default ₹0).
- Dividends credited on ex-date; no taxes on dividends or capital gains.
- Statement publication date assumed = fiscal period end + 75 days (annual).
- Obvious bad price ticks (a >40% jump that reverts within 5 days, e.g. NIFTYBEES on 19–20 Dec 2019) are replaced by the previous close and listed as warnings.
- Demergers Yahoo has not adjusted for are listed in `config/corporate_events.csv` (currently Tata Motors / TMCV, 14-Oct-2025); the spun-off shares are credited as cash worth (previous close − ex-date open). Older demergers (Reliance/Jio Financial 2023, ITC/ITC Hotels 2025) are already price-adjusted by Yahoo.
- Performance statistics start at the first rebalance with an investment (time spent waiting for 12 months of price history or enough scorable companies is excluded).
- A held stock with no further prices (delisted/suspended) is valued at its last close and a warning is logged.
