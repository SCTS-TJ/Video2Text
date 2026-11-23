# Video2Text · 社媒视频一键转文案

输入社媒视频链接，自动下载音频 → Whisper ASR 转写 → 带 word-level 时间戳的分段文案。

**v1.7.3** — 批量删除**性能优化 ~54×**（30 对文件 129s → 2.4s）（历史 v1.7.2: 多选下拉框 + 批量删除）

## 架构

```
┌── Dell 工作站 (192.168.121.30) = 逻辑控制 + 采集 + ASR ───────┐
│  video2text.service         Web 界面 + 采集 API      :8137      │
│  whisper-asr-dell.service   Whisper ASR (medium)     :7860      │
│  yt-dlp / bilibili-api 下载 · ffmpeg/ffprobe 提音频             │
│  结构化日志 → Agent_Workspace/Video2Text/logs/                  │
└─────────────────────────────────────────────────────────────────┘
        ▲ 浏览器访问                          ▲ 翻墙
┌── Mac mini (本机) ──────────────┐   ┌── iStoreOS 旁路由 ────────┐
│  http://192.168.121.30:8137      │   │  192.168.121.44:7890      │
└──────────────────────────────────┘   └───────────────────────────┘

> x79 (192.168.121.99:7860) 另有一份 Whisper ASR 备用实例; 当前服务用本机 30 的实例。
```

## 快速开始

```bash
# 安装
git clone <repo>
cd Video2Text
uv pip install -r requirements.txt --python .venv/bin/python

# 启动 Web 服务
bash run.sh start

# 访问
open http://localhost:8137
```

## 使用方式

### Web 界面
打开 `http://localhost:8137`，粘贴视频链接，点击「提取文案」。

- **在线模式**: 输入社媒链接，自动下载 + 转写
- **离线模式**: 勾选「📁 离线模式」，直接用已有文件测试（跳过下载）

### 支持的平台

| 平台 | 方式 | 说明 |
|------|------|------|
| **YouTube** + 有字幕 | 通道A — 字幕直取 | youtube-transcript-api, 零下载 |
| **YouTube** + 无字幕 | 通道B — 下载+ASR | yt-dlp + Dell ASR 转写 |
| **Bilibili** | 通道D — API 直连 | 绕过 WAF 412, Python requests 直接调 B站 API |
| **微信视频号** | 通道F — wx-downloader | 经腾讯元宝登录态解析 + 内置解密(需一次性 `wx-downloader login` 扫码) |
| 其他平台 | 通道C — 通用下载 | yt-dlp + 旁路由代理 |

### CLI
```bash
source .venv/bin/activate
python main.py "https://youtu.be/VIDEO_ID"
python main.py "URL" --json
python main.py "URL" --out result.txt
```

## 日志系统

日志统一输出到 `/Volumes/Studio_IT_Dev/Video2Text/`：

```
video2text.log    # 业务日志 (INFO+) — 排错主入口
error.log         # 错误日志 (WARNING+) — 只看异常
uvicorn.log       # HTTP 访问日志
```

查看实时日志：
```bash
tail -f /Volumes/Studio_IT_Dev/Video2Text/video2text.log
```

## 数据流

```
URL 输入
  ├─ YouTube + 有字幕 → [通道A] 字幕直取 (零下载)
  ├─ YouTube + 无字幕 → [通道B] 下载 → ASR
  ├─ Bilibili         → [通道D] Bilibili API 直连 → 下载 → ASR
  ├─ 微信视频号        → [通道F] wx-downloader (元宝解析+内置解密, 需先 login)
  └─ 其他平台         → [通道C] yt-dlp 下载 → ASR

转写完成后自动保存到 transcripts/ 目录 (.txt + .md 双格式)
```

## 项目结构

```
Video2Text/
├── app.py                # FastAPI Web 服务
├── main.py               # CLI 入口
├── run.sh                # 启动/停止脚本
├── ingestion/
│   ├── config.py         # 代理/路径配置
│   ├── ingest.py         # 五通道采集路由 (A/B/C/D/F)
│   ├── downloader.py     # yt-dlp 下载 (含国内站点代理跳过) + extract_audio
│   ├── bilibili_dl.py    # Bilibili API 直连下载器 (绕 WAF)
│   ├── weixin_channels.py# 微信视频号识别 + 下载(通道F, 集成 wx-downloader)
│   ├── transcript.py     # YouTube 字幕直取
│   ├── asr.py            # ASR 转写 (GPU + 三级分段)
│   ├── index.py          # 转写索引管理
│   └── logger.py         # 结构化日志配置
└── templates/
    └── index.html        # 前端 (内联 CSS/JS, 字幕同步高亮)
```

