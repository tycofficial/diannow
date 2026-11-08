"""Bounded synthetic traffic through existing configured TURN allocations.

No desktop/audio data, no new service, and no credential/address output.
Both clients use this machine's monotonic clock. Delay includes the complete
client -> relay -> client path; this is not a phone measurement.
"""
import argparse, asyncio, ctypes, json, math, statistics, struct, time
import aiohttp
from aioice.turn import create_turn_endpoint
from turn_route import _configured_udp_endpoints
from realtime_clock import run_realtime

ORIGIN='https://desktop-live-tianw-1105.tyc130923.chatgpt.site'
HEADER=struct.Struct('!4sBId')


class Receiver(asyncio.DatagramProtocol):
    def __init__(self):self.samples={};self.errors=0
    def connection_made(self,transport):self.transport=transport
    def datagram_received(self,data,addr):
        if len(data)<HEADER.size:return
        magic,phase,sequence,started=HEADER.unpack_from(data)
        if magic==b'DNP1':self.samples.setdefault(phase,{})[sequence]=(time.perf_counter(),started,len(data))
    def error_received(self,error):self.errors+=1


async def close_transport(transport):
    if transport is None:return
    transport.close()
    task=getattr(transport,'_TurnTransport__close_task',None)
    if task:
        try:await asyncio.wait_for(task,2)
        except (Exception,asyncio.CancelledError):pass


async def main(args):
    endpoint_factory=create_turn_endpoint
    if args.proxy_tcp:
        from turn_proxy import install_turn_proxy
        from aioice import turn
        install_turn_proxy('http://127.0.0.1:7890',prefer_direct=False)
        endpoint_factory=turn.create_turn_endpoint
    async with aiohttp.ClientSession(trust_env=True,timeout=aiohttp.ClientTimeout(total=12)) as http:
        async with http.get(ORIGIN+'/api/ice') as response:
            response.raise_for_status();config=await response.json()
    endpoints=_configured_udp_endpoints(config)
    selected=range(len(endpoints)) if args.endpoint is None else [args.endpoint]
    for index in selected:
        if index not in range(len(endpoints)):raise ValueError('Configured endpoint index unavailable')
        server,_,host,port=endpoints[index];a=b=None;stage='first-allocation'
        try:
            options=dict(server_addr=(host,port),username=server.get('username'),password=server.get('credential'),transport=args.transport)
            a,ra=await asyncio.wait_for(endpoint_factory(Receiver,**options),8)
            stage='second-allocation'
            b,rb=await asyncio.wait_for(endpoint_factory(Receiver,**options),8)
            stage='channel-bind'
            aa=a.get_extra_info('sockname');ba=b.get_extra_info('sockname')
            await asyncio.wait_for(asyncio.gather(a._TurnTransport__inner_protocol.send_data(b'init',ba),b._TurnTransport__inner_protocol.send_data(b'init',aa)),8)
            await asyncio.sleep(.3)
            stage='traffic'
            for phase,mbps in enumerate(args.rates):
                interval=args.packet_size*8/(mbps*1e6);start=time.perf_counter();sent=0
                while time.perf_counter()-start<args.seconds:
                    due=start+sent*interval;now=time.perf_counter()
                    if due>now:await asyncio.sleep(due-now)
                    packet=HEADER.pack(b'DNP1',phase,sent,time.perf_counter())+bytes(args.packet_size-HEADER.size)
                    a.sendto(packet,ba);sent+=1
                    if sent%8==0:await asyncio.sleep(0)
                duration=time.perf_counter()-start
                await asyncio.sleep(2)
                samples=rb.samples.pop(phase,{})
                ordered=sorted(samples.values());latencies=sorted(1000*(r-s) for r,s,_ in ordered)
                gaps=[1000*(ordered[i][0]-ordered[i-1][0]) for i in range(1,len(ordered))]
                received=len(samples)
                p95=latencies[min(len(latencies)-1,math.ceil(.95*len(latencies))-1)] if latencies else None
                print(json.dumps({'endpoint':index,'transport':args.transport,'proxy':args.proxy_tcp,'targetMbps':mbps,'packetBytes':args.packet_size,'seconds':round(duration,2),'sentPackets':sent,'receivedPackets':received,'notReceivedWithinDrainPercent':round(100*(sent-received)/max(1,sent),3),'sentMbps':round(sent*args.packet_size*8/duration/1e6,3),'receivedMbps':round(received*args.packet_size*8/duration/1e6,3),'medianPathDelayMs':round(statistics.median(latencies),2) if latencies else None,'p95PathDelayMs':round(p95,2) if p95 else None,'maxReceiveGapMs':round(max(gaps),2) if gaps else None,'socketErrors':ra.errors+rb.errors,'syntheticOnly':True}),flush=True)
        except Exception as error:
            print(json.dumps({'endpoint':index,'transport':args.transport,'proxy':args.proxy_tcp,'stage':stage,'error':type(error).__name__,'syntheticOnly':True}),flush=True)
        finally:await asyncio.gather(close_transport(a),close_transport(b))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--endpoint',type=int);parser.add_argument('--transport',choices=['udp','tcp'],default='udp');parser.add_argument('--seconds',type=int,default=10);parser.add_argument('--rates',default='1.5,8');parser.add_argument('--packet-size',type=int,default=1200);parser.add_argument('--proxy-tcp',action='store_true');args=parser.parse_args();args.rates=[float(x) for x in args.rates.split(',')]
    if args.proxy_tcp and args.transport!='tcp':parser.error('--proxy-tcp requires --transport tcp')
    if not 3<=args.seconds<=30 or not 200<=args.packet_size<=1400 or len(args.rates)>4 or any(not 0<r<=12 for r in args.rates):parser.error('Use bounded rates/durations/packet sizes')
    ctypes.windll.winmm.timeBeginPeriod(1)
    try:run_realtime(main(args))
    finally:ctypes.windll.winmm.timeEndPeriod(1)
