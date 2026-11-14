"""Route test imports existing agent definitions but never constructs devices."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
import rtc_agent


async def main():
    calls = []
    class Files:
        async def answer(self, offer, identity, servers):
            calls.append((offer, identity, servers))
            return {'type': 'answer', 'sdp': 'synthetic-file-only'}
    agent = rtc_agent.DesktopAgent.__new__(rtc_agent.DesktopAgent)
    agent.file_service = Files()
    agent.ice_servers = []
    # sessions/camera/input/audio_router deliberately absent: any media path fails.
    with patch('rtc_agent.DesktopSession', side_effect=AssertionError('File offers must not construct desktop sessions')):
        result = await agent.answer({'purpose': 'file-transfer', 'type': 'offer', 'sdp': 'synthetic'}, 'file-test')
    assert result['sdp'] == 'synthetic-file-only' and calls[0][1] == 'file-test'
    print('PASS file offer bypasses desktop sessions/capture/audio/input and preserves independent ICE policy; no devices or network')


asyncio.run(main())
