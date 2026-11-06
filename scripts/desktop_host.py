"""Standalone per-user desktop host; reuse the existing DPAPI configuration."""
import ctypes
import getpass
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

APP_DIR=Path(os.environ['LOCALAPPDATA'])/'CodexDesktopViewer'
STATE_DIR=APP_DIR/'desktop-service'
CONFIG_PATH=STATE_DIR/'connection.dpapi'
PROTECTED_DIR=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'DiannaoRemoteDesktop'
PROTECTED_CONFIG=Path(os.environ.get('PROGRAMDATA','C:/ProgramData'))/'DiannaoRemoteDesktop'/getpass.getuser()/'connection.dpapi'
MUTEX_NAME='Local\\CodexDesktopSupervisor_tianw_1105'
CREATE_NO_WINDOW=0x08000000

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def load_configuration():
    installed=Path(sys.executable).parent==PROTECTED_DIR
    path=PROTECTED_CONFIG if installed or PROTECTED_CONFIG.exists() else CONFIG_PATH
    data=path.read_bytes();buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt32=ctypes.WinDLL('crypt32',use_last_error=True)
    if not crypt32.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:config=json.loads(ctypes.string_at(target.data,target.size))
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)
    if not config.get('url','').startswith('https://') or not config.get('agentKey'):
        raise ValueError('Invalid desktop connection configuration')
    return config

def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(path)

def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def log(event,**values):
    STATE_DIR.mkdir(parents=True,exist_ok=True)
    with (STATE_DIR/'host.log').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'time':time.strftime('%Y-%m-%d %H:%M:%S'),'event':event,**values},ensure_ascii=False)+'\n')

def kernel():
    result=ctypes.WinDLL('kernel32',use_last_error=True)
    result.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];result.CreateMutexW.restype=wintypes.HANDLE
    result.CloseHandle.argtypes=[wintypes.HANDLE]
    return result

def claim_mutex():
    api=kernel();ctypes.set_last_error(0);handle=api.CreateMutexW(None,False,MUTEX_NAME)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error()==183:api.CloseHandle(handle);return None
    return handle

class JobLimits(ctypes.Structure):
    _fields_=[('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),('flags',wintypes.DWORD),('minSet',ctypes.c_size_t),('maxSet',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
class JobInfo(ctypes.Structure):
    _fields_=[('limits',JobLimits),('io',ctypes.c_ulonglong*6),('processMemory',ctypes.c_size_t),('jobMemory',ctypes.c_size_t),('peakProcessMemory',ctypes.c_size_t),('peakJobMemory',ctypes.c_size_t)]

def make_job():
    api=kernel();api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];api.CreateJobObjectW.restype=wintypes.HANDLE
    api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    api.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    job=api.CreateJobObjectW(None,None);info=JobInfo();info.limits.flags=0x2000
    if not job or not api.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)):raise ctypes.WinError(ctypes.get_last_error())
    return api,job

def own_command(*arguments):
    if getattr(sys,'frozen',False):return [sys.executable,*arguments]
    return [sys.executable,str(Path(__file__).resolve()),*arguments]

