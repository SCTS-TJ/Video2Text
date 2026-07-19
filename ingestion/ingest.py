"""统一采集入口。

策略:
  1. YouTube 且有字幕 -> 直取字幕 (transcript, 零下载)
  2. YouTube 无字幕   -> 下音频 -> ASR 转写 (whisper-webui)
  3. 其他平台          -> 直接下载视频

对外只暴露 ingest(url), 内部决定走哪条通道。
"""
from urllib.parse import urlparse

from . import downloader, transcript, asr


def _is_youtube(url: str) -> bool:
    try:
        h = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    return "youtube" in h or "youtu.be" in h


def ingest(url: str, force_download: bool = False, transcribe: bool = True) -> dict:
    """采集一个社媒链接, 返回文本或下载文件信息。

    transcribe: 无字幕时是否自动下载音频并走 ASR (默认开)
    """
    if _is_youtube(url) and not force_download:
        # 通道 A: 字幕直取 (免下载)
        res = transcript.fetch_youtube_transcript(url)
        if res["ok"]:
            return {
                "channel": "transcript",
                "ok": True,
                "text": res["text"],
                "path": "",
                "title": "",
                "ext": "",
                "mode": "text",
                "source": res["source"],
                "language": res["language"],
                "error": None,
            }
        # 通道 B: 无字幕 -> 下音频 -> ASR
        dl = downloader.download_media(url, audio_only=True)
        if not dl["ok"]:
            return {"channel": "download_audio", **dl}
        if not transcribe:
            return _as_download_result(dl)
        asr_res = asr.transcribe(dl["path"], language="zh")
        if asr_res["ok"]:
            return {
                "channel": "asr",
                "ok": True,
                "text": asr_res["text"],
                "path": dl["path"],
                "title": dl["title"],
                "ext": dl["ext"],
                "mode": "audio",
                "source": "whisper",
                "language": "zh",
                "error": None,
            }
        # ASR 失败但音频已下, 一并返回路径
        return {**_as_download_result(dl), "error": asr_res["error"]}

    if _is_youtube(url):
        return _as_download_result(downloader.download_media(url, audio_only=False))

    # 其他平台: 直接下载
    return _as_download_result(downloader.download_media(url, audio_only=False))


def _as_download_result(dl: dict) -> dict:
    return {
        "channel": "download_audio" if dl.get("mode") == "audio" else "download_video",
        "ok": True,
        "text": "",
        "path": dl.get("path", ""),
        "title": dl.get("title", ""),
        "ext": dl.get("ext", ""),
        "mode": dl.get("mode", ""),
        "source": "",
        "language": "",
        "error": dl.get("error"),
    }
