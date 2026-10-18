# Equity Analyzer – run report
Generated 2026-10-16 11:39

## Data collected
- Universe: 50 symbols; source: niftyindices.com (https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv)
- Price history: 135,291 daily rows; earliest 2015-10-19, latest 2026-10-16
- Annual statements per company: median 5 years (min 4, max 5) – 5 preferred
- Fetch problems logged: 46 (see fetch_problems.csv)
- Unexplained >35% daily price moves to review: 3
- Impossible publication dates: 0

## Metric availability across universe (count of companies per status)

| name                                         |   Fail |   Insufficient Data |   Not Applicable |   Pass |   Watch |
|:---------------------------------------------|-------:|--------------------:|-----------------:|-------:|--------:|
| Business Quality                             |      0 |                  50 |                0 |      0 |       0 |
| Industry Growth and Total Addressable Market |      0 |                  50 |                0 |      0 |       0 |
| Competitive Advantage / Economic Moat        |      0 |                  50 |                0 |      0 |       0 |
| Revenue Growth                               |     14 |                   0 |                0 |     26 |      10 |
| Profit and EPS Growth                        |     16 |                   0 |                0 |     27 |       7 |
| ROCE                                         |      7 |                   0 |               12 |     20 |      11 |
| ROE                                          |      6 |                   0 |                0 |     32 |      12 |
| ROIC                                         |      5 |                   0 |               12 |     22 |      11 |
| Operating Profit Margin                      |      3 |                   0 |               12 |     20 |      15 |
| CFO-to-PAT Conversion                        |      5 |                   0 |               12 |     31 |       2 |
| Free Cash Flow                               |      4 |                   0 |               12 |     27 |       7 |
| Free Cash Flow Yield                         |     20 |                   0 |               12 |      9 |       9 |
| Working Capital Management                   |      6 |                   0 |               12 |     27 |       5 |
| Earnings Quality and Non-Core Income         |      7 |                  10 |                0 |     21 |      12 |
| Debt-to-Equity                               |     10 |                   0 |               12 |     22 |       6 |
| Interest Coverage                            |      2 |                   0 |               12 |     27 |       9 |
| Piotroski F-Score                            |      8 |                   0 |               12 |     14 |      16 |
| Promoter Shareholding                        |      0 |                  50 |                0 |      0 |       0 |
| Promoter Pledge                              |      0 |                  50 |                0 |      0 |       0 |
| Management Quality and Capital Allocation    |      0 |                  50 |                0 |      0 |       0 |
| Order Book and Revenue Visibility            |      0 |                  50 |                0 |      0 |       0 |
| Customer and Revenue Concentration           |      0 |                  50 |                0 |      0 |       0 |
| P/E and Forward P/E                          |     16 |                   0 |                0 |     21 |      13 |
| P/B                                          |     15 |                   0 |                0 |     19 |      16 |
| EV/EBITDA                                    |     11 |                   0 |               12 |     16 |      11 |
| PEG                                          |     24 |                   2 |                0 |     10 |      14 |
| Margin of Safety                             |     30 |                   5 |               12 |      2 |       1 |

## Top 10 by conservative 27-metric score (current data)

|   rank | symbol     | name                                          |   conservative_score |   coverage |   pass |   watch |   fail |   insufficient |   not_applicable |
|-------:|:-----------|:----------------------------------------------|---------------------:|-----------:|-------:|--------:|-------:|---------------:|-----------------:|
|      1 | MARUTI     | Maruti Suzuki India Limited                   |             0.574074 |   0.703704 |     13 |       5 |      1 |              8 |                0 |
|      2 | HCLTECH    | HCL Technologies Limited                      |             0.555556 |   0.703704 |     14 |       2 |      3 |              8 |                0 |
|      3 | BHARTIARTL | Bharti Airtel Limited                         |             0.537037 |   0.703704 |     13 |       3 |      3 |              8 |                0 |
|      4 | COALINDIA  | Coal India Limited                            |             0.537037 |   0.703704 |     12 |       5 |      2 |              8 |                0 |
|      5 | ONGC       | Oil and Natural Gas Corporation Limited       |             0.537037 |   0.703704 |     12 |       5 |      2 |              8 |                0 |
|      6 | TCS        | Tata Consultancy Services Limited             |             0.5      |   0.703704 |     13 |       1 |      5 |              8 |                0 |
|      7 | EICHERMOT  | Eicher Motors Limited                         |             0.481481 |   0.703704 |     10 |       6 |      3 |              8 |                0 |
|      8 | ADANIPORTS | Adani Ports and Special Economic Zone Limited |             0.462963 |   0.703704 |      8 |       9 |      2 |              8 |                0 |
|      9 | APOLLOHOSP | Apollo Hospitals Enterprise Limited           |             0.462963 |   0.703704 |     11 |       3 |      5 |              8 |                0 |
|     10 | HINDUNILVR | Hindustan Unilever Limited                    |             0.462963 |   0.703704 |     11 |       3 |      5 |              8 |                0 |

