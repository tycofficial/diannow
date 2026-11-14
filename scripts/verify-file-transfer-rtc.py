"""Real loopback ICE/DTLS/SCTP and production receiver; no media or input imports."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import uuid
from unittest.mock import patch
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from file_transfer_rtc import FileTransferService, LABEL, frame


class Client:
    async def connect(self, service):
        self.queue = asyncio.Queue()
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.channel = self.pc.createDataChannel(LABEL, ordered=True)
        self.channel.on('message', lambda data: self.queue.put_nowait(json.loads(data)))
        await self.pc.setLocalDescription(await self.pc.createOffer())
        description = self.pc.localDescription
        answer = await service.answer({'sdp': description.sdp, 'type': description.type}, uuid.uuid4().hex, [])
        await self.pc.setRemoteDescription(RTCSessionDescription(**answer))
        for _ in range(300):
            if self.channel.readyState == 'open':
                break
            await asyncio.sleep(.01)
        assert self.channel.readyState == 'open', 'real SCTP failed to open'
        ready = await self.rpc({'type': 'hello', 'protocol': 1}, response='ready')
        assert ready['chunkBytes'] == 61440 and ready['windowBytes'] == 4194304
        return self

    async def rpc(self, event, response=None):
        identifier = uuid.uuid4().hex
        self.channel.send(json.dumps({**event, 'requestId': identifier}))
        async with asyncio.timeout(20):
            while True:
                value = await self.queue.get()
                if value.get('requestId') == identifier or response and value['type'] == response:
                    return value

    async def send(self, identity, data, start=0):
        offset = start
        while offset < len(data):
            while self.channel.bufferedAmount > 256 * 1024:
                await asyncio.sleep(.002)
            block = data[offset:offset + 61440]
            self.channel.send(frame(identity, offset, block))
            offset += len(block)

    async def close(self):
        if getattr(self, 'close_task', None):
            return
        self.close_task = asyncio.create_task(self.pc.close())
        try:
            await asyncio.wait_for(asyncio.shield(self.close_task), 5)
        except TimeoutError:
            for task in asyncio.all_tasks():
                chain, coro = [], task.get_coro()
                while coro is not None:
                    chain.append(getattr(coro, '__qualname__', type(coro).__name__))
                    coro = getattr(coro, 'cr_await', None)
                print('CLOSE TIMEOUT: ' + ' -> '.join(chain), flush=True)
            raise
        await asyncio.sleep(.1)


async def main(root):
    service = FileTransferService(root)
    clients = []
    try:
        with patch('aioice.ice.get_host_addresses', lambda **kwargs: ['127.0.0.1']):
            client = await Client().connect(service)
            print('SCTP open', flush=True)
            clients.append(client)
            accepted = await client.rpc({'type': 'begin', 'name': 'binary.exe', 'size': 3})
            assert accepted['type'] == 'accepted'  # Public begin is deliberate.
            await client.send(accepted['id'], b'abc')
            done = await client.rpc({'type': 'finish', 'id': accepted['id'], 'token': accepted['token'], 'sha256': hashlib.sha256(b'abc').hexdigest()})
            assert done['type'] == 'complete' and Path(done['path']).read_bytes() == b'abc'
            invalid = await client.rpc({'type': 'resume', 'id': accepted['id'], 'token': 'x' * 43})
            assert invalid['type'] == 'error' and invalid['code'] == 'unauthorized'
            bad = await client.rpc({'type': 'begin', 'name': '../escape', 'size': 1})
            assert bad['code'] == 'invalid_name'
            print('small/binary/capability/path passed', flush=True)
            for raw in ('[]', 'null', '"text"'):
                client.channel.send(raw)
                async with asyncio.timeout(5):
                    while (malformed := await client.queue.get())['type'] != 'error':
                        pass
                assert malformed['code'] == 'invalid_message'
            assert (await client.rpc({'type': 'ping'}))['type'] == 'pong'
            size = 8 * 1024 ** 2 + 7
            data = (bytes(range(256)) * ((size + 255) // 256))[:size]
            value = await client.rpc({'type': 'begin', 'name': 'resume_no_extension', 'size': size})
            await client.send(value['id'], data[:2 * 1024 ** 2])
            await client.rpc({'type': 'ping'})  # Drains all queued chunks before disconnect.
            print('prefix sent; closing first peer', flush=True)
            await client.close()
            print('first peer closed', flush=True)
            client = await Client().connect(service)
            clients.append(client)
            resumed = await client.rpc({'type': 'resume', 'id': value['id'], 'token': value['token']})
            print('new peer resumed', flush=True)
            assert 0 < resumed['committed'] <= 2 * 1024 ** 2
            assert resumed['prefixSha256'] == hashlib.sha256(data[:resumed['committed']]).hexdigest()
            await client.send(value['id'], data, resumed['committed'])
            done = await client.rpc({'type': 'finish', 'id': value['id'], 'token': value['token'], 'sha256': hashlib.sha256(data).hexdigest()})
            assert done['status'] == 'complete' and Path(done['path']).read_bytes() == data
            value = await client.rpc({'type': 'begin', 'name': 'cancel', 'size': 10})
            cancelled = await client.rpc({'type': 'cancel', 'id': value['id'], 'token': value['token']})
            assert cancelled['status'] == 'cancelled' and not (root / 'cancel').exists()
            value = await client.rpc({'type': 'begin', 'name': 'bad_hash', 'size': 3})
            await client.send(value['id'], b'abc')
            bad = await client.rpc({'type': 'finish', 'id': value['id'], 'token': value['token'], 'sha256': '0' * 64})
            assert bad['code'] == 'checksum_failed' and not (root / 'bad_hash').exists()
            print(json.dumps({'realTransport': 'loopback ICE/DTLS/SCTP aiortc 1.15', 'checks': ['public-begin', 'binary-byte-exact-disk-sha256', 'wrong-resume-capability', 'invalid-path', '8MiB-disconnect-new-peer-resume', 'cancel', 'checksum-failure'], 'media': False, 'input': False}))
    finally:
        for client in clients:
            await client.close()
        await service.close()
        assert not service.peers


if __name__ == '__main__':
    base = Path('D:/DIANNOW/FileTransferTests').absolute()
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='rtc-', dir=base))
    try:
        asyncio.run(asyncio.wait_for(main(root), 100))
    finally:
        assert root.resolve().parent == base.resolve() and root.name.startswith('rtc-')
        shutil.rmtree(root)
