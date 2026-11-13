"""Conservative paper engine: never opens without a real option quote and master lot size."""
import threading,time,logging
from datetime import datetime,time as clock
from zoneinfo import ZoneInfo
from db import connect
LOG=logging.getLogger(__name__)
class PaperEngine:
 def __init__(self):self.stop_event=threading.Event();self.thread=None;self.status='Stopped';self.reason='No validated option-chain adapter configured'
 def start(self):
  if self.thread and self.thread.is_alive():return
  self.stop_event.clear();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
 def stop(self):self.stop_event.set();self.status='Stopping'
 def run(self):
  self.status='Running (safe mode: no orders)'
  while not self.stop_event.wait(5):
   now=datetime.now(ZoneInfo('Asia/Kolkata'))
   if now.weekday()>4 or not(clock(9,20)<=now.time()<clock(15,15)):
    self.status='Outside paper trading window';continue
   # Explicitly no fabricated option premiums or automatic positions.
   self.status='Waiting for verified option premiums and F&O instrument mapping'
  self.status='Stopped'
ENGINE=PaperEngine()
