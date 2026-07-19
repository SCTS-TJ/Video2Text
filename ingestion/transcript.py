"""YouTube 字幕直取层, 基于 youtube-transcript-api。

无需下载视频, 直接取官方/自动字幕 -> 拼成纯文本。
作为 ASR 的"捷径通道": 命中即最快最准、零推理成本。
"""
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, RequestBlocked

from .config import get_config


def _extract_video_id(url: str) -> "str | None":
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return None
    if parsed.hostname in ("youtu.be",):
        return parsed.path.lstrip("/")
    if parsed.hostname and "youtube" in parsed.hostname:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if parsed.path.startswith("/embed/") or parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
    return None


def fetch_youtube_transcript(url: str) -> dict:
    """取 YouTube 字幕, 优先手动字幕, 退而求自动字幕。

    返回:
        {
            "ok": bool,
            "text": str,            # 拼接后的纯文本
            "language": str,
            "source": str,          # "manual" | "auto"
            "error": str|None,
        }
    """
    vid = _extract_video_id(url)
    if not vid:
        return {"ok": False, "text": "", "language": "", "source": "", "error": "invalid youtube url"}

    cfg = get_config()
    proxies = {"http": cfg.http_proxy, "https": cfg.https_proxy} if cfg.http_proxy else None

    try:
        api = YouTubeTranscriptApi(proxies=proxies) if proxies else YouTubeTranscriptApi()

        # 新版 API: fetch() 不接受 preserve_generated 参数
        tr = api.fetch(video_id=vid)
        source = "manual" if not getattr(getattr(tr, "snippets", [None])[0] if tr else None, "is_generated", True) else "auto"

        # 判断来源: 检查第一个 snippet 是否自动生成
        text = " ".join([s.text for s in tr])
        lang = ""
        if tr:
            first = tr[0] if tr else None
            if first:
                lang = getattr(first, "language_code", "") or ""
                # 新版 API: 从 snippet 判断是否自动生成
                try:
                    from youtube_transcript_api.formatters import TextFormatter
                    is_gen = getattr(first, "is_generated", None)
                    if is_gen is not None:
                        source = "auto" if is_gen else "manual"
                except Exception:
                    pass

        return {"ok": True, "text": text, "language": lang, "source": source, "error": None}

    except (TranscriptsDisabled, NoTranscriptFound):
        return {"ok": False, "text": "", "language": "", "source": "", "error": "no transcript available"}
    except RequestBlocked as e:
        return {"ok": False, "text": "", "language": "", "source": "", "error": f"IP blocked by YouTube"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "language": "", "source": "", "error": f"{type(e).__name__}: {e}"}
