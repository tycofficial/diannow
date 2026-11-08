"""Bounded real-media check through public signaling; never inject desktop input.

An empty client ICE-server list measures this computer's direct media pipeline,
not cellular or relay latency. Only this probe's session is disconnected.
"""
import argparse
import asyncio
import json
import statistics
import time
import uuid
import aiohttp
import aiortc.rtcrtpreceiver as receiver_module
from aiortc import RTCPeerConnection,RTCConfiguration,RTCBundlePolicy,RTCSessionDescription,RTCIceServer
from aiortc.jitterbuffer import JitterBuffer
from realtime_clock import run_realtime
from delivery_acceptance import delivery_failures

ORIGIN='https://desktop-live-tianw-1105.tyc130923.chatgpt.site'


async def main(seconds,qualities,relay=False,relay_udp=False,relay_direct_tcp=False):
    servers=[]
    relay=relay or relay_udp or relay_direct_tcp
    if relay:
        if not relay_udp and not relay_direct_tcp:
            from turn_proxy import install_turn_proxy
            install_turn_proxy('http://127.0.0.1:7890',prefer_direct=False)
        async with aiohttp.ClientSession(trust_env=True,timeout=aiohttp.ClientTimeout(total=12)) as http:
            async with http.get(ORIGIN+'/api/ice') as response:
                response.raise_for_status();ice=await response.json()
        if relay_udp:
            from turn_route import select_udp_turn_route
            ice=await select_udp_turn_route(ice)
            if ice.get('selectedTurnTransport')!='udp':raise RuntimeError('Existing TURN endpoint did not pass the direct UDP allocation check')
        for server in ice.get('iceServers',[]):
            urls=server.get('urls',[]);urls=[urls] if isinstance(urls,str) else urls
            selected=[url for url in urls if url.startswith('turn:') and 'transport=udp' in url] if relay_udp else [url for url in urls if url.startswith('turns:') or url.startswith('turn:') and 'transport=tcp' in url]
            if selected:
                servers.append(RTCIceServer(urls=selected[:1],username=server.get('username'),credential=server.get('credential')));break
        if not servers:raise RuntimeError('No matching existing relay is configured')
    original_decoder=receiver_module.get_decoder
    def low_delay_decoder(codec):
        decoder=original_decoder(codec)
        if codec.mimeType.lower()=='video/h264':decoder.codec.thread_count=1
        return decoder
    receiver_module.get_decoder=low_delay_decoder
    peer=RTCPeerConnection(RTCConfiguration(iceServers=servers,bundlePolicy=RTCBundlePolicy.BALANCED))
    channel=peer.createDataChannel('input');video=peer.addTransceiver('video',direction='recvonly');peer.addTransceiver('audio',direction='recvonly')
    # A full 1080P IDR can exceed aiortc's default 128 packet slots. More
    # bounded slots prevent probe-only frame loss; this adds no playout delay.
    video.receiver._RTCRtpReceiver__jitter_buffer=JitterBuffer(capacity=1024,is_video=True)
    opened=asyncio.Event();tasks=[];frames=0;dimensions=None;frame_times=[];packet_frames=[];rtts=[];source_fps=[];diagnostics={}
    if relay:
        from aioice.ice import TransportPolicy
        from ice_consent import install_consent_window
        for transceiver in peer.getTransceivers():transceiver.receiver.transport.transport._connection._transport_policy=TransportPolicy.RELAY
        peer.sctp.transport.transport._connection._transport_policy=TransportPolicy.RELAY
    original_receive=video.receiver._handle_rtp_packet
    async def received(packet,*args,**kwargs):
        if packet.marker:packet_frames.append(time.perf_counter())
        return await original_receive(packet,*args,**kwargs)
    video.receiver._handle_rtp_packet=received
    @channel.on('open')
    def ready():opened.set()
    @channel.on('message')
    def message(data):
        nonlocal diagnostics
        value=json.loads(data)
        if value.get('type')=='pong':
            if isinstance(value.get('time'),(int,float)):rtts.append(1000*(time.perf_counter()-value['time']))
            if isinstance(value.get('fps'),(int,float)):source_fps.append(value['fps'])
            if isinstance(value.get('videoDiagnostics'),dict):diagnostics=value['videoDiagnostics']
    @peer.on('track')
    def track_received(track):
        async def consume():
            nonlocal frames,dimensions
            try:
                while True:
                    frame=await track.recv()
                    if track.kind=='video':frames+=1;dimensions=[frame.width,frame.height];frame_times.append(time.perf_counter())
            except asyncio.CancelledError:raise
            except Exception:pass
        tasks.append(asyncio.create_task(consume()))
    phases=[]
    try:
        await peer.setLocalDescription(await peer.createOffer())
        # Protocols exist only after ICE gathering. Installing earlier leaves
        # consent replies unobserved and makes the probe falsely expire at 30s.
        if relay:install_consent_window(peer)
        async with aiohttp.ClientSession(trust_env=True,cookie_jar=aiohttp.DummyCookieJar(),timeout=aiohttp.ClientTimeout(total=12)) as http:
            async with http.post(ORIGIN+'/api/offer',json={'viewerId':'stability-probe-'+str(uuid.uuid4()),'viewerVersion':'stability-probe','type':'offer','sdp':peer.localDescription.sdp,'separateControl':True,'preferences':{'quality':qualities[0],'fps':60,'fixedProfile':True,'relayTransport':'tcp' if relay and not relay_udp else None}}) as response:
                response.raise_for_status();identifier=(await response.json())['id']
            answer=None;deadline=time.perf_counter()+30
            while time.perf_counter()<deadline:
                async with http.get(ORIGIN+'/api/answer/'+identifier) as response:
                    if response.status==200:answer=await response.json();break
                    if response.status not in (204,404):response.raise_for_status()
                await asyncio.sleep(.4)
            if answer is None:raise TimeoutError('Desktop did not answer this probe')
        if relay:
            answer['sdp']='\r\n'.join(line for line in answer['sdp'].split('\r\n') if not line.startswith('a=candidate:') or ' typ relay' in line)
            if ' typ relay' not in answer['sdp']:raise RuntimeError('Desktop relay candidate unavailable')
        await peer.setRemoteDescription(RTCSessionDescription(**answer));await asyncio.wait_for(opened.wait(),25)
        channel.send(json.dumps({'type':'audioState','enabled':False}))
        for quality in qualities:
            channel.send(json.dumps({'type':'settings','quality':quality,'fps':60,'fixedProfile':True}))
            await asyncio.sleep(3)
            phase_started=time.perf_counter();previous=phase_started;start_count=frames;previous_count=frames;intervals=[];first_index=len(frame_times);rtp_index=len(packet_frames);rtt_index=len(rtts);source_index=len(source_fps)
            for second in range(seconds):
                if channel.readyState=='open':channel.send(json.dumps({'type':'ping','time':time.perf_counter()}))
                await asyncio.sleep(1)
                now=time.perf_counter();rate=(frames-previous_count)/(now-previous);intervals.append(rate);previous=now;previous_count=frames
                if second%5==4:print(json.dumps({'quality':quality,'elapsed':second+1,'decodedFps':round(rate,2),'dimensions':dimensions,'connected':peer.connectionState=='connected'}),flush=True)
                if peer.connectionState in ('failed','closed'):break
            times=frame_times[first_index:];gaps=[1000*(b-a) for a,b in zip(times,times[1:])];pings=rtts[rtt_index:]
            phase={'quality':quality,'seconds':round(time.perf_counter()-phase_started,2),'dimensions':dimensions,'averageDecodedFps':round(statistics.mean(intervals),2),'minimumOneSecondFps':round(min(intervals),2),'zeroFpsIntervals':sum(f<1 for f in intervals),'under45FpsIntervals':sum(f<45 for f in intervals),'maximumFrameGapMs':round(max(gaps),2) if gaps else None,'frameGapsOver100ms':sum(g>100 for g in gaps),'medianControlRttMs':round(statistics.median(pings),2) if pings else None,'reportedSourceFps':round(statistics.mean(source_fps[source_index:]),2) if len(source_fps)>source_index else None}
            received_times=packet_frames[rtp_index:];received_gaps=[1000*(b-a) for a,b in zip(received_times,received_times[1:])]
            phase['rtpFrameMarkerFps']=round(len(received_times)/(time.perf_counter()-phase_started),2);phase['maximumRtpFrameGapMs']=round(max(received_gaps),2) if received_gaps else None
            phase['videoDiagnostics']=diagnostics
            phases.append(phase);print(json.dumps({'phaseResult':phase}),flush=True)
            if peer.connectionState!='connected' or frames==start_count:break
        pairs=[{'local':pair.local_candidate.type,'remote':pair.remote_candidate.type} for pair in video.receiver.transport.transport._connection._nominated.values()]
        result={'phases':phases,'localDirectPipeline':bool(pairs) and all(p['local']!='relay' and p['remote']!='relay' for p in pairs),'forcedRelay':relay,'relayTransport':'udp' if relay_udp else 'direct-tcp' if relay_direct_tcp else 'proxy-tcp' if relay else None,'candidatePairs':pairs,'connectionState':peer.connectionState,'phoneNetworkMeasured':False,'desktopInput':False,'playback':False}
        print(json.dumps(result),flush=True)
        failures=delivery_failures(phases,peer.connectionState=='connected')
        print(json.dumps({'acceptancePassed':not failures,'acceptanceFailures':failures,'criteria':{'minimumAverageDecodedFps':58,'minimumOneSecondDecodedFps':50,'maximumFrameGapMs':100},'phoneNetworkMeasured':False}),flush=True)
        assert not failures,'; '.join(failures)
    finally:
        if channel.readyState=='open':
            channel.send(json.dumps({'type':'disconnect'}));await asyncio.sleep(.1)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True);await peer.close();receiver_module.get_decoder=original_decoder


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=20);parser.add_argument('--qualities',default='480,1080');route=parser.add_mutually_exclusive_group();route.add_argument('--relay',action='store_true');route.add_argument('--relay-udp',action='store_true');route.add_argument('--relay-direct-tcp',action='store_true');args=parser.parse_args()
    if not 5<=args.seconds<=120:parser.error('Use 5–120 seconds per quality')
    qualities=args.qualities.split(',')
    if not qualities or any(q not in ('480','720','1080') for q in qualities):parser.error('Use supported stream profiles')
    run_realtime(main(args.seconds,qualities,args.relay,args.relay_udp,args.relay_direct_tcp))
