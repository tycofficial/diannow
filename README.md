# diannao · 远程电脑

直接打开网页即可连接，不需要登录、密钥或粘贴分享链接。

- ChatGPT Site：<https://desktop-live-tianw-1105.tyc130923.chatgpt.site/>
- GitHub Pages：仓库开启 Pages 后，`main` 分支的 `/docs` 是发布目录。

`node scripts/build.mjs` 同时生成 Site Worker 和 GitHub Pages 页面，两处使用同一份浏览器代码。

## 画面、声音和操作

默认保留电脑屏幕的原始分辨率。H.264 使用固定清晰度编码，网络受限时减少发送帧数，不反复缩放画面或重建编码器。实际分辨率、接收帧率、网络往返和视频缓冲显示在底栏。真实 4K、120 fps 需要源屏幕、显卡、接收设备和网络同时支持。

支持点击、单指滑动滚动、长按拖动、双指滚动、触控板滚动、右键、中文输入和快捷键。特殊按键使用物理扫描码，并保持足够的按下时间；失去连接时释放按键和鼠标。Esc 是否退出全屏由目标软件决定，更多按键还提供 F11。

点击“声音”播放电脑扬声器音频。浏览器通常要求这一次点击才能允许播放声音。

“麦克风”支持从手机或 iPad 回传声音。要让 Windows 软件将它识别为麦克风，电脑必须已有可用的虚拟音频输入设备，且目标软件使用该输入。当前实现支持将回传音频写入 `CABLE Input` 或 `VoiceMeeter Input`，由对应的虚拟麦克风端提供给软件。没有虚拟设备时页面会如实提示。

## 电脑端

Windows Python 3.12 环境安装 `requirements.txt`，运行：

```powershell
python scripts/rtc_agent.py
```

按提示在隐藏输入中提供连接配置 JSON，字段为 `url` 和 `agentKey`。电脑端凭据仅保存在进程内存，不放进网页、源代码或 Git。后端将其 SHA-256 设置为 `AGENT_KEY_SHA256`；允许的 GitHub Pages 来源配置为 `FRONTEND_ORIGINS`。这些配置不影响访客直接打开网页。

程序阻止重复启动。采集、编码、音频、输入和网络处理分开执行；没有连接时停止采集。当前会话最长运行八小时，电脑必须保持开机。

## GitHub Pages

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
python scripts/verify-rtc.py
```

输入验证不会向桌面注入按键；RTC 验证在本机建立真实音视频连接，检查原生分辨率、音频解码与按键确认。
