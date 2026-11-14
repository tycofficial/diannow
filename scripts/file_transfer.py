"""Bounded, resumable file reception. No execution, media, GUI or network IO.

New uploads are public by explicit product choice. Resuming existing transfers
checks a per-transfer capability and a connection lease. Acknowledged
bytes have been flushed and fsynced; completion additionally reads the file
back, checks SHA-256, then publishes without replacing an existing file.
"""
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import threading
import time
import uuid

CHUNK_BYTES = 60 * 1024
WINDOW_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 ** 3
DEFAULT_ROOT = Path('D:/DIANNOW/Received')
ID = re.compile(r'^[0-9a-f]{32}$')
HASH = re.compile(r'^[0-9a-f]{64}$')


class TransferError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def valid_name(value):
    if (not isinstance(value, str) or not value or value in ('.', '..') or
            len(value.encode('utf-16-le', errors='surrogatepass')) > 360 or
            value[-1] in ' .' or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value) or
            any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise TransferError('invalid_name', '文件名含路径、非法字符或过长，请重命名后重试。')
    stem = value.split('.')[0].rstrip(' ').upper().translate(str.maketrans('¹²³', '123'))
    if stem in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise TransferError('invalid_name', 'Windows 保留文件名不可用，请重命名。')
    return value


