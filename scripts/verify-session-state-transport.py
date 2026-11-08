"""Real local ICE/DTLS/SCTP: latest state survives an ordered-stream gap.

No desktop capture, devices, remote server or operating-system input is used.
"""
import asyncio
import json
import time
from aiortc import RTCPeerConnection, RTCConfiguration
from aiortc.rtcsctptransport import DataChunk
from session_state import SessionStateGate


async def main():
    viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    desktop = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    ordered = viewer.createDataChannel('input', ordered=True)
    state = viewer.createDataChannel('state', ordered=False, maxRetransmits=0)
    opened = asyncio.Event()
    replies = asyncio.Queue()
    state.on('open', opened.set)
    state.on('message', lambda message: replies.put_nowait(json.loads(message)))
    received_input, applied = [], []
    gate = SessionStateGate()
    dropped_acks = 0

    @desktop.on('datachannel')
    def channel(remote):
        @remote.on('message')
        def message(data):
            nonlocal dropped_acks
            if remote.label == 'input':
                received_input.append(data)
            else:
                ack = gate.handle(json.loads(data), applied.append)
                if dropped_acks < 2:
                    dropped_acks += 1
                else:
                    remote.send(json.dumps(ack))

    try:
        await viewer.setLocalDescription(await viewer.createOffer())
        await desktop.setRemoteDescription(viewer.localDescription)
        await desktop.setLocalDescription(await desktop.createAnswer())
        await viewer.setRemoteDescription(desktop.localDescription)
        await asyncio.wait_for(opened.wait(), 5)
        original_send = viewer.sctp._send_chunk
        blocked = []

        async def with_gap(chunk):
            if isinstance(chunk, DataChunk) and chunk.stream_id == ordered.id and chunk.user_data == b'ordered-blocker':
                blocked.append(chunk)
                return
            await original_send(chunk)

        viewer.sctp._send_chunk = with_gap
        ordered.send('ordered-blocker')
        ordered.send('later-ordered-message')
        event = {'type': 'sessionState', 'revision': 7, 'state': {'type': 'settings', 'quality': '1080', 'fps': 60, 'fixedProfile': True}}
        started = time.monotonic()
        for _ in range(3):
            state.send(json.dumps(event))
            await asyncio.sleep(.06)
        ack = await asyncio.wait_for(replies.get(), 2)
        assert ack['appliedRevision'] == 7 and len(applied) == 1
        assert not received_input and blocked, 'Ordered input must still be blocked during the successful state exchange'
        stale = {'type': 'sessionState', 'revision': 4, 'state': {**event['state'], 'quality': '480'}}
        state.send(json.dumps(stale))
        ack = await asyncio.wait_for(replies.get(), 2)
        assert ack['appliedRevision'] == 7 and len(applied) == 1
        assert not gate.allows_legacy(stale['state'])
        elapsed = (time.monotonic() - started) * 1000
        viewer.sctp._send_chunk = original_send
        await original_send(blocked[0])
        for _ in range(50):
            if len(received_input) == 2:
                break
            await asyncio.sleep(.02)
        assert received_input == ['ordered-blocker', 'later-ordered-message']
        print({'realLocalSctp': True, 'orderedStreamGapBypassed': True, 'droppedAcksRecovered': dropped_acks, 'settingsApplications': len(applied), 'elapsedMs': round(elapsed, 1), 'desktopInputInjected': False})
    finally:
        await asyncio.gather(viewer.close(), desktop.close())


asyncio.run(main())