## v1.7.3 更新内容 (批量删除性能优化)

- ⚡ **批量删除提速 ~54 倍**（30 对文件：**129s → 2.4s**，平均 80ms/文件）
- **根因（实测）**：`/api/delete-bulk` 最初逐文件复用单删逻辑，导致
  - `transcripts/index.json`（13.5MB / 55 万行）**每个文件都解析一次（1.8s）并写回一次（0.6s）**
  - `_isfile_retry()` 对**缺失的配对文件**白等 5 次重试（2.0s/个）
  - ⇒ 单文件合计 ~4.3s，全选 57 个 ≈ 4 分钟
- **修复**：
  - `downloads` **只 `listdir` 一次**（0.01s）做存在性判断，不再逐文件 stat+重试
  - `index.json` **只读一次、只写一次**（抽出内存操作 `_strip_keys()`）
  - 单文件失败仍不影响其它
- ✅ 实测：30 对（60 个实体）总用时 **2.39s**，0 失败，无残留

## v1.7.2 更新内容 (文件多选下拉框 + 批量删除)

- 🗂 **文件下拉框改造为「多选」**：离线模式的文件选择框 → 自定义多选下拉
  - 每项带勾选框 · 顶部 **全选** · 实时 **「已选 N 个」** · 底部 **「🗑️ 删除选中」**
  - 单选时按原逻辑作为「离线转写」的当前文件（标签直接显示文件名）
- 🗑 **批量删除**：新增 `POST /api/delete-bulk`
  - 逐个执行单文件删除逻辑（**含配对 mp3/mp4 与 index.json 记录**），单个失败不影响其它
  - 删除前有**确认弹窗**，列出待删文件与数量
- 🔧 后端重构：单删/批量删共用 `_delete_one()` + `_remove_from_index()`（行为与原先一致）
- ✅ 验证：后端实测（3 个删成功含配对文件，1 个不存在正确报错）；真机 Chromium UI 全流程 PASS

## v1.7.1 更新内容 (网页内置扫码授权登录)

- 🔑 **页头新增「🔑 授权登录」**：点开弹窗 → 选平台 → **页面直接显示二维码给你扫**
  - **服务器端**无头 Chromium 打开登录页 → 抓取二维码回传前端 → 轮询登录态 → 自动保存 cookie
  - 登录态判据用**探针页**（访问须登录页面是否仍 302 到 `/login`）—— 因为小红书对**访客**也下发 `web_session`，不能按它判断
  - 保存的 cookie 会**合并进 `cookies.txt`**（仅替换该站旧条目，不动 YouTube/Google）
- 🚨 **未授权报错自动引导**：下载失败若属未授权类错误，**自动弹出对应平台的扫码登录**
  - `No video formats found` / 小红书 → 小红书扫码
  - `元宝登录凭据` / `wx-downloader login` → 预留（视频号）
- 📄 另附 CLI 备用：`xhs_login.py`（本机有桌面时可跑）
- 接口：`GET /api/auth/providers`、`POST /api/auth/start`、`GET /api/auth/status/{sid}`

## v1.7.0 更新内容 (YouTube 修复 ✅)

### 问题
本网公网出口是**数据中心 IP**（Oracle 韩国机房 `152.67.214.202`），YouTube 对 yt-dlp 一律判 bot（`Sign in to confirm you're not a bot`）。
已逐项排除（均实测）：11 种 `player_client`、自造并注入 PO Token（player+gvs）、TLS 指纹伪装（`curl_cffi --impersonate`）、cookies（有效且未过期）、yt-dlp 升级到最新。

### 解法：走澳洲住宅出口，用 SSH 跳板链解决跨境限速
```
Dell ──SSH(中国→韩国, 快)──▶ 韩国VPS 152.67.214.202 ──SSH──▶ hdd1t(澳洲住宅) ──▶ YouTube
                                                        出口 = 114.76.214.20 (悉尼 / Telstra)
```
- 直连澳洲仅 **~4KB/s**（跨境限速）；经韩国中转后 **~600KB/s**
- **只用 22 端口**，不占用任何云端口（两台 VPS 的云防火墙只放行 80/443）

### 落地清单
| 组件 | 位置 / 值 |
|---|---|
| `v2t-yt-chain.service`（Dell systemd） | `ssh -N -D 127.0.0.1:11080 -J opc@152.67.214.202 root@114.76.214.20` |
| `V2T_YT_PROXY`（video2text drop-in） | `socks5h://127.0.0.1:11080` |
| `ASR_FALLBACK_URL`（video2text drop-in） | `http://192.168.121.99:7860/transcribe` |

