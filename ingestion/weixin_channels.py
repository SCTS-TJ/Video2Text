"""微信视频号 (WeChat Channels / 视频号) 处理层。

背景与结论 (2026-10-15 实测于 Dell 192.168.121.30):
  - yt-dlp 无 视频号 extractor, 只会落到 generic → `Unsupported URL`
  - 分享短链 `weixin.qq.com/sph/*` 落地到
    `channels.weixin.qq.com/finder-preview/pages/sph?id=...`,
    该页是纯 SPA (2.6KB 壳), 页面与接口都不返回明文媒体地址
  - 前端 bundle (`feed.*.js`) 用 **TEA** 解密视频流, 媒体为加密流
  => 纯服务端 HTTP 抓取拿不到可下载的视频。

因此本模块只做两件"不解密"的事:
  1. 精确识别 视频号 链接 (避免浪费一次必然失败的 yt-dlp 下载)
  2. 返回结构化、可操作的引导 (走"离线模式"上传已下载文件)

为何不在服务端实现下载/解密: 需要微信客户端会话 + 根证书中间人抓包,
不适合 headless 服务器, 且高度不稳定、有 ToS 风险。详见交接文档 v1.5.5。
"""
from urllib.parse import urlparse

from .logger import get_logger

logger = get_logger(__name__)

# 视频号 短链 / 落地页 域名
_SPH_HOSTS = ("weixin.qq.com", "channels.weixin.qq.com")

_HINT = (
    "微信视频号暂不支持自动下载: 该平台视频为加密流(TEA)且无公开 API, "
    "服务端无法直接抓取。可行做法: "
    "① 用抓包/解析工具(如 res-downloader)先在电脑上保存该视频文件; "
    "② 回到本页勾选「📁 离线模式」, 选择该文件进行转写。"
)


def is_weixin_channels(url: str) -> bool:
    """判断是否为微信视频号链接 (排除公众号文章 mp.weixin.qq.com)。"""
    try:
        p = urlparse(url)
    except Exception:
        return False
    host = (p.hostname or "").lower()
    if host == "channels.weixin.qq.com":
        return True
    if host == "weixin.qq.com":
        return "/sph" in p.path.lower()
    return False


def handle_weixin_channels(url: str) -> dict:
    """返回统一结构的结果: 不支持自动下载, 但给出可操作引导。"""
    logger.info("微信视频号链接, 走专用引导(不调用 yt-dlp) url=%s", url)
    return {
        "channel": "download_video",
        "ok": False,
        "text": "",
        "path": "",
        "video_path": "",
        "title": "",
        "ext": "",
        "mode": "video",
        "source": "",
        "language": "",
        "error": _HINT,
    }
