# diannao · 远程电脑

直接打开网页即可连接，不需要登录、密钥或粘贴分享链接。

- ChatGPT Site：<https://desktop-live-tianw-1105.tyc130923.chatgpt.site/>
- GitHub Pages：<https://tycofficial.github.io/diannow/>

`node scripts/build.mjs` 同时生成 Site Worker、仓库根目录和 `/docs` 的 GitHub Pages 页面，使用同一份浏览器代码。手机横屏时将工具栏移到左右两侧，桌面画面使用全部可用高度；竖屏保留上下工具栏。

## 画面、声音和操作

默认从 720P／60 fps 开始，自动模式在接收稳定后尝试更高分辨率，也可手动选择。H.264 使用固定清晰度编码；仅有持续拥塞证据时降低发送帧率，低带宽估计本身不会触发降帧。实际分辨率、发送和接收帧率、网络往返及视频抖动缓冲停留时间显示在底栏。码率显示的是当前视频流量，不是网络测速。真实 4K、120 fps 需要源屏幕、显卡、接收设备和网络同时支持。

支持点击、单指滑动滚动、长按拖动、双指滚动、触控板滚动、右键、中文输入和快捷键。特殊按键使用物理扫描码，并保持足够的按下时间；失去连接时释放按键和鼠标。Esc 是否退出全屏由目标软件决定，更多按键还提供 F11。

点击“开启声音”播放电脑扬声器音频，优先使用 Web Audio 的交互播放模式，不支持时使用兼容播放器。浏览器通常要求这一次点击才能允许播放声音。音视频使用独立流与同步标识，避免音频播放缓冲拖住桌面画面。

音频发送在调度停顿后丢弃过期采样；输入时间戳发生跳跃时重建 Opus 编码缓存，同时保留原有 RTP 时间基准，避免先发送旧时间戳再突然跳跃。接收设备仍会根据实际网络和播放状态调整音频缓冲。

键盘提供“实时发送”和“编辑后发送”。编辑后发送模式保留语音输入草稿、换行和修改，收到电脑的接收确认后才清空；失败或未确认会保留草稿。

“麦克风”支持从手机或 iPad 回传声音。要让 Windows 软件将它识别为麦克风，电脑必须已有可用的虚拟音频输入设备，且目标软件使用该输入。当前实现支持将回传音频写入 `CABLE Input` 或 `VoiceMeeter Input`，由对应的虚拟麦克风端提供给软件。没有虚拟设备时页面会如实提示。

## 电脑端

本机桌面上的 **远程桌面.exe** 可直接双击。窗口提供后台状态、启动、停止、重新连接和打开 GitHub 观看页面。关闭窗口后后台继续运行；登录、解锁和唤醒时会尝试启动，运行中的后台检测到睡眠唤醒后会重建采集。异常退出后会自动重试。关机及睡眠期间不能推流，重启后需要先登录 Windows。

`scripts/build-desktop.ps1` 将当前电脑端及其运行依赖打包为单文件 EXE；`scripts/install-desktop.ps1` 保留现有 DPAPI 配置，备份旧启动任务，将真实 EXE 复制到桌面并更新 `CodexDesktopViewer` 计划任务。EXE 不依赖本机 Python，连接配置仍保存在当前 Windows 用户的加密文件中，不包含在 EXE 或仓库里。

Windows Python 3.12 环境安装 `requirements.txt`，运行：

```powershell
python scripts/rtc_agent.py
```

按提示在隐藏输入中提供连接配置 JSON，字段为 `url` 和 `agentKey`。电脑端凭据仅保存在进程内存，不放进网页、源代码或 Git。后端将其 SHA-256 设置为 `AGENT_KEY_SHA256`；允许的 GitHub Pages 来源配置为 `FRONTEND_ORIGINS`。这些配置不影响访客直接打开网页。

程序阻止重复启动，最多支持四个独立网页连接；打开另一页面不会踢掉已有活跃连接。无心跳的旧连接会超时释放，同一请求重试不会重复占用会话。采集、编码、音频、输入和网络处理分开执行；没有连接时停止采集。运行期间请求 Windows 1 ms 计时精度，退出时释放。

多个观看页面共享桌面采集，并复用同一采集时刻、同一分辨率的像素转换结果；各连接保留独立的编码状态。音频设备的打开与关闭在工作线程执行，避免设备初始化暂停网络事件循环。实际帧率和延迟仍取决于电脑负载、网络及接收设备。

电脑端会实际探测 AMD AMF 编码器；可用时将原始 BGRA 帧直接送给显卡，原始分辨率下省去 CPU 色彩转换，并读取采集线程的最新帧，避免重复调用 DXGI 抓屏。显卡不可用时自动回退到软件 H.264。采集进程使用 AboveNormal 优先级，不使用 High 或 Realtime。

本机安装的 `CodexDesktopViewer` 计划任务在当前用户登录后后台启动，异常退出后自动重启，没有八小时运行限制。连接凭据通过 Windows DPAPI 加密保存在当前用户本地应用目录；电脑仍需保持开机并登录。

## GitHub Pages

仓库根目录与 `/docs` 都生成 `index.html` 和 `.nojekyll`，主地址直接显示远程桌面界面，避免将 README 渲染成首页。

在仓库 Settings → Pages 中选择 **Deploy from a branch**、**main**、**/docs** 并保存。此后同步 `docs` 的提交会自动更新页面。

GitHub Pages 托管浏览器页面，Site Worker 提供连接信令，Windows 程序采集桌面并执行输入。音视频通过 WebRTC 传输。受限网络可能需要配置 TURN 中继；普通浏览器和标准权限进程无法操作 Windows 安全桌面或部分管理员窗口。

## 验证

```powershell
node scripts/build.mjs
node scripts/verify-signaling.mjs
node scripts/verify-gestures.mjs
node scripts/validate-artifact.mjs
python scripts/verify-input.py
python scripts/verify-encoder.py
python scripts/verify-audio-clock.py
python scripts/verify-audio-timeline.py
python scripts/verify-shared-frame-source.py
python scripts/verify-media-device-lifecycle.py
python scripts/verify-rtp-scheduling.py
python scripts/verify-rtc.py
```

输入验证不会向桌面注入按键；RTC 验证在本机建立真实音视频连接，检查原生分辨率、音频解码与按键确认。