### 配套修复：ASR 自动回退
本机 GPU 与 ComfyUI 共享。ComfyUI 占满显存时 whisper 会 `CUDA OOM` → 现在**自动回退到 x79 的 whisper**，任务不再整体失败。

### 健康灯
页头新增 **`YT链`**（optional）：探测本地中转链是否就绪（链断会变红提示）。

## v1.6.3 更新内容 (YouTube 专用出口开关)

- 🔀 **新增环境变量 `V2T_YT_PROXY`**：给 YouTube 单独指定出口代理（**留空 = 沿用默认旁路由代理**）
  - 将来换到住宅/干净出口时，**只需填一个环境变量、无需改代码**：
    ```bash
    sudo systemctl edit video2text        # 或改 /etc/systemd/system/video2text.service
    #   [Service]
    #   Environment=V2T_YT_PROXY=http://<host>:<port>
    sudo systemctl restart video2text
    ```
- 📋 **旁路由实测核实（2026-11-16）**：PassWall 的 3 个落地**全部是 Oracle 云机房**
  （韩春川 `152.67.214.202` / 美圣何塞 `155.248.214.183` / hdd1t 链）；实测切到美国节点后**出口仍是韩国那个 IP**
  ⇒ **现有基础设施里没有"住宅/干净出口"可用**
- 🧹 **已卸载**排障中安装的 bgutil PO Token provider（Docker 容器 + 镜像 + pip 插件）—— 实测对本问题无效

## v1.6.2 更新内容 (YouTube 风控诊断)

- 🩺 **`Sign in to confirm you're not a bot` 现在附带可操作诊断**（不再只回显 yt-dlp 原文）
- **根因（2026-11-16 实测）**：**本网出口 IP 是数据中心 IP** —— `152.67.214.202` = **Oracle Cloud 韩国机房**（整条宽带的公网出口），YouTube 对机房 IP 做针对性风控
- **已逐项排除**（都不是原因）：

  | 检查项 | 结论 |
  |---|---|
  | cookies | ✅ **有效且未过期**（带其访问订阅页返回 200；SID/SAPISID 等 2027-08 到期） |
  | JS 运行时 | ✅ node 正常求解 n-challenge |
  | player_client | ❌ 试了 11 种，全部 `LOGIN_REQUIRED` |
  | PO Token provider | ❌ 已装 bgutil 2.0.2（能出 token），但流程走不到该步；官方亦说明 PO Token **无法**绕过基于 IP 的登录限制 |
  | 直连 | ❌ 被墙，必须走代理（出口正是该机房 IP） |
  | 影响面 | 多数视频失败（Gangnam/Despacito…），少数仍可（dQw4w9WgXcQ） |

- **真正的解法**：把 YouTube 的出口换成**住宅/干净 IP**（换代理线路或加分流规则）—— 非代码可解

## v1.6.1 更新内容 (复制按钮改造)

- 📋 **「导出 TXT」→「复制(带时间戳)」**：不再下载 `.txt` 文件，改为**直接复制到剪贴板**
  - 内容不变（有分段则每行 `[mm:ss] 文本`），只是交付方式从「下载文件」改为「复制」
- 🐛 **修复 http 下剪贴板不可用（重要）**：
  - `navigator.clipboard` 仅在**安全上下文**（HTTPS / localhost）可用；经 `http://192.168.121.30:8137` 局域网访问时它是 `undefined` ⇒ **原「复制」按钮一直是静默失败**（无任何提示）
  - 新增 `copyText()`：优先 Clipboard API，不可用时回退 `document.execCommand('copy')` + 临时 textarea
- ✅ 验证：真实 Chrome headless 经该 http 地址（实测 `isSecureContext=false`）点击两按钮，复制成功且内容逐字校验通过

## v1.6.0 更新内容 (微信视频号端到端打通 ✅)

- 🎉 **视频号真正可用（端到端实测通过）**：
  `https://weixin.qq.com/sph/...` → wx-downloader 元宝解析 → 下载 → **Isaac64 解密** → ffmpeg 抽音频 → ASR 转写
  - 实测产物经 `ffprobe` 验证为合法 **H.264 + AAC MP4**（39.75s / 1.8MB），转写文本正常
