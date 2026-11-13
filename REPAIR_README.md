# Repair notes

Root cause confirmed from screenshots: Dhan `load_fo_universe()` found zero verified mappings, crashing both Live Radar and Next-Day Desk. The old connector required detecting futures symbols from a Dhan master whose schema/contract names differ. This repair uses NSE published F&O market lots for membership and joins exact equity symbols to Dhan NSE cash IDs. It refuses to invent mappings.

**Deployment:** Upload the CONTENTS of this folder to the existing GitHub repo root, keeping `.github/workflows` and `.streamlit`. Keep GitHub/Streamlit secrets outside the repository. Run the snapshot workflow after market close. The dashboard and workflow must share the same revision.

**Tests:** `python -m unittest test_universe_offline.py` and `python -m compileall -q .`. No Dhan credentials are included. Offline tests cannot prove external master format, Dhan API permissions, Streamlit Cloud persistence or actual 203 F&O names.

**Important:** Auto Paper Trader is a simulated underlying-point model, not live options execution. It runs when the Streamlit tab is active, and its local SQLite database may be lost on cloud restart. It is not unattended 24/7 trading.
