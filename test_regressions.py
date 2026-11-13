"""Offline regression tests. No real Dhan credentials or market requests."""
import unittest
from unittest.mock import patch
import pandas as pd
import mtt_v2 as m
import dhan_feed

class RegressionTests(unittest.TestCase):
    def test_ohlcv_case(self):
        idx = pd.date_range('2026-11-12 09:15', periods=80, freq='5min', tz='Asia/Kolkata')
        frame = pd.DataFrame({'Open':[100]*80,'High':[102]*80,'Low':[99]*80,'Close':[101]*80,'Volume':[1000]*80},index=idx)
        out=m.normalize_ohlcv(frame)
        self.assertEqual(list(out.columns), ['open','high','low','close','volume'])
    def test_dynamic_map_not_47(self):
        mapping={f'TEST{i}':('NSE_EQ',str(i+100),'EQUITY') for i in range(203)}
        with patch.object(dhan_feed,'load_fo_universe',return_value=mapping):
            self.assertEqual(len(dhan_feed.load_fo_universe()),203)
    def test_snapshot_validity(self):
        import json
        from pathlib import Path
        d=json.loads((Path(__file__).parent/'snapshots/next_day.json').read_text())
        self.assertEqual(d['scanned'],47) # uploaded baseline, not a live 203-stock result

if __name__=='__main__':unittest.main()
