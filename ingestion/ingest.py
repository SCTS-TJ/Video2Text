"""统一采集入口。

策略:
  1. YouTube 且有字幕 -> 直取字幕 (transcript, 零下载)
  2. YouTube 无字幕   -> 下视频 + 提音频 -> ASR 转写
  3. 其他平台          -> 直接下载视频/音频

对外只暴露 ingest(url), 内部决定走哪条通道。
"""
from urllib.parse import urlparse

from . import downloader, transcript


def _is_youtube(url: str) -> bool:
    try:
        h = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    return "youtube" in h or "youtu.be" in h


def ingest(url: str, force_download: bool = False, transcribe: bool = True) -> dict:
    """采集一个社媒链接, 返回文本或下载文件信息。

    transcribe: 无字幕时是否自动下载并走 ASR (默认开)

    返回字段:
      channel: "transcript" | "asr" | "download_video"
      ok: bool
      text: str
      path: str            # 音频路径 (给 ASR)
      video_path: str      # 视频路径 (给前端预览)
      title: str
      ext: str
      mode: str
      ...
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
                "video_path": "",
                "title": "",
                "ext": "",
                "mode": "text",
                "source": res["source"],
                "language": res["language"],
                "error": None,
            }
        # 通道 B: 无字幕 -> 下视频 -> 提音频 -> ASR
        dl = downloader.download_with_audio(url)
        if not dl["ok"]:
            return {"channel": "download_video", **dl}
        if not transcribe:
            return _as_download_result(dl)
        from .asr import transcribe as asr_transcribe
        audio_path = dl.get("path", dl.get("video_path", ""))
        asr_res = asr_transcribe(audio_path, language="zh")
        if asr_res["ok"]:
            return {
                "channel": "asr",
                "ok": True,
                "text": asr_res["text"],
                "path": audio_path,
                "video_path": dl.get("video_path", ""),
                "title": dl["title"],
                "ext": "mp4",
                "mode": "video",
                "source": "whisper",
                "language": "zh",
                "error": None,
                "duration": asr_res.get("duration", 0),
                "segments": asr_res.get("segments", []),
            }
        # ASR 失败但视频已下, 一并返回
        return {**_as_download_result(dl), "error": asr_res["error"]}

    if _is_youtube(url):
        # 强制下载: 下载视频
        return _as_download_result(downloader.download_with_audio(url))

    # 其他平台: 直接下载
    return _as_download_result(downloader.download_with_audio(url))


def _as_download_result(dl: dict) -> dict:
    return {
        "channel": "download_video",
        "ok": True,
        "text": "",
        "path": dl.get("path", ""),
        "video_path": dl.get("video_path", ""),
        "title": dl.get("title", ""),
        "ext": dl.get("ext", ""),
        "mode": dl.get("mode", ""),
        "source": "",
        "language": "",
        "error": dl.get("error"),
    }
