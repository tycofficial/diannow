# 文件上传验收 · 2026-11-14 · build 24 / protocol 1

本次功能从 `b4100172a03ab539b3a1b5f20264f4ee27f066cb` 的隔离工作树发布。原工作树中另外五批未同步信令提交完整保留，不包含在本次发布增量中。这里区分本机真实 HTTP/WebRTC/磁盘证据、测试 double 和尚未验证的生产行为。

## 行为与边界

- 网页“传文件”选择文件，无口令、密码或登录步骤。这是用户明确选择的公开上传行为；公开观看和已有键鼠方式保持原状。
- 文件字节走独立的 file-only PeerConnection / SCTP，沿用现有 ICE/STUN/TURN 配置，不经过 Worker/R2 存储，不创建媒体轨道或输入处理器。
- 每块最多 60 KiB；浏览器网络缓冲及待接收端确认字节各限制在 4 MiB 内；一个页面按文件顺序流水传输，电脑最多接收两个文件、四个文件连接；内存记录缓存限制 128 项。
- 单文件上限 128 GiB，并受 D 盘实际可用空间限制。大文件按 File.slice 读取、增量 SHA-256，不把整个文件读入内存。适用于任意后缀和无后缀；不自动执行、打开、解压接收内容。
- 目录固定为 `D:\DIANNOW\Received`。文件名禁止路径分隔、盘符、ADS、Windows 保留名、非法字符和重解析路径；同名通过 ` (n)` 后缀另存，绝不覆盖已有文件。
- `.incoming` 临时文件单独保存；ACK 是 fsync 后的接收字节。完成要求全部接收、fsync、整文件读回 SHA-256 与网页源文件相符，然后原子发布文件名。页面和电脑端提示显示确切保存路径。
- 自动重连最多三次；继续使用接收端已落盘偏移与前缀 SHA-256。刷新后重新选择同一名称、大小和修改时间的原文件，可使用当前浏览器会话保存的内部续传凭证继续。该凭证由软件自动管理，不是用户口令。POST 不在同一次协商中重放，GET 允许在截止时间内恢复。
- 传输中可取消；全部数据收完后的校验/提交阶段禁用取消并明确显示状态。断网时取消未确认会显示未确认，不冒充删除成功。丢失续传信息时可重新上传，旧临时文件不会冒充完整文件。
- 新网页要求电脑心跳声明 `fileProtocol: 1` 后才发起文件协商，旧电脑端会显示升级提示，不向旧媒体处理器发送文件 offer。旧网页仍可使用新版电脑端。文件流与桌面会话独立，但页面首次确认文件协议目前依赖正常桌面心跳。

## 实测环境

Windows 11；Python 3.12.14 / aiohttp 3.14.5 / aiortc 1.15.0 / av 17.1.0；Node 24.17.0；已安装 Chromium，无界面运行且不采集媒体。测试合成文件、浏览器 profile、收件目录均位于 D 盘自建临时目录。服务端只绑定 `127.0.0.1` 随机端口，测试端不配置 STUN/TURN，不连接生产、代理或桌面服务。

## 通过

| 验收 | 实际证据 |
| --- | --- |
| 小文件、0 字节、无后缀、二进制、任意后缀、Unicode 文件名 | 真实 D 盘 FileReceiver 字节比较、完成 SHA-256；`.exe`、`.html` 仅写入，未执行 |
| 同名不覆盖 | 两次同名上传，内容分别保留，完成重试幂等 |
| 路径与续传授权 | 非法路径、ADS、保留名、错误内部凭证、过期连接 lease 均被拒绝；匿名新建上传明确允许 |
| 断线/重启续传 | 真实 8 MiB ICE/DTLS/SCTP 断链后新 peer 续传；持久化 manifest 恢复并检查前缀；校验中中断状态恢复到暂停 |
| 取消/校验失败 | 未生成完整文件，临时部分按状态处理；取消后的下一文件不被旧窗口阻塞 |
| 磁盘不足/故障 | 空间不足拒绝新建；注入写入 ENOSPC、校验 fsync EIO、暂停 fsync EIO，验证不发布、句柄/lease 释放及可恢复状态；故障为确定性注入，不是填满 D 盘 |
| 2,147,549,185 字节存储 | 真实写入、读回 SHA-256、大小一致；49.84 秒，tracemalloc 跟踪堆峰值 2,648,341 字节；该数值不是整个进程内存 |
| Chromium 8,388,611 字节 | 真实 File.slice→生产 JS→真实 SCTP→生产 Python→磁盘；主动断链一次并续传；16.398 秒；独立 Node 读盘 SHA-256 一致 |
| Chromium 67,108,867 字节 | 同一真实链路及一次断链续传；44.242 秒；浏览器峰值 bufferedAmount 4,024,928 字节、未确认 4,177,920 字节；两端和独立 Node SHA-256 一致 |
| 进度 IPC | 真实状态 JSON 写读，完成字节/确切路径/速度归零/不含秘密；窗口启动动作使用 double，未显示生产窗口 |
| 协议失败路径 | 非对象 JSON、错误凭证、路径、校验失败的真实 SCTP 回归；接收槽回收；客户端取消窗口、终态失败新建、长前缀保活、旧后端兼容使用确定性 double |
| 原有功能 | 隔离树 build、ESM 产物校验、viewer startup、signaling、gestures、session state、trackpad、motion queue、session lifecycle、heartbeat size、ICE configuration、TURN route 通过；心跳 526 字节 |

