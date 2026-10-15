"""YouTube 字幕直取层, 基于 youtube-transcript-api。

无需下载视频, 直接取官方/自动字幕 -> 拼成纯文本。
作为 ASR 的"捷径通道": 命中即最快最准、零推理成本。
"""
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, RequestBlocked

try:  # v1.1+ 用构造参数 proxy_config 注入代理 (旧代码的 proxies= 会 TypeError)
    from youtube_transcript_api.proxies import GenericProxyConfig
except Exception:  # noqa: BLE001
    GenericProxyConfig = None

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

    try:
        # v1.2.x: 代理走构造参数 proxy_config (旧代码传 proxies= 在已装版本上直接 TypeError,
        # 使通道A(YouTube字幕直取)静默失效, 每次都退回"下载+ASR")。
        proxy_config = None
        if cfg.http_proxy and GenericProxyConfig is not None:
            proxy_config = GenericProxyConfig(http_url=cfg.http_proxy, https_url=cfg.https_proxy)
        api = YouTubeTranscriptApi(proxy_config=proxy_config) if proxy_config else YouTubeTranscriptApi()

        # 语言候选: 不能只默认 ('en',), 否则纯中文视频永远匹配不到字幕。
        tr = api.fetch(
            video_id=vid,
            languages=("zh-Hans", "zh-CN", "zh-Hant", "zh-TW", "zh", "en"),
        )
        snippets = list(tr)

        text = " ".join(s.text for s in snippets)
        lang = ""
        source = "auto"
        if snippets:
            first = snippets[0]
            lang = getattr(first, "language_code", "") or ""
            is_gen = getattr(first, "is_generated", None)
            if is_gen is not None:
                source = "auto" if is_gen else "manual"

        return {"ok": True, "text": text, "language": lang, "source": source, "error": None}

    except (TranscriptsDisabled, NoTranscriptFound):
        return {"ok": False, "text": "", "language": "", "source": "", "error": "no transcript available"}
    except RequestBlocked as e:
        return {"ok": False, "text": "", "language": "", "source": "", "error": f"IP blocked by YouTube"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "language": "", "source": "", "error": f"{type(e).__name__}: {e}"}