- 🔐 **登录流程落地**：元宝凭据在 **Mac mini(192.168.121.31)** 用 Chrome 扫码生成，再拷到 Dell（见下节）
- 🐛 **登录态信号修正**：旧失败记录**仅当晚于 `auth.json` 写入时间(mtime)** 才判为已过期
  - 此前刚重新扫码登录后，健康灯会误报"登录可能已过期"（还留着登录前的失败记录）
- 🚦 健康灯：`视频号 ● 已登录`

## v1.5.9 更新内容 (修复健康灯点击)

- 🐛 **修复: 点击健康灯没反应(详情气泡打不开)**
  - 根因: 点击热区判定用 `#svcTip`, 而健康灯在**兄弟节点** `#svcLights` 里 → 点灯落入 `else` 分支**直接关闭**; 此前唯一可点的 `⚠` 图标又因 optional 不计入 bad 而隐藏 → **完全打不开**
  - 修复: 热区改为整个 `#svcStatus`(点任意灯/标签都能展开), 并加 `▾` 视觉提示
  - 验证: headless Chromium 实测 — 点 `[视频号]` 灯展开(显示 `视频号 ○ 需扫码登录`)、再点收起、点其它灯亦可展开

## v1.5.8 更新内容 (视频号健康灯)

- 🚦 **页头新增「视频号」健康灯**: `/api/health` 返回 `wechat` 服务(标 `optional`)
  - 已登录 → 绿; 需扫码登录 / 未安装 → 红, 但**不计入 overall**, 不会把整体判红、也不弹错误横幅
- 🔎 **"登录可能已过期"运行期信号(关键)**: 元宝凭据是会话 cookie(`expires=-1`), 本地**无法**判断服务端会话是否失效
  - 故额外记录**最近一次通道F 下载结果**: "本地凭据在 + 最近一次因登录失败" → 灯变红并提示 **登录可能已过期, 请重扫 wx-downloader login**
  - 边界: 从未登录 → 显示"需扫码登录"(不误报过期); 非登录类失败(网络等) → 不误报
- 🧪 7 个分支已单测验证(未安装/未登录/已登录/过期/未登录+失败/非登录失败/成功)

## v1.5.7 更新内容 (微信视频号打通 · 通道F)

- 🎉 **视频号可自动下载**: 集成开源 `wx-downloader`(PyPI, 纯 Python)
  - 原理: 经**腾讯元宝登录态**解析分享链接 → 下载 → **内置解密**(Isaac64 密钥流 XOR) → 输出可播放 MP4
  - **无需** Go / 微信客户端 / 代理抓包 / 根证书 (远优于 wx_channels_download、wx_channel 等 MITM 方案)
- 🔌 **通道F 集成** (`ingestion/weixin_channels.py`):
  - `wx-downloader download <url> --no-login --json -o <临时目录>`, 用独立目录收集 `*.mp4`(不依赖其 JSON 输出 schema)
  - 再经 `ffmpeg` 抽 `mp3` 供 ASR, 复用现有下载→转写管线
- 🔐 **一次性前置**: 需先 `wx-downloader login` 扫码登录元宝(凭据 `~/.config/wx-channels/auth.json`, 过期需重登)
- 🧰 `downloader.extract_audio()` 提为可复用工具函数

> ⚠️ 依赖许可: `wx-downloader` 为 **MIT + Commons Clause**(禁止售卖该软件本身); 内部自用无碍。
> ⚠️ 元宝/视频号接口非官方稳定 API, 上游变更时可能需升级 `wx-downloader`。

## v1.5.6 更新内容 (索引完整性修复)

- 🐛 **通道A 索引污染修复**: `_index_entry` 此前只校验 `text`, 未校验文件名
  - 通道A(YouTube 字幕直取)结果 `path=""` → 每次都会写入一条 **key 为 `""`** 的垃圾索引
    (`title=untitled`, `segments=[]`, `duration=0`), 且互相覆盖
  - 现无本地媒体文件时直接跳过索引(文本仍由 `_save_transcript` 落盘)
- 🧹 清理历史遗留的空 key 索引条目

## v1.5.5 更新内容 (平台支持修复)

- ✅ **YouTube 下载 403 修复**: yt-dlp 升级 `2026.07.04 → 2026.08.19`
  - 旧版对 YouTube 新兴 SABR/PO-token 流处理不周, 实测下载报 `HTTP Error 403: Forbidden`; 升级后实测下载 243MB 4K 视频成功
  - cookies 无需更换(实测原 cookies 可用)
