# Video2Text · 社媒视频一键转文案

输入社媒视频链接，自动下载音频 → Whisper ASR 转写 → 带时间戳的分段文案。

## 架构

```
┌── Mac mini (M2) ────────────────────┐     ┌── Dell 工作站 (RTX 3090) ──┐
│  Web 服务 :8137                      │     │  Whisper-ASR :7860         │
│  yt-dlp 下载                         │────→│  large-v3-turbo GPU 转写   │
│  ffprobe 读时长                      │     └────────────────────────────┘
│  三级分段算法                         │
│  前端界面                             │
└─────────────────────────────────────┘
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

- **在线模式**: 输入 YouTube/社媒链接，自动下载 + 转写
- **离线模式**: 勾选「📁 离线模式」，直接用已有文件测试（跳过下载）

### CLI
```bash
source .venv/bin/activate
python main.py "https://youtu.be/VIDEO_ID"
python main.py "URL" --json
python main.py "URL" --out result.txt
```

## 数据流

```
URL 输入
  ├─ YouTube + 有字幕 → [通道A] 字幕直取 (youtube-transcript-api, 零下载)
  ├─ YouTube + 无字幕 → [通道B] 下载音频 → Dell 3090 ASR 转写
  └─ 其他平台         → [通道C] 直接下载视频/音频

转写完成后自动保存到 transcripts/ 目录 (.txt + .md 双格式)
```

## 分段算法

转写结果按**三级切分**估算时间戳（不碰音频，纯文本算法）：

1. **强标点** `。！？.!?` — 自然语义断句
2. **弱标点** `，,、；;` — 次级停顿
3. **空格 + 动态字数上限** — 兜底防塌陷

目标每段 5-15 秒，不超过 20 秒。

## 项目结构

```
Video2Text/
├── app.py                # FastAPI Web 服务
├── main.py               # CLI 入口
├── run.sh                # 启动/停止脚本
├── ingestion/
│   ├── config.py         # 代理/路径配置
│   ├── ingest.py         # 三通道路由
│   ├── downloader.py     # yt-dlp 下载
│   ├── transcript.py     # YouTube 字幕直取
│   └── asr.py            # ASR 转写 (Dell 3090 + 三级分段)
└── templates/
    └── index.html        # 前端 (内联 CSS/JS)
```

## 环境要求

- Python 3.11+
- ffmpeg / ffprobe
- 局域网内 Dell 工作站 (192.168.121.99:7860)
- 可选: 旁路由代理 (iStoreOS) 用于翻墙

## License

MIT
