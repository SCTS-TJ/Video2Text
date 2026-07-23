"""统一采集入口。

策略:
  1. YouTube 且有字幕 -> 直取字幕 (transcript, 零下载)
  2. YouTube 无字幕   -> 下视频 + 提音频 -> ASR 转写
  3. 其他平台          -> 直接下载视频/音频

对外只暴露 ingest(url), 内部决定走哪条通道。
"""
from urllib.parse import urlparse

from . import downloader, transcript
from .bilibili_dl import download_bilibili
from .logger import get_logger

logger = get_logger(__name__)


def _is_youtube(url: str) -> bool:
    try:
        h = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    return "youtube" in h or "youtu.be" in h


def _is_bilibili(url: str) -> bool:
    """判断 URL 是否为 Bilibili 视频。"""
    try:
        h = urlparse(url).hostname or ""
    except Exception:
        return False
    return "bilibili.com" in h or "b23.tv" in h


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
        logger.info("通道A: YouTube字幕直取 url=%s", url)
        res = transcript.fetch_youtube_transcript(url)
        if res["ok"]:
            logger.info("通道A成功 text_len=%d lang=%s", len(res.get("text", "")), res.get("language"))
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
        logger.info("通道A失败, 降级到通道B: 下载+ASR url=%s", url)
        dl = downloader.download_with_audio(url)
        if not dl["ok"]:
            logger.warning("通道B下载失败 error=%s", dl.get("error"))
            return {"channel": "download_video", **dl}
        if not transcribe:
            logger.info("通道B下载完成(不转写) path=%s", dl.get("path"))
            return _as_download_result(dl)
        from .asr import transcribe as asr_transcribe
        audio_path = dl.get("path", dl.get("video_path", ""))
        logger.info("通道B开始ASR audio=%s", audio_path)
        asr_res = asr_transcribe(audio_path, language="zh")
        if asr_res["ok"]:
            logger.info("通道B完成 text_len=%d", len(asr_res.get("text", "")))
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
        logger.warning("通道BASR失败 error=%s", asr_res["error"])
        return {**_as_download_result(dl), "error": asr_res["error"]}

    if _is_youtube(url):
        # 强制下载: 下载视频
        logger.info("通道B(强制): YouTube强制下载 url=%s", url)
        return _as_download_result(downloader.download_with_audio(url))

    if _is_bilibili(url):
        # 通道 D: Bilibili 专用下载 (绕过 yt-dlp WAF)
        logger.info("通道D: Bilibili专用下载 url=%s", url)
        dl = download_bilibili(url)
        if dl["ok"]:
            if not transcribe:
                logger.info("通道D下载完成(不转写) path=%s", dl.get("path"))
                return _as_download_result(dl)
            from .asr import transcribe as asr_transcribe
            audio_path = dl.get("path", dl.get("video_path", ""))
            logger.info("通道D开始ASR audio=%s", audio_path)
            asr_res = asr_transcribe(audio_path, language="zh")
            if asr_res["ok"]:
                logger.info("通道D完成 text_len=%d", len(asr_res.get("text", "")))
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
            logger.warning("通道DASR失败 error=%s", asr_res["error"])
            return {**_as_download_result(dl), "error": asr_res["error"]}
        # Bilibili 下载失败, 回退到通用下载
        logger.warning("通道D下载失败, 回退到通用通道 error=%s", dl.get("error"))

    # 其他平台: 直接下载
    logger.info("通道C: 其他平台下载 url=%s", url)
    return _as_download_result(downloader.download_with_audio(url))


def _as_download_result(dl: dict) -> dict:
    return {
        "channel": "download_video",
        "ok": dl.get("ok", False),  # 继承真实的下载结果
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
