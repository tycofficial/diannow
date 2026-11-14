"""Ephemeral synthetic file-only browser fixture; bound only to IPv4 loopback."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid
from unittest.mock import patch
from aiohttp import web
from file_transfer_rtc import FileTransferService


async def main(root):
    service = FileTransferService(root)
    answers, workers = {}, set()
    stopped = asyncio.Event()
    source = Path(__file__).resolve().parents[1] / 'web' / 'file-transfer.mjs'
    async def page(request):
        return web.Response(text='''<!doctype html><meta charset="utf-8"><nav></nav><script type="module">
import {FileTransferPanel} from '/file-transfer.mjs';
window.peakBuffered=0;window.peakOutstanding=0;window.reconnects=0;
window.panel=new FileTransferPanel({iceServers:()=>[]});window.client=panel.client;
const render=client.onChange;client.onChange=items=>{render(items);
 window.result=items.map(t=>({name:t.file.name,size:t.file.size,status:t.status,committed:t.committed,path:t.path,error:t.error,attempts:t.attempts}));
 const task=items.find(t=>t.status==='receiving');
 if(task&&task.committed>1048576&&!window.dropped&&window.dropOnce){window.dropped=true;window.reconnects++;window.client.disconnect(new Error('synthetic connection interruption'));}
};
setInterval(()=>{window.peakBuffered=Math.max(window.peakBuffered,client.channel?.bufferedAmount||0);window.peakOutstanding=Math.max(window.peakOutstanding,...client.tasks.map(t=>Math.max(0,t.sent-t.committed)))},50);
document.querySelector('#filesPanel input').id='pick';document.querySelector('#files').click();
</script>''', content_type='text/html')
    async def module(request):
        return web.Response(text=source.read_text(encoding='utf-8'), content_type='text/javascript')
    async def offer(request):
        payload = await request.json()
        assert payload.get('purpose') == 'file-transfer'
        identity = str(uuid.uuid4())
        async def answer():
            await asyncio.sleep(.1)  # Guarantee at least one genuine HTTP 204 wait.
            answers[identity] = await service.answer(payload, identity, [])
        task = asyncio.create_task(answer())
        workers.add(task)
        task.add_done_callback(workers.discard)
        return web.json_response({'id': identity})
    async def answer(request):
        value = answers.get(request.match_info['identity'])
        return web.json_response(value) if value else web.Response(status=204)
    async def stop(request):
        stopped.set()
        return web.json_response({'ok': True})
    app = web.Application(client_max_size=128 * 1024)
    app.router.add_get('/', page)
    app.router.add_get('/file-transfer.mjs', module)
    app.router.add_post('/api/offer', offer)
    app.router.add_get('/api/answer/{identity}', answer)
    app.router.add_post('/shutdown', stop)
    runner = web.AppRunner(app)
    with patch('aioice.ice.get_host_addresses', lambda **kwargs: ['127.0.0.1']):
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        print(json.dumps({'port': site._server.sockets[0].getsockname()[1]}), flush=True)
        try:
            await asyncio.wait_for(stopped.wait(), 2100)
        finally:
            await asyncio.gather(*workers, return_exceptions=True)
            await service.close()
            await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    root = Path(args.root).absolute()
    assert root.parent.name.startswith('browser-') and root.parent.parent == Path('D:/DIANNOW/FileTransferTests').absolute()
    asyncio.run(main(root))
