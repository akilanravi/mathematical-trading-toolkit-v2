import unittest
from unittest.mock import patch
import pandas as pd
import dhan_feed

class UniverseTest(unittest.TestCase):
    def test_equity_master_join(self):
        names=[f'STOCK{i:03d}' for i in range(80)]
        frame=pd.DataFrame({'SEM_TRADING_SYMBOL':names+['BAD'], 'SEM_SMST_SECURITY_ID':[str(i+1000) for i in range(80)]+['123'], 'SEM_SEGMENT':['E']*81, 'SEM_EXM_EXCH_ID':['NSE']*81, 'SEM_INSTRUMENT_NAME':['EQUITY']*81})
        with patch.object(dhan_feed,'_master',return_value=frame),patch.object(dhan_feed,'_nse_fo_symbols',return_value=set(names)):
            got=dhan_feed.load_fo_universe(force=True)
        self.assertEqual(len(got),80)
        self.assertEqual(got['STOCK000'],('NSE_EQ','1000','EQUITY'))
    def test_fail_closed_without_exchange_list(self):
        with patch.object(dhan_feed,'_master',return_value=pd.DataFrame({'SYMBOL':['X'],'SECURITY_ID':['1']})),patch.object(dhan_feed,'_nse_fo_symbols',return_value=None):
            with self.assertRaises(RuntimeError): dhan_feed.load_fo_universe(force=True)

if __name__=='__main__': unittest.main()
