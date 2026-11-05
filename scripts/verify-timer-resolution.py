"""Mock timer resolution lifecycle and additional numeric diagnostics."""
import asyncio
import contextlib
import io
import json
from types import SimpleNamespace
import rtc_agent

class TimerDouble:
    def __init__(self,result=0,error=False):self.result=result;self.error=error;self.calls=[]
    def timeBeginPeriod(self,value):
        self.calls.append(('begin',value))
        if self.error:raise OSError('Unavailable timer API')
        return self.result
    def timeEndPeriod(self,value):self.calls.append(('end',value));return 0

output=io.StringIO()
with contextlib.redirect_stdout(output):
    normal=TimerDouble()
    with rtc_agent.WindowsTimerResolution(winmm=normal):pass
    assert normal.calls==[('begin',1),('end',1)]
    exceptional=TimerDouble()
    try:
        with rtc_agent.WindowsTimerResolution(winmm=exceptional):raise RuntimeError('Agent stopped')
    except RuntimeError:pass
    else:raise AssertionError('The context must preserve agent failures.')
    assert exceptional.calls==[('begin',1),('end',1)]
    for timer in [TimerDouble(result=1),TimerDouble(error=True)]:
        with rtc_agent.WindowsTimerResolution(winmm=timer):pass
        assert timer.calls==[('begin',1)]

    # main wraps agent initialization as well as service work in the context.
    events=[]
    class ContextDouble:
        def __enter__(self):events.append('begin')
        def __exit__(self,*args):events.append('end')
    async def stopped_agent():events.append('run');raise RuntimeError('Initialization failed')
    original_context=rtc_agent.WindowsTimerResolution;original_agent=rtc_agent.run_agent
    rtc_agent.WindowsTimerResolution=ContextDouble;rtc_agent.run_agent=stopped_agent
    try:
        try:asyncio.run(rtc_agent.main())
        except RuntimeError:pass
        assert events==['begin','run','end']
    finally:rtc_agent.WindowsTimerResolution=original_context;rtc_agent.run_agent=original_agent

session=rtc_agent.DesktopSession.__new__(rtc_agent.DesktopSession)
session.session_id='numeric-report';session.last_report_log=0;session.track=None
report={'inputAckMs':8.2,'audioBufferMs':30,'audioTotalSamplesDuration':1.2,'audioTotalSamplesReceived':48000,'audioConcealedSamplesDelta':0,'audioTotalPlayoutDelay':2,'audioTotalSamplesCount':40,'audioPlayoutMs':50,'audioPlayoutSamplesDuration':1.2,'audioBaseLatencyMs':5.3,'audioOutputLatencyMs':12.0,'videoDecodedDelta':60,'videoDroppedDelta':0,'playbackFramesDelta':59,'playbackDroppedDelta':1,'fps':True,'bufferMs':float('nan'),'decodeMs':None,'mbps':'bad'}
output=io.StringIO()
with contextlib.redirect_stdout(output):session.report_receiver({'receiverReport':report},now=10)
actual=json.loads(output.getvalue())['receiverReport']
assert actual=={key:value for key,value in report.items() if key not in ['fps','bufferMs','decodeMs','mbps']}
print('Timer begin/end verified on normal exit, agent failure and unavailable API; numeric receiver diagnostics verified. No desktop or network IO.')