最终补记：Chromium **2,147,549,185 字节**真实全链路通过，一次真实 peer 关闭后自动新协商/续传，耗时 **1,363.768 秒**（约 1.50 MiB/s，本机测试数值，不是跨网速度承诺）。浏览器最大 bufferedAmount **4,193,892 字节**，最大未确认 **4,177,920 字节**；接收端读盘 SHA-256、客户端源文件 SHA-256 和独立 Node 读盘 SHA-256 一致。完整数据实际写入 D 盘后再校验；原始合成文件、收件文件、profile 和 fixture 均已清理。最终资源清理/恢复改动在隔离源码上另行通过关联小文件与故障回归，未重复这次耗时 2 GiB 测试。

最终隔离树的真实生产面板还通过 0 字节（1.966 秒）和 65,537 字节（2.293 秒）上传；后一轮实际断言面板可见、完成进度、SHA-256 状态和确切保存路径显示。Chrome 版本为 **157.0.8103.52**。没有显示电脑端生产 Tk 窗口或采集桌面。

隔离树 PyInstaller **6.16.0** HostOnly 构建通过，产物 `RemoteDesktop.exe` 6,916,550 字节，SHA-256 `79a7bcadc4590521df51f1b3ab0e1d79de2da2e97a9ba233962c8c091ecb41c9`；完整 runtime 1,488 个文件、266,519,510 字节。实际读取 EXE 的 PYZ，确认包含三个文件传输模块，不含旧五批中的 `signaling_transport` / `ice_config_refresh` 模块，产物中无 DPAPI 凭证文件。因管理员令牌不足，未执行该带管理员 manifest 的 EXE，也未安装。

## 已有失败与未执行

`verify-media-isolation.py` 在旧帧率断言 `recent_fps(track, 5) == 60` 失败：该测试仅提供 `track.sent`，但 main 既有生产代码已改为读取 `track.frame_samples`，因此返回 0。`b410` 已同时包含这段旧测试和新实现；本次未改帧率方法。该测试在此前身份隔离断言通过后才失败；本次未以修改视频行为掩盖此既有失败。

尚未验证：国内双端、跨运营商/NAT/CGNAT、真实 TURN 文件路径、HTTPS/CORS 实际生产上传、手机 Safari、多页面同时大文件、128 GiB 边界、断电持久性、生产桌面/音频/GPU/输入、稳定 60fps。不能把 loopback、合成故障或 tracemalloc 数值当成这些验收通过，也不承诺任意网络直连或最快速度。

## 重现与部署

运行现有 Python：`scripts/verify-file-transfer.py`（加 `--large-bytes 2147549185` 做 D 盘大文件存储）、`scripts/verify-file-transfer-rtc.py`、`scripts/verify-file-agent-isolation.py`。Node：`verify-file-client.mjs`、`verify-file-client-failures.mjs`、`verify-file-browser.mjs --bytes=2147549185`；浏览器测试通过 `DIANNOW_TEST_PYTHON` 指定已安装解释器，`DIANNOW_TEST_BROWSER` 可指定已有 Chrome。浏览器测试最多约 30 分钟，非零退出表示失败，清理仅限本次自建目录。

电脑端从同一隔离源码用 `build-desktop.ps1 -HostOnly -PythonPath <已安装Python> -BuildRoot D:\DIANNOW\Builds\file-transfer-release-20261114` 构建。`update-desktop-runtime.ps1 -SourceHost <输出host目录>` 要求管理员终端，先备份原 runtime，再平滑停止、复制、逐文件校验和无设备 frozen 自检，然后启动原计划任务。它不更改任务定义、DPAPI 配置、驱动、代理或防火墙；非管理员在任何写入前退出，不自动弹 UAC。RuntimeOnly 安装入口改为此路径。