- 🧭 **微信视频号专用引导 (通道E)**:
  - 视频号视频为 TEA 加密流 + 无公开 API, 服务端无法自动下载
  - 新增 `ingestion/weixin_channels.py`: 精确识别视频号链接, 不再浪费一次必然失败的 yt-dlp 调用
  - 返回可操作提示: 用抓包/解析工具保存文件 → 本页勾选「📁 离线模式」转写

## v1.5.4 更新内容 (系统体检修复)

- 🩺 **下载失败可诊断**: `_run_ytdlp` 保留 stderr, 新增 `_extract_error()` 优先提取 `ERROR:` 行
  - 之前任何下载失败都只报 `unknown error`, 现在返回真实原因(如 `ERROR: Unsupported URL: ...`)
- ⚡ **真实下载进度**: 打通 `progress_cb`, 用 yt-dlp 的 percent/speed 回调
  - 取代旧「文件大小 / 50MB」粗略估算; 前端可显示真实 MB/s
- 🎯 **通道A(YouTube 字幕直取)恢复**:
  - 适配 `youtube-transcript-api` 1.2.x (构造参数 `proxy_config`, 旧 `proxies=` 会 TypeError)
  - `fetch(languages=["zh-Hans","zh-CN","zh-Hant","zh-TW","zh","en"])`, 中文视频不再漏字幕
- 🔧 **force_download 打通**: `req.force_download` 全链路透传(此前 API/CLI 参数被忽略)
- 🖥 **JS 运行时按平台探测**: `ytdlp_js_runtime` 不再硬编码 Mac 路径(Linux 命中 `/usr/bin/node`)
- 🐛 **失败进度修正**: 下载失败的任务不再错误停在 95%

## v1.4.0 更新内容

- 🖱 **Logo 点击回主页**: 点左上角「V2」刷新回到首页
- 🗑 **移除无用按钮**: 去掉「一键润色」「金句提炼」及对齐偏移控制条
- 🔗 **源链接显示**: 预览 pill 显示完整视频源 URL, 可点击跳转
- 📁 **离线模式优化**:
  - 文件列表只显示 `.mp4/.mkv/.webm` 视频文件, 避免误选 `.mp3`
  - 长文件名 `…` 截断, 不撑破布局
- 🎞 **黑屏修复**:
  - `_build_payload` 的 ffprobe 探测结果同步回 `result`, 确保存入 index
  - `api_check_existing` 放宽探测条件, 修正历史条目的错误 `media_type`
  - 前端 skip 模式用扩展名判断而非 entry.media_type
  - 修复 `import subprocess` 缺失导致探测静默失败
- 🔄 **字幕高亮修复**:
  - 换文件后重置 `_syncRafId`, 确保新播放器绑定 timeupdate 监听
  - 离线 skip 分支也重置同步状态 (之前遗漏提前 return)
- 🧹 **杂项**:
  - `api/files` 只返回视频文件
  - `<video>` 加 `playsinline` 属性
  - 移除「同步/手动」切换按钮 (功能不稳定)
- 🛠 **代码健壮性**: 所有模块接入统一日志, 下载/ASR/异常全链路可追踪

## 环境要求

- Python 3.11+
- ffmpeg / ffprobe
- 局域网内 Dell 工作站 (192.168.121.30, SSH 用户: boothgianni): Web :8137 + Whisper ASR :7860
- 可选: 旁路由代理 (iStoreOS 192.168.121.44:7890) 用于翻墙
- 微信视频号(通道F): `pip install wx-downloader`(需 Python 3.10+)

## 微信视频号启用步骤（已落地）

**架构**：登录在有桌面的 **Mac mini(192.168.121.31)** 完成；下载/解密/转写在 **Dell(192.168.121.30)**。

```bash
# ── 一次性: 扫码登录（在 Mac mini 上, 需桌面 + Chrome）──
export PATH="$HOME/.local/bin:$PATH"          # 未装 uv 时先: curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install wx-downloader
wx-downloader login                           # 弹 Chrome → 手机微信扫码登录腾讯元宝
wx-downloader status                          # 期望 "已保存（服务端有效性将在下载时检查）"

# ── 把凭据拷到 Dell（Mac mini → Dell 免密已通）──
scp ~/.config/wx-channels/auth.json boothgianni@192.168.121.30:/home/boothgianni/.config/wx-channels/

# ── 使用：页面直接粘贴 https://weixin.qq.com/sph/... ──
```

> ⚠️ Dell 上需 `pip install wx-downloader`（通道F 调用它，已装）。
> ℹ️ 元宝凭据是**会话**（非永久）；失效后页面健康灯会变红提示"登录可能已过期"，重跑 `wx-downloader login` 即可。

## License

MIT
