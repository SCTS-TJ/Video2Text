# Video2Text · 社媒视频一键转文案

输入社媒视频链接，自动下载音频 → Whisper ASR 转写 → 带 word-level 时间戳的分段文案。

**v1.4.0** — UI 优化: 源链接显示 + 离线文件过滤 + 黑屏修复 + 交互改进

## 架构

```
┌── Mac mini (M2) ────────────────────────┐     ┌── Dell 工作站 (RTX 3090) ──┐
│  Web 服务 :8137                          │     │  Whisper-ASR :7860         │
│  yt-dlp / bilibili-api 下载              │────→│  large-v3-turbo GPU 转写   │
│  ffprobe 读时长                          │     │  (加 LD_LIBRARY_PATH 修复) │
│  结构化日志 → /Volumes/Studio_IT_Dev/    │     └────────────────────────────┘
│  前端界面 (字幕同步高亮)                  │
└─────────────────────────────────────────┘
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
| **YouTube** + 无字幕 | 通道B — 下载+ASR | yt-dlp + Dell 3090 转写 |
| **Bilibili** | 通道D — API 直连 | 绕过 WAF 412, Python requests 直接调 B站 API |
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
  ├─ YouTube + 无字幕 → [通道B] 下载 → Dell 3090 ASR
  ├─ Bilibili         → [通道D] Bilibili API 直连 → 下载 → ASR
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
│   ├── ingest.py         # 四通道采集路由
│   ├── downloader.py     # yt-dlp 下载 (含国内站点代理跳过)
│   ├── bilibili_dl.py    # Bilibili API 直连下载器 (绕 WAF)
│   ├── transcript.py     # YouTube 字幕直取
│   ├── asr.py            # ASR 转写 (Dell 3090 + 三级分段)
│   ├── index.py          # 转写索引管理
│   └── logger.py         # 结构化日志配置
└── templates/
    └── index.html        # 前端 (内联 CSS/JS, 字幕同步高亮)
```

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
- 局域网内 Dell 工作站 (192.168.121.99:7860, SSH 用户: boothgianni)
- 可选: 旁路由代理 (iStoreOS 192.168.121.44:7890) 用于翻墙

## License

MIT