当前工具管理员令牌为 False，因此尚未安装/重启生产电脑端。既有 Site 元数据可读，但源码写入凭证接口对相同 project_id 返回 `NOT_FOUND: Sites project not found`；未创建替代 Site、改变访问权限或部署未推送源码。生产版本状态须据实际发布结果另行核实。

## 发布前更新脚本修复 · 同一 PR #2

上一版更新脚本递归统一安装目录 ACL，且复制/自检失败仅报告备份位置；“保留原权限”的表述不成立。本批删除这一做法，未在生产执行过该旧脚本。

- 更新只对已存在的运行时文件使用 `FileMode.Open` 写入原文件的数据流，不创建、替换、删除安装目录中的文件对象，不调用任何 ACL setter。逐路径记录并核验原有 Owner、Group、DACL 的 SDDL，包含未参与更新的目录和用户文件。没有读取/声称验证 SACL；同一文件对象的数据写入不主动改动其安全描述符。[FileMode.Open 文档](https://learn.microsoft.com/en-us/dotnet/api/system.io.filemode?view=netframework-4.8.1)
- 新文件、目录、只读属性改动在停止后台前列为 `permission_review_required`，不自动分配权限或放宽权限。重解析点、硬链接、源/目标/备份/状态目录重叠、混入连接凭证的来源均拒绝。
- 仅备份待改运行时文件，逐项读回 SHA-256，保存每项原权限及不可覆盖的事务 manifest 后才申请停止。配置目录、DPAPI 内容和未列入运行时来源的用户文件不复制或覆盖。
- 部分复制、内容校验、自检、启动检查失败会先确保新后台停止，再按已验证备份恢复所有实际触及的原字节；无需重写的锁文件直接核验原内容。随后复核原字节及逐项权限，清除本次停止标记，再启动并检查旧后台。原先停止的后台保持停止。
- 生产启动检查要求新的、未过期的 host-status 心跳，host/engine 两个 PID 均实际存在且来自原安装 EXE，原计划任务处于 Running；不会把 `Start-ScheduledTask` 调用成功或缓存状态当成恢复成功。冻结自检等待最多 30 秒，仅超时的自有无设备自检进程可终止；生产后台不强制杀进程。
- `stop.request` 写入本次随机所有权标识；本次停止产生的空 `engine-stop.request` 在重启前一并清理。已有或并发的用户 stop/restart 请求保留，不擅自清除或覆盖。恢复失败、备份损坏、权限漂移会明确报告，保留备份与 manifest，不谎报恢复成功。
- 强杀更新器或断电不在本批自动恢复保证内。未有已验证提交/恢复结果的旧 manifest 会阻止下一轮自动更新，等待人工核实，避免把不确定的半更新状态当作新基线。

本批实测：PowerShell 7.6.9 下 `verify-runtime-update.ps1` **27 个场景通过**，使用真实临时文件写入、SHA-256 和前后实际 SDDL 对比；部分写入、真实共享锁、第二个文件失败、自检异常、新后台启动失败、停止超时/异常、备份损坏、回滚 IO/停止失败、旧后台健康失败、逐路径权限差异/漂移、外部/已有停止请求、新权限待审、来源变动、未结束事务及原先停止状态均有覆盖。权限差异/漂移使用只读返回值 double，未修改测试目录或生产 ACL。

`verify-runtime-health.ps1` 直接提取并复用三个实际生产函数，真实临时状态 JSON 配合进程/任务/时钟 double，验证新鲜心跳、PID/路径/任务校验、等待旧任务退出、启动截止时间、协议不兼容、自检失败/超时和自有进程清理。没有调用生产任务或启动任何真实 EXE。

Windows PowerShell **5.1.26100.9444** 的四个脚本只读 AST 解析通过；其功能测试命令 `powershell.exe -NoProfile -NonInteractive -File scripts/verify-runtime-update.ps1` 被本机执行策略以 `PSSecurityException / UnauthorizedAccess` 拒绝，脚本未执行。没有使用 ExecutionPolicy Bypass、修改策略或换方式执行被拒测试。实际生产安装、真实任务恢复、真实 frozen 自检仍未运行；既有 2 GiB 文件传输与 EXE 构建无需因安装脚本改动重复执行，原结果保留。Site 的 NOT_FOUND 发布阻塞保持原状。
