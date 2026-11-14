"""Synthetic file-store acceptance. Optional multi-GB IO stays in own D: tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import tracemalloc
from unittest.mock import patch
import errno
from types import SimpleNamespace

from file_transfer import FileReceiver, TransferError, CHUNK_BYTES, valid_name
from file_transfer_progress import ProgressReporter


def expect(code, function, *args):
    try:
        function(*args)
    except TransferError as error:
        assert error.code == code, (code, error.code)
    else:
        raise AssertionError('Expected ' + code)


def upload(store, name, data, owner='fixture'):
    value = store.begin(name, len(data), owner)
    for offset in range(0, len(data), CHUNK_BYTES):
        store.write(value['id'], value['token'], owner, offset, data[offset:offset + CHUNK_BYTES])
    done = store.finish(value['id'], value['token'], owner, hashlib.sha256(data).hexdigest())
    assert Path(done['path']).read_bytes() == data
    assert done['status'] == 'complete' and done['committed'] == len(data)
    return value, done


def main(root, large_bytes):
    results = []
    store = FileReceiver(root / 'received')
    for name, data in [('small.txt', b'synthetic\x00\xff'), ('empty', b''), ('no_extension', b'no suffix'), ('binary.exe', bytes(range(256)) * 5), ('page.html', b'<script>synthetic only</script>')]:
        upload(store, name, data)
    first, done = upload(store, 'repeat.bin', b'first')
    _, second = upload(store, 'repeat.bin', b'second')
    assert done['path'] != second['path'] and Path(done['path']).read_bytes() == b'first'
    assert store.finish(first['id'], first['token'], 'fixture', hashlib.sha256(b'first').hexdigest())['path'] == done['path']
    results.append('small/zero/no-extension/binary/all-suffixes/collision/idempotent-completion')
    expect('invalid_name', valid_name, 'COM¹.bin')
    for name in ['../escape', 'a/b', 'a\\b', 'C:escape', 'file:ads', 'NUL.txt', 'COM¹.bin', 'bad.', 'bad ', '', '..']:
        expect('invalid_name', valid_name, name)
    value = store.begin('resume.bin', CHUNK_BYTES * 2, 'old')
    block = bytes(range(256)) * (CHUNK_BYTES // 256)
    store.write(value['id'], value['token'], 'old', 0, block)
    expect('unauthorized', store.resume, value['id'], 'x' * 43, 'attacker')
    expect('invalid_transfer', store.resume, '../escape', value['token'], 'fixture')
    resumed = store.resume(value['id'], value['token'], 'new')
    assert resumed['committed'] == len(block)
    expect('lease_lost', store.write, value['id'], value['token'], 'old', len(block), block)
    store.write(value['id'], value['token'], 'new', 0, block)  # byte-identical duplicate
    expect('chunk_conflict', store.write, value['id'], value['token'], 'new', 0, b'x' * len(block))
    store.pause(value['id'], value['token'], 'new')
    restarted = FileReceiver(root / 'received')
    restored = restarted.resume(value['id'], value['token'], 'restart')
    assert restored['committed'] == len(block) and restored['prefixSha256'] == hashlib.sha256(block).hexdigest()
    restarted.write(value['id'], value['token'], 'restart', len(block), block)
    done = restarted.finish(value['id'], value['token'], 'restart', hashlib.sha256(block * 2).hexdigest())
    assert Path(done['path']).read_bytes() == block * 2
    restarted.close()
    results.append('wrong-capability/path/reconnect/takeover/stale-writer/duplicate/restart-resume')
    value = store.begin('cancel.bin', 1024, 'cancel')
    store.write(value['id'], value['token'], 'cancel', 0, b'a' * 512)
    store.cancel(value['id'], value['token'], 'cancel')
    assert not (store.incoming / (value['id'] + '.part')).exists()
    assert not (store.root / 'cancel.bin').exists()
    value = store.begin('checksum.bin', 3, 'bad')
    store.write(value['id'], value['token'], 'bad', 0, b'abc')
    expect('checksum_failed', store.finish, value['id'], value['token'], 'bad', hashlib.sha256(b'xyz').hexdigest())
    assert not (store.root / 'checksum.bin').exists()
    disk = FileReceiver(root / 'low-space', disk_usage=lambda path: SimpleNamespace(free=0))
    expect('disk_full', disk.begin, 'file', 1, 'disk')
    assert not list(disk.incoming.glob('*.part'))
    results.append('cancel/checksum-failure/low-disk/no-incomplete-publication')
    value = store.begin('fault_fsync', 3, 'fault')
    store.write(value['id'], value['token'], 'fault', 0, b'abc')
    with patch('file_transfer.os.fsync', side_effect=OSError(errno.EIO, 'synthetic fault')):
        expect('disk_error', store.finish, value['id'], value['token'], 'fault', hashlib.sha256(b'abc').hexdigest())
    assert store.records[value['id']]['_stream'] is None
    assert store.records[value['id']]['status'] == 'paused' and not (store.root / 'fault_fsync').exists()
    resumed = store.resume(value['id'], value['token'], 'fault2')
    assert resumed['committed'] == 0  # Failed checkpoint never acknowledges new bytes.
    store.write(value['id'], value['token'], 'fault2', 0, b'abc')
    assert store.finish(value['id'], value['token'], 'fault2', hashlib.sha256(b'abc').hexdigest())['status'] == 'complete'
    value = store.begin('fault_write', 3, 'fault')
    stream = store.records[value['id']]['_stream']
    class BrokenWrite:
        def write(self, data):
            raise OSError(errno.ENOSPC, 'synthetic disk full')
        def close(self):
            stream.close()
    store.records[value['id']]['_stream'] = BrokenWrite()
    expect('disk_full', store.write, value['id'], value['token'], 'fault', 0, b'abc')
    assert not (store.incoming / (value['id'] + '.part')).exists() and not (store.root / 'fault_write').exists()
    results.append('injected-mid-write-ENOSPC/verify-fsync-EIO/closed-handles/recoverable-checkpoint')
    store.close()
    launches = []
    progress_path = root / 'synthetic-progress.json'
    reporter = ProgressReporter(progress_path, launch=lambda: launches.append(True))
    observed = FileReceiver(root / 'progress-store', notify=reporter)
    _, completed = upload(observed, '进度_🌟.unknown', b'synthetic progress')
    progress = json.loads(progress_path.read_text(encoding='utf-8'))['items'][0]
    assert progress['committed'] == progress['size'] and progress['status'] == 'complete'
    assert progress['path'] == completed['path'] and progress['speed'] == 0 and launches == [True]
    assert 'token' not in progress and 'tokenHash' not in progress
    observed.close()
    results.append('native-progress-IPC/complete-path/durable-bytes/window-launch-request-double/no-secret-in-status')
    crashed = FileReceiver(root / 'interrupted-verification')
    value = crashed.begin('recover_verify', 3, 'before-crash')
    crashed.write(value['id'], value['token'], 'before-crash', 0, b'abc')
    crashed.pause(value['id'], value['token'], 'before-crash')
    manifest = crashed.incoming / (value['id'] + '.json')
    state = json.loads(manifest.read_text(encoding='utf-8'))
    state['status'] = 'verifying'  # Exact journal state after fsync, before readback completes.
    manifest.write_text(json.dumps(state), encoding='utf-8')
    restarted = FileReceiver(crashed.root)
    assert restarted.resume(value['id'], value['token'], 'after-crash')['committed'] == 3
    assert restarted.finish(value['id'], value['token'], 'after-crash', hashlib.sha256(b'abc').hexdigest())['status'] == 'complete'
    restarted.close()
    crashed.close()
    results.append('persisted-verifying-state-recovery/recheck-before-completion')
    paused = FileReceiver(root / 'pause-fault')
    value = paused.begin('pause_fault', 3, 'old-wire')
    paused.write(value['id'], value['token'], 'old-wire', 0, b'abc')
    with patch('file_transfer.os.fsync', side_effect=OSError(errno.EIO, 'synthetic pause fault')):
        paused.pause(value['id'], value['token'], 'old-wire')
    record = paused.records[value['id']]
    assert record['_stream'] is None and record['_owner'] is None and record['status'] == 'paused'
    assert paused.resume(value['id'], value['token'], 'new-wire')['committed'] == 0
    paused.cancel(value['id'], value['token'], 'new-wire')
    paused.close()
    results.append('pause-fsync-fault/handle-and-lease-release/slot-recovery')
    if large_bytes:
        large = FileReceiver(root / 'large')
        value = large.begin('multi_gb', large_bytes, 'large')
        expected, offset = hashlib.sha256(), 0
        tracemalloc.start()
        started = time.monotonic()
        while offset < large_bytes:
            data = block[:min(len(block), large_bytes - offset)]
            large.write(value['id'], value['token'], 'large', offset, data)
            expected.update(data)
            offset += len(data)
        done = large.finish(value['id'], value['token'], 'large', expected.hexdigest())
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert Path(done['path']).stat().st_size == large_bytes and done['committed'] == large_bytes
        assert done['sha256'] == expected.hexdigest()
        assert peak < 16 * 1024 ** 2, 'Python tracked heap must remain bounded'
        results.append({'multiGBBytes': large_bytes, 'elapsedSeconds': round(time.monotonic() - started, 2), 'pythonTrackedPeakBytes': peak, 'diskReadbackSha256Matched': True})
        large.close()
    print(json.dumps({'checks': results, 'syntheticOnly': True, 'executedFiles': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='D:/DIANNOW/FileTransferTests')
    parser.add_argument('--large-bytes', type=int, default=0)
    args = parser.parse_args()
    base = Path(args.base).absolute()
    base.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='store-', dir=base))
    try:
        main(directory, args.large_bytes)
    finally:
        # Delete only this test's own exact directory, never a computed parent.
        assert directory.resolve().parent == base.resolve() and directory.name.startswith('store-')
        shutil.rmtree(directory)
