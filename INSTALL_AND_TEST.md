# Mathematical Trading Toolkit V2 — integrated repair

Replace the entire repository content with this ZIP's project files (preserve your GitHub Secrets and Streamlit Secrets). Do not upload .env files or credentials.

1. Extract the ZIP locally and upload/commit the updated files to your GitHub repository, including `.github/workflows/next_day_desk.yml` and new `paper_engine.py`.
2. Ensure GitHub Actions Secrets `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` exist. Streamlit must separately have the same credentials in Streamlit Secrets; GitHub Secrets are NOT automatically available in Streamlit.
3. GitHub Actions > Next-Day Desk Snapshot > Run workflow after NSE close. Inspect `Generate after-close snapshot`, `Validate snapshot` and `Commit snapshot with safe push retry`.
4. In Streamlit, test Live Radar with Top 50 first. Full-universe scanning makes many sequential requests and can be slow/rate-limited. A verified Dhan F&O universe may differ from 203.
5. In Deep Analysis, select a mapped stock and 5m timeframe. Intraday uses Dhan only. Daily and auxiliary MTF/benchmark code can still use Yahoo.
6. Paper Trader is **simulation of underlying spot points**, NOT option premium or option-money P&L. It opens only when its toggle is enabled, based on confirmed 5m bars, and checks exits when the Streamlit tab is running. No real orders are sent. Cloud-hosted SQLite may reset on redeploy/restart. For reliable unattended paper trading, a separate persistent scheduled worker/database is required.
7. Run `python -m unittest -v test_regressions` and `python mtt_v2.py selftest` locally after installing `requirements.txt`.

## Verification limitations
- Python compilation and offline regression/self-tests passed here.
- Real Dhan authentication, live F&O master, market-hours prices, complete 203-stock coverage and end-to-end Streamlit rendering could NOT be verified offline.
- Original snapshot (47 scanned) is retained as historical evidence until a new snapshot is successfully generated and pushed.
- Full 203-symbol simulation and automatic option fills are NOT claimed.
