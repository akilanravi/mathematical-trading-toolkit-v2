# MTT V2 — Step 1: F&O-only data foundation

Replace your GitHub files with the contents of this ZIP. Preserve existing Streamlit Secrets (`DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`). Never commit credentials.

This step removes all index fetching and introduces an automatically discovered NSE F&O stock universe, derived from current Dhan FUTSTK records and mapped to NSE cash IDs. It adds a 30-second isolated status fragment and a 5-stock batched Dhan diagnostic. It does **not** yet implement the full radar, paper trader, or automatic background scans; these are later steps.

If the Dhan master lacks an underlying-symbol column or cannot map equity IDs uniquely, the app reports the error rather than inventing identifiers. Dhan HTTP 429 cooldown remains in place.

Run: `pip install -r requirements.txt && streamlit run app.py`.

If you see an instrument-master schema error, share only the column names and the error message (never credentials).