def start_background():
    if (PROTECTED_DIR/'RemoteDesktop.exe').exists():
        subprocess.run(['schtasks.exe','/Run','/TN','CodexDesktopViewer'],creationflags=CREATE_NO_WINDOW,check=False)
        return
    installed=APP_DIR/'RemoteDesktop.exe'
    command=[str(installed),'--background'] if installed.exists() else own_command('--background')
    # A standalone one-file child must retain its own extracted runtime after the GUI closes.
    environment={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'}
    subprocess.Popen(command,cwd=APP_DIR if APP_DIR.exists() else Path.cwd(),creationflags=CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS,close_fds=True,env=environment)

def resumed(last_tick,current_tick):return current_tick-last_tick>15

def supervise():
    mutex=claim_mutex()
    if mutex is None:return
    api,job=make_job();process=None
    try:
        load_configuration()  # Verify before starting any capture process.
        for name in ['stop.request','restart.request']:(STATE_DIR/name).unlink(missing_ok=True)
        api.SetThreadExecutionState(0x80000001)
        log('host-started',pid=os.getpid(),frozen=bool(getattr(sys,'frozen',False)))
        last_tick=time.monotonic();restart_at=0
        while True:
            now=time.monotonic()
            if (STATE_DIR/'stop.request').exists():break
            heartbeat=read_json(STATE_DIR/'engine-status.json')
            stalled=bool(process and heartbeat.get('pid')==process.pid and time.time()-heartbeat.get('updated',time.time())>55)
            requested=(STATE_DIR/'restart.request').exists() or stalled
            if resumed(last_tick,now) or requested:
                (STATE_DIR/'restart.request').unlink(missing_ok=True)
                if process and process.poll() is None:
                    api.TerminateJobObject(job,0);process.wait(timeout=15)
                process=None;restart_at=now
                log('resume-or-restart',stalled=stalled)
            last_tick=now
            if process and process.poll() is not None:
                log('engine-exited',code=process.returncode);process=None;restart_at=now+3
            if process is None and now>=restart_at:
                process=subprocess.Popen(own_command('--agent'),cwd=APP_DIR,creationflags=CREATE_NO_WINDOW,close_fds=True)
                if not api.AssignProcessToJobObject(job,int(process._handle)):
                    process.kill();raise ctypes.WinError(ctypes.get_last_error())
                log('engine-started',pid=process.pid)
            atomic_json(STATE_DIR/'host-status.json',{'hostPid':os.getpid(),'enginePid':process.pid if process else None,'running':bool(process and process.poll() is None),'updated':time.time(),'executable':sys.executable})
            time.sleep(1)
    finally:
        if process and process.poll() is None:
            api.TerminateJobObject(job,0);process.wait(timeout=15)
        atomic_json(STATE_DIR/'host-status.json',{'running':False,'updated':time.time()})
        api.SetThreadExecutionState(0x80000000);api.CloseHandle(job);api.CloseHandle(mutex)
        log('host-stopped')

class AgentOutput:
    encoding='utf-8'
    def __init__(self):
        STATE_DIR.mkdir(parents=True,exist_ok=True);path=STATE_DIR/'exe-agent.log'
        if path.exists() and path.stat().st_size>5_000_000:path.replace(path.with_suffix('.previous.log'))
        self.stream=path.open('a',encoding='utf-8',buffering=1);self.pending='';self.lock=threading.RLock()
    def write(self,value):
        with self.lock:
            self.stream.write(value);self.pending+=value
            while '\n' in self.pending:
                line,self.pending=self.pending.split('\n',1)
                try:message=json.loads(line)
                except ValueError:continue
                if message.get('serviceAuthenticated') or message.get('engineHeartbeat'):
                    state=read_json(STATE_DIR/'engine-status.json')
                    if message.get('serviceAuthenticated'):state['authenticated']=True
                    state.update(pid=os.getpid(),updated=time.time())
                    atomic_json(STATE_DIR/'engine-status.json',state)
        return len(value)
    def flush(self):self.stream.flush()
    def fileno(self):return self.stream.fileno()
    def isatty(self):return False

def run_engine():
    output=AgentOutput();sys.stdout=sys.stderr=output
    # High is bounded below Realtime; only the media worker is promoted.
    api=kernel();api.GetCurrentProcess.restype=wintypes.HANDLE
    api.SetPriorityClass.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    api.SetPriorityClass(api.GetCurrentProcess(),0x80)
    class Throttling(ctypes.Structure):_fields_=[('Version',wintypes.DWORD),('ControlMask',wintypes.DWORD),('StateMask',wintypes.DWORD)]
    policy=Throttling(1,1,0)
    api.SetProcessInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    api.SetProcessInformation(api.GetCurrentProcess(),4,ctypes.byref(policy),ctypes.sizeof(policy))
    atomic_json(STATE_DIR/'engine-status.json',{'authenticated':False,'pid':os.getpid(),'updated':time.time()})
    config=load_configuration();sys.stdin=io.StringIO(json.dumps(config)+'\n')
    import asyncio
    import rtc_agent
    asyncio.run(rtc_agent.main())

def self_test(destination):
    result={'frozen':bool(getattr(sys,'frozen',False)),'configurationReadable':False}
    try:
        config=load_configuration();result['configurationReadable']=True
        import rtc_agent, av, pyaudiowpatch
        av.Codec('libx264','w');result['mediaDependenciesReady']=True
        import asyncio,aiohttp
        async def check():
            headers={'X-Agent-Key':config['agentKey']}
            if config.get('token'):headers['OAI-Sites-Authorization']='Bearer '+config['token']
            async with aiohttp.ClientSession(headers=headers,cookie_jar=aiohttp.DummyCookieJar(),trust_env=True,timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(config['url'].rstrip('/')+'/api/agent/poll') as response:return response.status
        result['siteStatus']=asyncio.run(check());result['ok']=result['siteStatus']==200
    except Exception as error:
        import traceback
        result.update(ok=False,error=str(error),traceback=traceback.format_exc())
    atomic_json(Path(destination),result)
    return 0 if result['ok'] else 1

def show_window():
    import tkinter as tk
    from tkinter import messagebox
    try:load_configuration()
    except Exception as error:messagebox.showerror('远程桌面','无法读取本机连接配置：'+str(error));return
    start_background()
    root=tk.Tk();root.title('远程桌面');root.geometry('480x290');root.resizable(False,False)
    root.configure(bg='#f3f3f3')
    tk.Label(root,text='远程桌面',font=('Microsoft YaHei UI',18),bg='#f3f3f3').pack(anchor='w',padx=22,pady=(18,4))
    status=tk.StringVar(value='正在启动后台…');detail=tk.StringVar(value='')
    tk.Label(root,textvariable=status,font=('Microsoft YaHei UI',12),bg='#f3f3f3').pack(anchor='w',padx=22,pady=4)
    tk.Label(root,textvariable=detail,font=('Microsoft YaHei UI',9),bg='#f3f3f3',fg='#555').pack(anchor='w',padx=22)
    def open_viewer():
        import webbrowser
        webbrowser.open('https://tycofficial.github.io/diannow/')
    tk.Button(root,text='打开远程桌面网页',command=open_viewer,font=('Microsoft YaHei UI',10),relief='solid',bd=1,padx=12,pady=4).pack(anchor='w',padx=22,pady=(8,0))
    row=tk.Frame(root,bg='#f3f3f3');row.pack(fill='x',padx=22,pady=15)
    def request(name):STATE_DIR.mkdir(parents=True,exist_ok=True);(STATE_DIR/name).touch()
    for text,command in [('启动后台',start_background),('重新连接',lambda:request('restart.request')),('停止后台',lambda:request('stop.request'))]:
        tk.Button(row,text=text,command=command,font=('Microsoft YaHei UI',10),relief='solid',bd=1,padx=12,pady=6).pack(side='left',padx=(0,8))
    tk.Label(root,text='关闭此窗口后，后台仍会运行。\n开机登录后自动启动；睡眠唤醒后自动恢复。',justify='left',font=('Microsoft YaHei UI',9),fg='#555',bg='#f3f3f3').pack(anchor='w',padx=22)
    def update():
        state=read_json(STATE_DIR/'host-status.json');alive=state.get('running') and time.time()-state.get('updated',0)<8
        status.set('后台正在运行' if alive else '后台未运行')
        detail.set('进程 '+str(state.get('enginePid',''))+' · 当前用户登录后自动启动' if alive else '可点击“启动后台”恢复')
        root.after(1000,update)
    update();root.mainloop()

def main():
    STATE_DIR.mkdir(parents=True,exist_ok=True)
    if '--self-test' in sys.argv:return self_test(sys.argv[sys.argv.index('--self-test')+1])
    if '--agent' in sys.argv:run_engine()
    elif '--background' in sys.argv:supervise()
    elif '--restart' in sys.argv:(STATE_DIR/'restart.request').touch()
    elif '--stop' in sys.argv:(STATE_DIR/'stop.request').touch()
    else:show_window()
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        log('fatal',type=type(error).__name__)
        raise