## Backtests executed
### Price momentum 12-1 (partial: price data only)
- Run folder: reports/backtests/20261016_113411_0aeb
- Period: 2016-12-30 → 2026-10-16 (9.8 yrs); first trade 2017-01-02
- ₹500,000 → ₹4,180,084; total 736.02%, CAGR 24.21%, max drawdown -44.36%, volatility 21.66%, Sharpe 0.84, Sortino 1.16
- Benchmark (NIFTYBEES.NS adjusted close (Nifty 50 ETF, dividends reinvested, after ETF fees)): CAGR 12.44%, max drawdown -36.34%; excess CAGR 11.78%, beta 1.18
- Trades: 180 sells, win rate 68.89%, avg gain 78.74%, avg loss -11.24%; turnover 1.44x/yr
- Costs: charges ₹65,067, slippage ₹55,933; dividends ₹220,124
- P&L: realised ₹3,256,771, unrealised ₹203,189
- Warnings: 2

### Low volatility 1y (partial: price data only)
- Run folder: reports/backtests/20261016_113422_68b7
- Period: 2016-12-30 → 2026-10-16 (9.8 yrs); first trade 2017-01-02
- ₹500,000 → ₹1,540,305; total 208.06%, CAGR 12.17%, max drawdown -25.67%, volatility 13.37%, Sharpe 0.47, Sortino 0.67
- Benchmark (NIFTYBEES.NS adjusted close (Nifty 50 ETF, dividends reinvested, after ETF fees)): CAGR 12.44%, max drawdown -36.34%; excess CAGR -0.26%, beta 0.74
- Trades: 91 sells, win rate 69.23%, avg gain 35.57%, avg loss -12.07%; turnover 0.89x/yr
- Costs: charges ₹22,690, slippage ₹19,072; dividends ₹175,195
- P&L: realised ₹949,694, unrealised ₹-85,191
- Warnings: 2

### 27-metric score, point-in-time (partial: quantitative metrics only)
- Run folder: reports/backtests/20261016_113916_f961
- Period: 2023-06-30 → 2026-10-16 (3.3 yrs); first trade 2023-07-03
- ₹500,000 → ₹819,518; total 63.90%, CAGR 16.17%, max drawdown -18.50%, volatility 13.18%, Sharpe 0.75, Sortino 1.10
- Benchmark (NIFTYBEES.NS adjusted close (Nifty 50 ETF, dividends reinvested, after ETF fees)): CAGR 6.99%, max drawdown -15.23%; excess CAGR 9.18%, beta 0.88
- Trades: 29 sells, win rate 75.86%, avg gain 28.59%, avg loss -19.02%; turnover 0.97x/yr
- Costs: charges ₹5,676, slippage ₹4,653; dividends ₹93,291
- P&L: realised ₹169,520, unrealised ₹56,706
- Warnings: 2

## Known biases (apply to all backtests above)
- SURVIVORSHIP BIAS: the universe is TODAY's index constituents applied to the whole history. Free historical Nifty membership lists were not available, so stocks that were dropped from the index (often poor performers) are missing. Results are likely flattered.
- Delisted companies are not in Yahoo Finance and cannot be included.
- Current brokerage/statutory charge rates are applied to the whole period.
- Dividends are credited on the ex-date and dividend tax is ignored; capital-gains tax is ignored.
- Financial statement publication dates are ESTIMATED (period end + lag), and Yahoo shows the latest (possibly restated) figures rather than as-first-published numbers.