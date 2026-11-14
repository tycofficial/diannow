"""Small per-user status IPC and a separate Tk window; never logs file names."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

STATE = Path(os.environ.get('LOCALAPPDATA', '.')) / 'CodexDesktopViewer' / 'desktop-service' / 'file-transfer-status.json'


def runtime_self_test(destination):
    """Frozen dependency/store smoke test without devices, credentials or GUI."""
    import hashlib
    import tempfile
    import _tkinter
    from file_transfer import FileReceiver
    from file_transfer_rtc import LABEL
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {'ok': False, 'fileProtocol': 1, 'build': 24, 'frozen': bool(getattr(sys, 'frozen', False)), 'media': False, 'input': False}
    try:
        with tempfile.TemporaryDirectory(prefix='file-runtime-smoke-', dir=destination.parent) as directory:
            store = FileReceiver(directory)
            try:
                value = store.begin('synthetic_no_extension', 3, 'self-test')
                store.write(value['id'], value['token'], 'self-test', 0, b'abc')
                done = store.finish(value['id'], value['token'], 'self-test', hashlib.sha256(b'abc').hexdigest())
                assert Path(done['path']).read_bytes() == b'abc' and LABEL == 'diannow-files-v1'
                result['ok'] = True
            finally:
                store.close()
    except Exception as error:
        result['error'] = type(error).__name__
    destination.write_text(json.dumps(result), encoding='utf-8')
    return 0 if result['ok'] else 1


class ProgressReporter:
    def __init__(self, path=STATE, launch=None):
        self.path, self.launch = Path(path), launch or self.open_window
        self.lock = threading.Lock()
        self.items, self.samples = {}, {}
        self.last_write, self.last_launch = 0, 0

    @staticmethod
    def open_window():
        if not getattr(sys, 'frozen', False):
            return  # Source/test runs never launch GUI implicitly.
        subprocess.Popen([sys.executable, '--transfer-window'], creationflags=0x08000000,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def __call__(self, value):
        with self.lock:
            now = time.monotonic()
            identity, received = value['id'], value['committed']
            previous = self.samples.get(identity, (now, received, 0))
            speed = previous[2]
            if now - previous[0] >= .25:
                speed = max(0, (received - previous[1]) / (now - previous[0]))
                self.samples[identity] = (now, received, speed)
            elif identity not in self.samples:
                self.samples[identity] = previous
            fresh = identity not in self.items
            self.items[identity] = {**value, 'speed': speed, 'updated': time.time()}
            if len(self.items) > 20:
                old = min(self.items, key=lambda key: self.items[key]['updated'])
                self.items.pop(old, None)
                self.samples.pop(old, None)
            terminal = value['status'] in ('complete', 'failed', 'cancelled', 'paused')
            if terminal:
                self.items[identity]['speed'] = 0
            if not fresh and not terminal and now - self.last_write < .2:
                return
            self.last_write = now
            temporary = self.path.with_name(self.path.name + '.' + str(os.getpid()) + '.tmp')
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps({'updated': time.time(), 'items': list(self.items.values())}, ensure_ascii=False), encoding='utf-8')
                os.replace(temporary, self.path)
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if fresh and now - self.last_launch > 2:
                self.last_launch = now
                try:
                    self.launch()
                except OSError:
                    pass


def show_transfer_window(path=STATE):
    import ctypes
    import tkinter as tk
    from tkinter import ttk
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.CreateMutexW(None, False, 'Local\\DiannowFileTransferWindow')
    if not handle or ctypes.get_last_error() == 183:
        if handle:
            api.CloseHandle(handle)
        return
    root = tk.Tk()
    root.title('DIANNOW 文件接收')
    root.geometry('660x430')
    root.minsize(480, 280)
    ttk.Label(root, text='接收文件 · D:\\DIANNOW\\Received', font=('Microsoft YaHei UI', 13)).pack(anchor='w', padx=16, pady=12)
    text = tk.Text(root, wrap='word', font=('Microsoft YaHei UI', 10), padx=14, pady=8)
    text.pack(fill='both', expand=True)
    ttk.Label(root, text='进度以接收端已落盘字节为准。关闭窗口不影响传输；接收的文件不会自动打开。').pack(padx=12, pady=10)
    labels = {'receiving': '正在接收', 'recovering': '正在核对续传内容', 'verifying': '正在读盘校验', 'publishing': '正在保存', 'paused': '连接中断，等待续传', 'complete': '完整落盘，SHA-256 校验通过', 'failed': '传输失败', 'cancelled': '已取消'}
    def refresh():
        try:
            state = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        lines = []
        for item in reversed(state.get('items', [])):
            size, done = item['size'], item['committed']
            percent = 100 if not size else done * 100 / size
            status = labels.get(item['status'], item['status'])
            if item['status'] in ('receiving', 'recovering', 'verifying') and time.time() - item.get('updated', 0) > 15:
                status += '（暂时没有新进度，等待恢复）'
            lines.append(f"{item['name']}\n{status}  {percent:.1f}%  {done:,} / {size:,} 字节  {item.get('speed', 0)/1024**2:.2f} MiB/s")
            if item['status'] == 'verifying':
                lines.append(f"读盘校验：{item.get('verifiedBytes', 0):,} / {size:,} 字节")
            if item.get('path'):
                lines.append('保存路径：' + item['path'])
            if item.get('error'):
                lines.append('原因：' + item['error'])
            lines.append('')
        text.configure(state='normal')
        text.delete('1.0', 'end')
        text.insert('1.0', '\n'.join(lines) or '等待文件传输…')
        text.configure(state='disabled')
        root.after(300, refresh)
    try:
        refresh()
        root.mainloop()
    finally:
        api.CloseHandle(handle)
