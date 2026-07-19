"""Video2Text ingestion layer.

统一入口:
  - 有字幕的 YouTube 视频 -> 直取字幕(零下载)
  - 无字幕 YouTube 视频   -> 下载视频+提取音频 -> ASR 转写
  - 其他平台(B站/腾讯等)  -> 直接下载视频
"""

from .config import get_config
from .downloader import download_media, download_with_audio
from .transcript import fetch_youtube_transcript
from .asr import transcribe
from .ingest import ingest

__all__ = [
    "get_config",
    "download_media",
    "download_with_audio",
    "fetch_youtube_transcript",
    "transcribe",
    "ingest",
]
