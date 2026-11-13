# MTT V2 after-close and fast paper-monitor upgrade

## After-close snapshots

The `.github/workflows/after_close.yml` workflow runs on weekdays at 15:55 IST (10:25 UTC). GitHub Actions scheduling is best-effort, not guaranteed instant. Set repository **Actions secrets** `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` separately from Streamlit Cloud secrets. The workflow writes dated JSON into `desk_snapshots/` and commits it to the repository. The next Streamlit redeploy will display it. To run immediately, use **Actions → MTT after-close desk snapshot → Run workflow**. A custom comma-separated watchlist can be set as repository variable `MTT_DESK_WATCHLIST`. The snapshot is intentionally not a 10-bull/10-bear ranking.

## Paper monitor

In Paper Trader enable **Monitor latest underlying price**. Its UI fragment checks every 5 seconds while the page is active. Dhan requests are gated process-wide, with Yahoo fallback; Yahoo values may be delayed. This does NOT enable simulated option trades. A Streamlit Cloud background thread cannot reliably run unattended. For continuous paper trading deploy a separate persistent worker, validate Dhan option-chain premiums and instrument-master lot sizes, and use durable external DB storage. No unverified trades are entered.

## Rate limiting

All Dhan calls in the Streamlit Python process share a 1-second minimum interval and a 600-second cooldown after HTTP 429. GitHub Actions is a separate process and therefore does not share the in-memory limiter. Do not run the scan and dashboard refreshes aggressively. The API's actual quotas may be more restrictive.

## Limitations

The snapshot is a limited watchlist/daily-candle report. NSE holidays are not automatically excluded, and GitHub scheduled jobs can be delayed or skipped. FII/DII scraping may fail. Streamlit Cloud local SQLite is not durable. The paper engine remains safe mode. GIFT NIFTY has no verified source. Never interpret a Yahoo quote as guaranteed real-time.
