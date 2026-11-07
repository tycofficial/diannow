# diannao · 远程电脑

打开原网址即可连接，无需登录或填写连接配置：

- https://tycofficial.github.io/diannow/
- https://desktop-live-tianw-1105.tyc130923.chatgpt.site/

网页每分钟检查发布版本，空闲且无未发送草稿时自动载入新版本；不会连续重载同一个版本。两个平台使用相同的网页代码，GitHub Pages 通过 Site Worker 交换连接信令。网页自动取得 ICE 配置，优先直连，失败后自动重试 TURN 中继。电脑端使用已运行的本机 Clash 代理访问信令站点，并让 TURN/TCP 媒体连接通过同一代理，并从站点取得 TURN TCP 配置。电脑端保留 STUN 公网 UDP 映射探测，允许不同网络直接协商；断线重试优先采用首条已配置的 TCP 中继线路。中继线路的速度取决于网络和提供商。

## 画面与操作

提供 480P、720P、1080P、4K、自动清晰度和帧率选择；默认选择流畅480P。显示真实接收帧率、输入往返、音视频缓冲；未知统计不显示为零。使用显卡 H.264 按线路带宽调整码率编码，共享最新采集帧，避免历史画面排队。120Hz 虚拟屏幕已经支持，但实际传输帧率受显卡、网络、接收设备影响，不能承诺 4K/120fps。

手机横屏时工具栏排列于左右，桌面使用全部可用高度；提供隐藏工具栏的专注模式。单指原生触控直接响应滑动和拖动；静止点按也先发送更新帧再抬起，触控 API 失败时自动回退为鼠标点击、单指滚动和窗口标题栏拖动，双指点击在两指中点执行右键，支持双指滚动。也可切换触控板、拖动锁定和速度。iPhone Safari 未提供网页震动接口时使用视觉反馈。

最多四个设备同时连接，各设备独立持有按键、鼠标和触控状态。支持外接实体键盘直接输入、中文输入、系统软键盘，以及包含字母、数字、标点、功能键和数字区的网页键盘。“窗口换屏”将当前窗口移动到另一显示器。

## 音频

已安装虚拟声卡时，独立桌面音频不随本机物理扬声器音量和静音改变。WASAPI 采集回调直接驱动音频发送，采用 5ms Opus 包和有界缓冲。浏览器播放通常需要点击“声音”；网络和设备播放缓冲仍然存在。

开启网页“麦克风”后，手机音频写入 VB-CABLE，并将 Windows 默认输入切换到虚拟麦克风。关闭麦克风或设备断开后恢复电脑原来的输入；多个设备开启时，最后一个关闭才恢复。只观看桌面不会改变电脑默认麦克风。目标软件应使用 Windows 默认输入或 CABLE Output。

## 独立电脑后台

桌面“远程桌面.exe”可手动启动、停止和重连；关闭其窗口或最小化 ChatGPT 不会停止后台。真实后端安装于受保护的 Program Files 目录，通过最高可用权限计划任务在登录、解锁、唤醒后启动，后台 EXE 本身声明 requireAdministrator，点击后显式激活目标窗口，可向同一交互桌面的同权限管理员窗口发送输入。Windows UAC 安全桌面仍需本机确认。

视频暂时停止解码时请求新的关键帧并重试现有播放器，操作通道仍存活时保留连接。初始连接超时不会误伤已连接的会话，真正网络断开后自动尝试中继。电脑端的 ICE 保活响应等待窗口为 2 秒，以容纳跨地区 TURN 往返；仍在最后一次有效保活回复超过 30 秒后关闭失联连接。TURN/TCP 视频按 8KB 应用排队上限和 256KB 系统发送缓冲施加反压，队列拥塞时暂停视频发包，把发送机会留给操作、声音和保活，并结合接收端带宽反馈和实际往返延迟为视频发包定速，避免将静态桌面的低流量误当成线路上限。AMD 编码器明确启用强制 IDR，实际恢复帧始终附带缓存的 SPS/PPS，避免断流后重复请求无效的 I 帧。AMF 重配置至少保持30秒并确认持续趋势，普通全画面刷新周期延长至30秒，解码恢复请求仍立即处理；编码排队限制为一帧，超过50毫秒的过时预取帧会被重新采样。即使只有接收端经过 TURN，也控制发送节奏；只有 RTT 相对基线增长或接收缓冲证实排队时才跟随低带宽估计，避免静态画面的低流量形成永久限速。关闭网页声音时停止发送桌面音频，避免持续占用流量；再次开启声音会恢复发送。网页明确区分画面连接与操作通道就绪，小指针根据画面缩放，宽度不超过 10 CSS 像素。新版使用 balanced 协商，将操作数据与音视频拆为独立 ICE/DTLS 传输，避免视频 TCP 排队堵住按键和触控；手指移动每帧只发送最新位置，可靠通道保留下压和抬起，过期移动不会重新按下。触控板使用累计位移，即使丢失中间包，下一次位置或点击也能补齐位移。

媒体进程使用 High 优先级、关闭其节能节流，不使用 Realtime。内核事件传递后台心跳，状态文件读取锁不会使进程崩溃。异常退出自动重试，旧会话清理不能无限期阻塞新连接。关机和睡眠期间不能传输，重启后需先登录 Windows。

`scripts/build-desktop.ps1 -HostOnly` 构建独立后端；不加参数同时构建桌面单文件启动器。`scripts/install-desktop.ps1 -RuntimeOnly` 更新后台而不重复安装驱动。首次安装需要官方虚拟声卡和虚拟显示器。防火墙仅放行受保护后端的 WebRTC UDP，不关闭整个防火墙。

连接配置由 Windows DPAPI 加密保管。Site 运行时设置 AGENT_KEY_SHA256、FRONTEND_ORIGINS 和 TURN_ICE_SERVERS_JSON；网页不要求用户手动配置。

## 构建与验证

`node scripts/build.mjs` 同步生成仓库根目录、docs 和 Site Worker。GitHub Pages 使用 main 分支的 docs 目录。部署包仅含 Worker 和 hosting 清单。

```powershell
node scripts/build.mjs
node scripts/validate-artifact.mjs
node scripts/verify-signaling.mjs
node scripts/verify-viewer-startup.mjs
node scripts/verify-gestures.mjs
node scripts/verify-trackpad.mjs
node scripts/verify-motion-queue.mjs
python scripts/verify-input-motion.py
python scripts/verify-control-transport.py
python scripts/verify-ice-configuration.py
python scripts/verify-h264-recovery.py
python scripts/verify-touch-fallback.py
python scripts/verify-native-touch.py
python scripts/verify-microphone-routing.py
python scripts/verify-media-device-lifecycle.py
python scripts/verify-session-lifecycle.py
python scripts/verify-status-lock.py
```

输入与手势验证使用模拟接口，不向桌面注入按键。音视频和线上连接验证需要明确区分模拟统计与实际接收结果。

## 480P 低延迟模式

网页默认从 480P 起步，保持源画面比例，60fps 的目标预算为 1.5Mbps。不同分辨率使用独立转换锁和线程本地转换器并行处理；相同分辨率的观看者共享当前不可变画面，过时预取帧会被丢弃。自动模式连续观察到缓冲增长或冻结后降到 480P，至少保持两分钟；持续健康后才逐级恢复清晰度。手动选择的清晰度保持不变。

python scripts/verify-stream-profile.py 验证 480P 尺寸、码率预算、并行转换和同分辨率共享。发布前需要先更新 Windows 后台，再同步 Site 和 GitHub Pages；旧后台不会识别 480P 设置。
