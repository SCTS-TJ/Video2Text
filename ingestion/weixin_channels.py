"""微信视频号 (WeChat Channels / 视频号) 处理层 — 通道F。

背景 (2026-10-15 实测于 Dell 192.168.121.30):
  - yt-dlp 无 视频号 extractor, 只会落到 generic → `Unsupported URL`
  - 分享短链 `weixin.qq.com/sph/*` 落地页是纯 SPA(2.6KB 壳), 页面/接口均不返回明文媒体地址
  - 前端 bundle 用 Isaac64 密钥流 XOR 解密(媒体为加密流, 仅前 128KB 加密)

方案: 复用成熟开源工具 **wx-downloader** (PyPI, 纯 Python, 无需 MITM/根证书/微信客户端):
  经腾讯元宝登录态解析分享链接 → 下载 → 内置解密 → 输出可播放 MP4。

本模块职责:
  1. `is_weixin_channels()` 精确识别 视频号 链接
  2. `download_weixin_video()` 调 wx-downloader 下载(通道F), 再抽 mp3 供 ASR
  3. `handle_weixin_channels()` 失败时返回可操作引导(如提示 `wx-downloader login`)

前置条件: 一次性 `wx-downloader login` 扫码登录元宝, 凭据 `~/.config/wx-channels/auth.json`。
详见交接文档 v1.5.7。
"""
import glob
import os
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlparse

from .config import get_config
from .downloader import extract_audio
from .logger import get_logger

logger = get_logger(__name__)

# 视频号 短链 / 落地页 域名
_SPH_HOSTS = ("weixin.qq.com", "channels.weixin.qq.com")

_HINT = (
    "微信视频号下载失败(未登录或解析失败)。请先在 Dell(192.168.121.30) 上以 boothgianni 运行 "
    "`wx-downloader login` 扫码登录(凭据过期需重新登录); "
    "或改走「📁 离线模式」上传已下载的视频文件。"
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


def _find_wxdl() -> str:
    """定位 wx-downloader 可执行文件 (环境变量 > 当前 venv > PATH)。"""
    cand = os.getenv("WX_DOWNLOADER_BIN")
    if cand and os.path.isfile(cand):
        return cand
    cand = os.path.join(os.path.dirname(sys.executable), "wx-downloader")
    if os.path.isfile(cand):
        return cand
    return "wx-downloader"


def _direct_env() -> dict:
    """构造子进程环境: 去除代理(元宝/微信 CDN 均为国内直连)。"""
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def download_weixin_video(url: str, timeout: int = 900) -> dict:
    """通道F: 用 wx-downloader 解析+下载+解密 视频号, 返回 ingest 结果结构。

    wx-downloader 经腾讯元宝登录态解析分享链接 -> 下载 -> 内置解密 -> 输出可播放 MP4。
    为避免依赖其 --json 的输出 schema, 这里用独立输出目录直接收集 *.mp4 产物。
    """
    cfg = get_config()
    wxdl = _find_wxdl()
    token = uuid.uuid4().hex[:8]
    out_dir = os.path.join(cfg.download_dir, f"_wx_out_{token}")
    cache_dir = os.path.join(cfg.download_dir, "_wx_cache")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)

    cmd = [wxdl, "download", url, "--no-login", "--json", "-o", out_dir, "--cache-dir", cache_dir]
    logger.info("通道F: wx-downloader 开始 url=%s", url)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_direct_env())
    except FileNotFoundError:
        shutil.rmtree(out_dir, ignore_errors=True)
        return {"ok": False, "error": "未安装 wx-downloader (pip install wx-downloader)"}
    except subprocess.TimeoutExpired:
        shutil.rmtree(out_dir, ignore_errors=True)
        logger.warning("通道F: wx-downloader 超时 url=%s", url)
        return {"ok": False, "error": "wx-downloader 超时"}

    mp4s = glob.glob(os.path.join(out_dir, "*.mp4"))
    if p.returncode == 0 and mp4s:
        src = max(mp4s, key=os.path.getmtime)
        dst = os.path.join(cfg.download_dir, os.path.basename(src))
        if os.path.exists(dst):  # 同名不覆盖
            stem, ext = os.path.splitext(os.path.basename(src))
            dst = os.path.join(cfg.download_dir, f"{stem}_{token}{ext}")
        shutil.move(src, dst)
        shutil.rmtree(out_dir, ignore_errors=True)
        title = os.path.splitext(os.path.basename(dst))[0]
        audio = extract_audio(dst)
        logger.info("通道F: 下载成功 video=%s audio=%s", dst, audio or "(未提取)")
        return {"ok": True, "path": audio or dst, "video_path": dst,
                "title": title, "ext": "mp4", "mode": "video", "error": None}

    shutil.rmtree(out_dir, ignore_errors=True)
    err_lines = [ln for ln in (p.stderr or "").strip().splitlines() if ln.strip()]
    msg = err_lines[-1] if err_lines else (p.stdout or "").strip()[:300] or "wx-downloader 失败"
    logger.warning("通道F: 下载失败 rc=%s error=%s", p.returncode, msg)
    return {"ok": False, "error": msg}


def handle_weixin_channels(url: str, reason: str = "") -> dict:
    """视频号下载失败时的统一兜底结果(含可操作引导)。"""
    logger.info("微信视频号兜底引导 url=%s reason=%s", url, reason)
    detail = f"{reason}\n{_HINT}" if reason else _HINT
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
        "error": detail,
    }