def no_link(path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
        raise TransferError('unsafe_path', '接收目录或临时文件是链接，已拒绝写入。')


def safe_directory(path):
    path = Path(path).absolute()
    for parent in reversed((path, *path.parents)):
        no_link(parent)
    path.mkdir(parents=True, exist_ok=True)
    no_link(path)
    return path


class FileReceiver:
    def __init__(self, root=DEFAULT_ROOT, *, notify=None, disk_usage=shutil.disk_usage,
                 reserve_bytes=32 * 1024 ** 2, max_file_bytes=MAX_FILE_BYTES,
                 checkpoint_bytes=1024 ** 2, clock=time.monotonic):
        self.root = Path(root).absolute()
        self.incoming = self.root / '.incoming'
        self.notify = notify or (lambda value: None)
        self.disk_usage, self.reserve_bytes = disk_usage, reserve_bytes
        self.max_file_bytes, self.checkpoint_bytes, self.clock = max_file_bytes, checkpoint_bytes, clock
        self.records = {}
        self.lock = threading.RLock()

    def _paths(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise TransferError('invalid_transfer', '传输编号无效。')
        no_link(self.root)
        no_link(self.incoming)
        part, meta = self.incoming / (identity + '.part'), self.incoming / (identity + '.json')
        no_link(part)
        no_link(meta)
        return part, meta

    def _metadata(self, record):
        _, path = self._paths(record['id'])
        value = {key: item for key, item in record.items() if not key.startswith('_')}
        temporary = path.with_suffix('.tmp')
        no_link(temporary)
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _status(self, record, *, token=None):
        value = {key: record.get(key) for key in ('id', 'name', 'size', 'committed', 'status', 'path', 'sha256', 'error', 'verifiedBytes')}
        value['prefixSha256'] = record['prefixSha256']
        if token is not None:
            value['token'] = token
        return value

    def _report(self, record):
        self.notify(self._status(record))

    def _space(self, needed, exclude=None):
        outstanding = sum(max(0, r['size'] - r['committed']) for r in self.records.values()
                          if r['status'] == 'receiving' and r['id'] != exclude)
        if self.disk_usage(self.root).free < needed + outstanding + self.reserve_bytes:
            raise TransferError('disk_full', 'D 盘可用空间不足，未创建完整文件。')

    def _active(self, exclude=None):
        if sum(r['status'] == 'receiving' and r['id'] != exclude for r in self.records.values()) >= 2:
            raise TransferError('busy', '电脑正在接收两个文件，请稍后重试。')

    def _get(self, identity, token, owner=None):
        part, meta = self._paths(identity)
        record = self.records.get(identity)
        if record is None:
            if len(self.records) >= 128:
                for key, old in sorted(self.records.items(), key=lambda item: item[1].get('updatedAt', 0)):
                    if old.get('_stream') is None:
                        del self.records[key]
                        break
            try:
                if meta.stat().st_size > 8192:
                    raise ValueError()
                record = json.loads(meta.read_text(encoding='utf-8'))
                assert record['id'] == identity
                valid_name(record['name'])
                assert isinstance(record['size'], int) and not isinstance(record['size'], bool) and 0 <= record['size'] <= self.max_file_bytes
                assert 0 <= record['committed'] <= record['size']
                assert HASH.fullmatch(record['tokenHash']) and HASH.fullmatch(record['prefixSha256'])
                if record['status'] == 'verifying':
                    record['status'] = 'paused'  # Interrupted readback never counts as completion.
                record.update(_stream=None, _hasher=None, _owner=None, _written=record['committed'])
                self.records[identity] = record
            except (OSError, ValueError, KeyError, AssertionError, TypeError):
                raise TransferError('not_found', '找不到可续传记录，请重新选择文件。') from None
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise TransferError('unauthorized', '续传凭证无效。')
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(digest, record['tokenHash']):
            raise TransferError('unauthorized', '续传凭证无效。')
        if owner is not None and record.get('_owner') != owner:
            raise TransferError('lease_lost', '传输已由新的连接接管。')
        return record, part

    def begin(self, name, size, owner):
        name = valid_name(name)
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= self.max_file_bytes:
            raise TransferError('invalid_size', '文件大小无效或超过单文件 128 GiB 上限。')
        with self.lock:
            self._active()
            if len(self.records) >= 128:
                for key, old in sorted(self.records.items(), key=lambda item: item[1].get('updatedAt', 0)):
                    if old.get('_stream') is None:
                        del self.records[key]
                        break
            safe_directory(self.root)
            safe_directory(self.incoming)
            self._space(size)
            identity, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
            part, _ = self._paths(identity)
            stream = part.open('x+b', buffering=0)
            info = os.fstat(stream.fileno())
            record = {'id': identity, 'name': name, 'size': size, 'committed': 0, 'status': 'receiving',
                      'tokenHash': hashlib.sha256(token.encode()).hexdigest(), 'prefixSha256': hashlib.sha256().hexdigest(),
                      'createdAt': time.time(), 'updatedAt': time.time(), 'fileIdentity': [info.st_dev, info.st_ino],
                      '_stream': stream, '_hasher': hashlib.sha256(), '_owner': owner, '_written': 0, '_checkpoint': self.clock()}
            self.records[identity] = record
            try:
                self._checkpoint(record, force=True)
            except OSError as error:
                self._fail(record, part, error)
            self._report(record)
            return self._status(record, token=token)

    def authorize(self, identity, token):
        with self.lock:
            return self._status(self._get(identity, token)[0])

    def _checkpoint(self, record, force=False):
        if not force and record['_written'] - record['committed'] < self.checkpoint_bytes and self.clock() - record['_checkpoint'] < .25:
            return False
        stream = record['_stream']
        stream.flush()
        os.fsync(stream.fileno())
        record['committed'] = record['_written']
        record['prefixSha256'] = record['_hasher'].copy().hexdigest()
        record['updatedAt'] = time.time()
        record['_checkpoint'] = self.clock()
        self._metadata(record)
        self._report(record)
        return True

    def resume(self, identity, token, owner):
        with self.lock:
            record, part = self._get(identity, token)
            if record['status'] == 'publishing':
                candidate = self.root / valid_name(record.get('candidateName'))
                no_link(candidate)
                if not part.exists() and candidate.exists() and candidate.stat().st_size == record['size']:
                    digest = hashlib.sha256()
                    with candidate.open('rb') as stream:
                        while block := stream.read(1024 ** 2):
                            digest.update(block)
                    if hmac.compare_digest(digest.hexdigest(), record.get('sha256', '')):
                        record.update(status='complete', path=str(candidate), _owner=None)
                        self._metadata(record)
                elif part.exists():
                    record['status'] = 'paused'
            if record['status'] == 'complete':
                return self._status(record)
            if record['status'] not in ('receiving', 'paused'):
                raise TransferError(record['status'], '该传输已结束，不能继续写入。')
            self._active(identity)
            self._space(record['size'] - record['committed'], identity)
            if record.get('_stream'):
                self._checkpoint(record, force=True)
            else:
                info = part.stat()
                if [info.st_dev, info.st_ino] != record['fileIdentity'] or info.st_size < record['committed']:
                    raise TransferError('corrupt_partial', '临时文件已改变，请重新传输。')
                stream = part.open('r+b', buffering=0)
                record['_stream'] = stream
                stream.truncate(record['committed'])
                hasher = hashlib.sha256()
                left = record['committed']
                reported = self.clock()
                try:
                    while left:
                        data = stream.read(min(left, 1024 ** 2))
                        if not data:
                            raise TransferError('corrupt_partial', '临时文件内容不完整。')
                        hasher.update(data)
                        left -= len(data)
                        if self.clock() - reported >= .25:
                            self.notify({**self._status(record), 'status': 'recovering', 'verifiedBytes': record['committed'] - left})
                            reported = self.clock()
                except BaseException:
                    stream.close()
                    record['_stream'] = None
                    raise
                if not hmac.compare_digest(hasher.hexdigest(), record['prefixSha256']):
                    stream.close()
                    record['_stream'] = None
                    raise TransferError('corrupt_partial', '临时文件校验失败，请重新传输。')
                record.update(_hasher=hasher, _written=record['committed'], _checkpoint=self.clock())
            record.update(status='receiving', _owner=owner)
            self._metadata(record)
            self._report(record)
            return self._status(record)

    def write(self, identity, token, owner, offset, data):
        if not isinstance(data, bytes) or not 0 < len(data) <= CHUNK_BYTES:
            raise TransferError('invalid_chunk', '文件分块大小无效。')
        with self.lock:
            record, part = self._get(identity, token, owner)
            if record['status'] != 'receiving':
                raise TransferError('not_receiving', '传输当前不可写入。')
            stream = record['_stream']
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset + len(data) > record['size']:
                raise TransferError('invalid_offset', '文件偏移超出范围。')
            if offset < record['_written']:
                if offset + len(data) > record['_written']:
                    raise TransferError('invalid_offset', '重复分块越过已接收边界。')
                stream.seek(offset)
                same = stream.read(len(data)) == data
                stream.seek(record['_written'])
                if not same:
                    raise TransferError('chunk_conflict', '重复分块内容不一致，请重新传输。')
                return self._status(record)
            if offset != record['_written']:
                raise TransferError('invalid_offset', '分块不连续，请按已落盘偏移续传。')
            try:
                view = memoryview(data)
                while view:
                    count = stream.write(view)
                    if not count:
                        raise OSError(errno.EIO, 'No write progress')
                    view = view[count:]
                record['_hasher'].update(data)
                record['_written'] += len(data)
                self._checkpoint(record)
            except OSError as error:
                self._fail(record, part, error)
            return self._status(record)

    def _fail(self, record, part, error):
        if record.get('_stream'):
            record['_stream'].close()
            record['_stream'] = None
        part.unlink(missing_ok=True)
        record.update(status='failed', error='disk_full' if getattr(error, 'errno', None) == errno.ENOSPC else 'disk_error', _owner=None)
        self._metadata(record)
        self._report(record)
        raise TransferError(record['error'], '磁盘空间不足或写入失败；未发布完整文件。') from None

    def finish(self, identity, token, owner, expected):
        with self.lock:
            try:
                return self._finish(identity, token, owner, expected)
            except OSError:
                record = self.records.get(identity)
                if record:
                    stream = record.get('_stream')
                    if stream:
                        try:
                            stream.close()
                        except OSError:
                            pass
                    record.update(_stream=None, _owner=None, error='disk_error')
                    # A rename may already have succeeded. Keep its journal so
                    # resume verifies that exact candidate instead of uploading twice.
                    if record['status'] not in ('publishing', 'complete'):
                        record['status'] = 'paused'
                    try:
                        self._metadata(record)
                    except OSError:
                        pass
                    self._report(record)
                raise TransferError('disk_error', '磁盘校验或保存失败；临时文件保留，请检查磁盘后重试。') from None

    def _finish(self, identity, token, owner, expected):
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise TransferError('invalid_hash', '完整性校验值无效。')
        with self.lock:
            record, part = self._get(identity, token)
            if record['status'] == 'complete':
                if not hmac.compare_digest(record['sha256'], expected):
                    raise TransferError('checksum_failed', '已保存文件的校验值不同。')
                return self._status(record)
            if record.get('_owner') != owner:
                raise TransferError('lease_lost', '传输已由新的连接接管。')
            if record['status'] != 'receiving' or record['_written'] != record['size']:
                raise TransferError('incomplete', '文件尚未全部接收。')
            record['status'] = 'verifying'
            self._checkpoint(record, force=True)
            stream = record['_stream']
            stream.seek(0)
            digest = hashlib.sha256()
            verified, reported = 0, self.clock()
            while data := stream.read(1024 ** 2):
                digest.update(data)
                verified += len(data)
                if self.clock() - reported >= .25:
                    record['verifiedBytes'] = verified
                    self._report(record)
                    reported = self.clock()
            actual = digest.hexdigest()
            stream.close()
            record['_stream'] = None
            if not hmac.compare_digest(actual, expected):
                part.unlink(missing_ok=True)
                record.update(status='failed', error='checksum_failed', _owner=None)
                self._metadata(record)
                self._report(record)
                raise TransferError('checksum_failed', '完整性校验失败，未保存为完整文件，请重试。')
            no_link(self.root)
            original = Path(record['name'])
            for number in range(10000):
                name = record['name'] if number == 0 else original.stem + f' ({number})' + original.suffix
                destination = self.root / name
                try:
                    record.update(status='publishing', candidateName=name, sha256=actual)
                    self._metadata(record)
                    if os.name == 'nt':
                        os.rename(part, destination)  # Windows refuses replacement.
                    else:
                        os.link(part, destination, follow_symlinks=False)
                        part.unlink()
                    break
                except FileExistsError:
                    continue
                except OSError as error:
                    record.update(status='paused', error='publish_failed', _owner=None)
                    self._metadata(record)
                    raise TransferError('publish_failed', '文件校验通过，但保存文件名失败，可重试续传。') from error
            else:
                raise TransferError('name_conflict', '同名文件过多，请重命名后重试。')
            record.update(status='complete', path=str(destination), sha256=actual, verifiedBytes=record['size'], _owner=None)
            self._metadata(record)
            self._report(record)
            return self._status(record)

    def pause(self, identity, token, owner):
        with self.lock:
            try:
                record, _ = self._get(identity, token, owner)
            except TransferError as error:
                if error.code == 'lease_lost':
                    return
                raise
            if record['status'] == 'receiving':
                try:
                    self._checkpoint(record, force=True)
                except OSError:
                    record['error'] = 'disk_error'
                finally:
                    try:
                        record['_stream'].close()
                    except OSError:
                        pass
                    record.update(status='paused', _stream=None, _owner=None)
                    try:
                        self._metadata(record)
                    except OSError:
                        pass
                    self._report(record)

    def cancel(self, identity, token, owner):
        with self.lock:
            record, part = self._get(identity, token)
            if record['status'] == 'complete':
                raise TransferError('already_complete', '完整文件已保存，不会因取消而删除。')
            if record['status'] == 'cancelled':
                return self._status(record)
            if record.get('_owner') != owner:
                raise TransferError('lease_lost', '传输已由新的连接接管。')
            if record.get('_stream'):
                record['_stream'].close()
            part.unlink(missing_ok=True)
            record.update(status='cancelled', _stream=None, _owner=None)
            self._metadata(record)
            self._report(record)
            return self._status(record)

    def close(self):
        with self.lock:
            for record in self.records.values():
                if record.get('_stream'):
                    try:
                        self._checkpoint(record, force=True)
                    except OSError:
                        record['error'] = 'disk_error'
                    finally:
                        try:
                            record['_stream'].close()
                        except OSError:
                            pass
                        record.update(status='paused', _stream=None, _owner=None)
                        try:
                            self._metadata(record)
                        except OSError:
                            pass
