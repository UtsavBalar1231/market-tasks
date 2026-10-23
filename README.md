# market-tasks

Data and tooling for four scheduled Indian-market research tasks that email Utsav: a pre-market check, an evening swing scan of Nifty Midcap 150 and Smallcap 250, a daily news and ideas brief, and a Saturday weekly review.

| Path | What it is |
|---|---|
| `mscan.py` | Scanner. History from [eod2_data](https://github.com/BennyThadikaran/eod2_data) (NSE EOD, split-adjusted), newer sessions from `data/nse`, then any candles a run passes with `--extra`. Commands: `day`, `scan`, `show SYM...`, `week`. Writes `charts.html` blocks for the emails. |
| `scripts/fetch_nse.py` | Run by the Action after the close: NSE bhavcopy with delivery, index closes with P/E, bulk and block deals into `data/nse` (last 45 sessions), status in `data/status.json`. |
| `.github/workflows/nse-daily.yml` | Weekdays 18:45 and 19:15 IST. |
| `data/universe.csv` | Nifty Midcap 150 and Smallcap 250 constituents (Sep 2026 rebalance) with Groww page slugs. Refresh after each March and September rebalance. |
| `guide/email.html` | Email design system the tasks build from. |
| `prompts/` | The live prompts for the four scheduled tasks, kept in sync with the tasks. |

Research only. Nothing here places orders.
