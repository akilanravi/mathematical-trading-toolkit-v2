# Mathematical Trading Toolkit V2 — safe foundation

## Files
- `app.py`: Streamlit entrypoint and section navigation
- `data.py`: Dhan-first quote/candle access and Yahoo fallback
- `conditions.py`: shared nine-condition calculation
- `topbar.py`: index summary
- `fiidii.py`: public institutional feed (best effort)
- `tab1_radar.py`, `tab2_desk.py`, `tab3_deep.py`, `tab4_paper.py`: views
- `paper_engine.py`: safe-mode worker scaffold
- `db.py`: SQLite tables
- `costs.py`: illustrative fee model
- `tests/test_conditions.py`: basic unit tests

## Run
Python 3.11 or 3.12 recommended. `python -m venv .venv`, activate it, then `pip install -r requirements.txt` and `streamlit run app.py`. Set `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in Streamlit Cloud Secrets, not GitHub Secrets. Use paid Dhan Data APIs entitlement. Run `pytest -q`.

## Explicit limitations
This is a **safe foundation, not a completed full-spec trading system**. It does NOT claim that all requested integrations work. A Dhan security ID is required for Dhan candle calls; symbol-to-ID mapping is not yet validated. Dhan instrument master is downloaded but not normalized across its changing schema. Top-bar index values require manual fetch, and lack automatic 10-second quote refresh. GIFT Nifty is N/A rather than a false Nasdaq proxy. Yahoo does not reliably supply live option premiums, delivery %, F&O OI or complete 5-minute history. NSE option chain fallback is not implemented. Moneycontrol FII/DII parsing is not implemented. Holiday calendar, automatic 15:45 desk schedule, persistent background workers on sleeping Streamlit Cloud, full-universe scans, weighted rankings, live execution blueprint, Greeks, trailing-stop worker and completed per-strategy ledger are not implemented. The worker intentionally opens no positions. SQLite on Streamlit Community Cloud's ephemeral filesystem is NOT restart-durable; use persistent database hosting for production. Fees are illustrative and require verification against current Zerodha and statutory tariffs. A chart is not proof of valid entries. Only completed candles should be fed into signals; production code must also exclude incomplete intraday bars.

## Security
Never commit access tokens, credentials or SQLite data. Rotate previously shared tokens. `st.secrets` only in deployment. Do not enable automatic broker orders.
