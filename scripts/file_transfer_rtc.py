"""File-only WebRTC association. Never creates media tracks or input handlers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import struct
import time
import uuid

from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from file_transfer import FileReceiver, TransferError, CHUNK_BYTES, WINDOW_BYTES, MAX_FILE_BYTES

LABEL = 'diannow-files-v1'
MAGIC = b'DFT1'
HEADER = struct.Struct('!4s16sQ')


def frame(identity, offset, data):
    return HEADER.pack(MAGIC, bytes.fromhex(identity), offset) + data


class FileWire:
    def __init__(self, service, channel):
        self.service, self.channel = service, channel
        self.owner = uuid.uuid4().hex
        self.queue = asyncio.Queue(maxsize=128)
        self.bound, self.last_ack = {}, {}
        self.hello = False
        self.closed = False
        self.last_activity = time.monotonic()
        self.task = asyncio.create_task(self.run())

    def send(self, value):
        if self.channel.readyState == 'open' and self.channel.bufferedAmount < 256 * 1024:
            self.channel.send(json.dumps(value, ensure_ascii=False, separators=(',', ':')))

    def progress(self, value):
        if value['id'] in self.bound and not self.closed:
            self.send({'type': 'status', **value})

    def receive(self, message):
        if self.closed:
            return
        if not isinstance(message, (str, bytes)) or len(message) > (CHUNK_BYTES + HEADER.size if isinstance(message, bytes) else 8192):
            self.send({'type': 'error', 'code': 'invalid_message', 'message': '传输消息过大或格式无效。'})
            asyncio.create_task(self.close())
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.send({'type': 'error', 'code': 'backpressure', 'message': '接收队列已满，请重连续传。'})
            asyncio.create_task(self.close())

    async def call(self, function, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.service.pool, function, *args)

    async def run(self):
        while not self.closed:
            message, event = None, {}
            try:
                message = await asyncio.wait_for(self.queue.get(), .25 if self.bound else 30)
            except asyncio.TimeoutError:
                if not self.hello or time.monotonic() - self.last_activity > 120:
                    self.send({'type': 'error', 'code': 'idle', 'message': '传输连接空闲过久，请重连。'})
                    asyncio.create_task(self.close())
                    return
                continue
            if message is None or self.closed:
                return
            self.last_activity = time.monotonic()
            try:
                if isinstance(message, bytes):
                    if not self.hello or len(message) <= HEADER.size:
                        raise TransferError('protocol_required', '请先完成文件协议协商。')
                    magic, raw_id, offset = HEADER.unpack_from(message)
                    identity = raw_id.hex()
                    event = {'id': identity}
                    if magic != MAGIC or identity not in self.bound:
                        raise TransferError('unauthorized', '分块不属于当前传输连接。')
                    value = await self.call(self.service.store.write, identity, self.bound[identity], self.owner, offset, message[HEADER.size:])
                    if value['committed'] != self.last_ack.get(identity):
                        self.last_ack[identity] = value['committed']
                        self.send({'type': 'status', **value})
                    continue
                parsed = json.loads(message)
                if not isinstance(parsed, dict):
                    raise TransferError('invalid_message', '文件协议消息无效。')
                event = parsed
                kind = event.get('type')
                if kind == 'hello' and event.get('protocol') == 1:
                    self.hello = True
                    self.send({'type': 'ready', 'protocol': 1, 'chunkBytes': CHUNK_BYTES, 'windowBytes': WINDOW_BYTES, 'maxFileBytes': MAX_FILE_BYTES, 'maxConcurrent': 2})
                    continue
                if not self.hello:
                    raise TransferError('protocol_required', '请先完成文件协议协商。')
                if kind == 'ping':
                    self.send({'type': 'pong', 'requestId': event.get('requestId')})
                    continue
                if kind == 'begin':
                    value = await self.call(self.service.store.begin, event.get('name'), event.get('size'), self.owner)
                    self.bound[value['id']] = value['token']
                    response = 'accepted'
                elif kind == 'resume':
                    identity, token = event.get('id'), event.get('token')
                    await self.call(self.service.store.authorize, identity, token)
                    self.bound[identity] = token
                    value = await self.call(self.service.store.resume, identity, token, self.owner)
                    response = 'resumed'
                elif kind in ('finish', 'cancel'):
                    identity, token = event.get('id'), event.get('token')
                    if identity not in self.bound or token != self.bound[identity]:
                        raise TransferError('unauthorized', '传输凭证不属于当前连接。')
                    if kind == 'finish':
                        value = await self.call(self.service.store.finish, identity, token, self.owner, event.get('sha256'))
                        response = 'complete'
                    else:
                        value = await self.call(self.service.store.cancel, identity, token, self.owner)
                        response = 'cancelled'
                else:
                    raise TransferError('invalid_message', '不支持的文件操作。')
                self.send({'type': response, 'requestId': event.get('requestId'), **value})
            except TransferError as error:
                self.send({'type': 'error', 'requestId': event.get('requestId'), 'id': event.get('id'), 'code': error.code, 'message': str(error)})
            except (ValueError, TypeError, KeyError, struct.error):
                self.send({'type': 'error', 'requestId': event.get('requestId'), 'code': 'invalid_message', 'message': '文件协议消息无效。'})
            except Exception:
                self.send({'type': 'error', 'requestId': event.get('requestId'), 'code': 'disk_error', 'message': '接收失败，已落盘部分可在重连后检查续传。'})

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if self.task is not asyncio.current_task():
            await asyncio.gather(self.task, return_exceptions=True)
        for identity, token in list(self.bound.items()):
            try:
                await self.call(self.service.store.pause, identity, token, self.owner)
            except (TransferError, OSError):
                pass
        self.channel.close()


class FilePeer:
    def __init__(self, service, identity):
        self.service, self.identity = service, identity
        self.pc = None
        self.wire = None
        self.created_at = time.monotonic()
        self.closed = False
        self.answer_result = None
        self.watchdog = None

    async def answer(self, offer, ice_servers):
        # Reject media-bearing offers before constructing any media object.
        if 'm=audio' in offer.get('sdp', '') or 'm=video' in offer.get('sdp', ''):
            raise TransferError('file_only', '文件连接不能包含音视频。')
        self.pc = pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        @pc.on('datachannel')
        def on_channel(channel):
            if channel.label != LABEL or self.wire is not None or not channel.ordered:
                channel.close()
                return
            self.wire = FileWire(self.service, channel)
            channel.on('message', self.wire.receive)
            @channel.on('close')
            def on_close():
                asyncio.create_task(self.close())
        @pc.on('connectionstatechange')
        async def on_state():
            if pc.connectionState in ('failed', 'closed'):
                await self.close()
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer['sdp'], type=offer['type']))
        await pc.setLocalDescription(await pc.createAnswer())
        self.answer_result = {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}
        async def watch():
            while not self.closed:
                await asyncio.sleep(5)
                if self.pc.connectionState != 'connected' and time.monotonic() - self.created_at > 40:
                    await self.close()
        self.watchdog = asyncio.create_task(watch())
        return self.answer_result

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.watchdog and self.watchdog is not asyncio.current_task():
            self.watchdog.cancel()
        try:
            if self.wire:
                await self.wire.close()
        finally:
            try:
                if self.pc:
                    await self.pc.close()
            finally:
                self.service.peers.pop(self.identity, None)


class FileTransferService:
    def __init__(self, root=None, *, notify=None):
        self.loop = asyncio.get_running_loop()
        self.notify = notify or (lambda value: None)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='FileTransferDisk')
        self.store = FileReceiver(**({'root': root} if root is not None else {}), notify=self.progress)
        self.peers = {}

    def progress(self, value):
        try:
            self.notify(value)
        except OSError:
            pass  # A status window must never interrupt a durable file write.
        def dispatch():
            for peer in tuple(self.peers.values()):
                if peer.wire:
                    peer.wire.progress(value)
        self.loop.call_soon_threadsafe(dispatch)

    async def answer(self, offer, identity, ice_servers):
        existing = self.peers.get(identity)
        if existing and existing.answer_result:
            return existing.answer_result
        if len(self.peers) >= 4:
            raise TransferError('busy', '文件连接过多，请稍后重试。')
        peer = FilePeer(self, identity)
        self.peers[identity] = peer
        try:
            return await peer.answer(offer, ice_servers)
        except BaseException:
            await peer.close()
            raise

    async def close(self):
        try:
            await asyncio.gather(*(peer.close() for peer in tuple(self.peers.values())), return_exceptions=True)
        finally:
            try:
                await self.loop.run_in_executor(self.pool, self.store.close)
            finally:
                self.pool.shutdown(wait=True, cancel_futures=True)
