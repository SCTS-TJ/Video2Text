"""视频/音频下载层, 基于 yt-dlp。

- 下载无水印视频源 (mp4)
- 可指定 audio_only=True 只抽音频
- 下载视频后自动提取音频供 ASR 使用
- 通过旁路由代理访问被墙站点
- 国内站点 (Bilibili 等) 不走代理, 避免海外 IP 被拒
"""
import os
import subprocess
import sys
from urllib.parse import urlparse

from .config import get_config, proxy_env
from .logger import get_logger

logger = get_logger(__name__)

# yt-dlp 可能只在 venv 中, subprocess 默认 PATH 不包含 venv/bin
_YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
if not os.path.isfile(_YTDLP):
    _YTDLP = "yt-dlp"  # 兜底: 系统 PATH

_FFMPEG = "/opt/homebrew/bin/ffmpeg"

# 国内站点域名列表 (这些站点不需要走代理)
_CHINESE_DOMAINS = [
    "bilibili.com", "b23.tv",
    "youku.com",
    "iqiyi.com",
    "tudou.com",
    "douyin.com",
    "kuaishou.com",
    "weibo.com",
    "zhihu.com",
    "xigua.com", "ixigua.com",
]


def _is_chinese_site(url: str) -> bool:
    """判断 URL 是否为国内视频站点, 决定是否跳过代理。"""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return False
    for domain in _CHINESE_DOMAINS:
        if domain in hostname:
            return True
    return False


def _bilibili_headers(url: str, cmd: list) -> None:
    """Bilibili 需要额外请求头, 否则会 412 Precondition Failed。"""
    if "bilibili" in url or "b23.tv" in url:
        cmd += [
            "--add-header", "Referer: https://www.bilibili.com",
            "--add-header", "Origin: https://www.bilibili.com",
        ]


def download_media(url: str, audio_only: bool = False) -> dict:
    """下载给定链接的媒体。

    返回:
        {
            "ok": bool,
            "path": str,        # 落地文件绝对路径 (音频/视频)
            "video_path": str,  # 视频文件路径 (audio_only=False 时同 path)
            "title": str,
            "ext": str,
            "mode": str,        # "video" | "audio"
            "error": str|None,
        }
    """
    cfg = get_config()
    os.makedirs(cfg.download_dir, exist_ok=True)

    mode_str = "audio" if audio_only else "video"
    logger.info("开始下载 url=%s mode=%s proxy=%s", url, mode_str, cfg.http_proxy)

    # 输出模板
    out_tmpl = os.path.join(cfg.download_dir, "%(title)s [%(id)s].%(ext)s")

    # 判断是否国内站点
    is_cn = _is_chinese_site(url)

    # 国内站点不走代理, 海外站点走旁路由代理
    cmd = [_YTDLP, "--no-playlist", "--restrict-filenames"]
    if not is_cn:
        cmd += ["--proxy", cfg.http_proxy]
    else:
        logger.info("国内站点, 跳过代理 url=%s", url)

    # JS 运行时: 解决 YouTube n-challenge (仅海外需要)
    if not is_cn:
        cmd += ["--js-runtimes", f"node:{cfg.ytdlp_js_runtime}", "--remote-components", "ejs:github"]
    # cookies
    if cfg.ytdlp_cookies_file and os.path.isfile(cfg.ytdlp_cookies_file):
        cmd += ["--cookies", cfg.ytdlp_cookies_file]
    elif cfg.ytdlp_cookies_from_browser:
        cmd += ["--cookies-from-browser", cfg.ytdlp_cookies_from_browser]

    # Bilibili 专用请求头 (防 412)
    _bilibili_headers(url, cmd)

    if audio_only:
        # 纯音频: 下载最佳音频格式并转 mp3
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        result_mode = "audio"
    else:
        # 视频: 下载最佳画质(含音频), 保证 mp4 容器
        cmd += ["--merge-output-format", "mp4", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]
        result_mode = "video"

    cmd += ["--print", "after_move:filepath", "-o", out_tmpl, url]
    return _run_ytdlp(cmd, result_mode, skip_proxy_env=is_cn)


def download_with_audio(url: str) -> dict:
    """下载完整视频 + 从中提取音频供 ASR, 返回双路径。

    返回:
        {
            "ok": bool,
            "path": str,        # 音频文件路径 (给 ASR)
            "video_path": str,  # 视频文件路径 (给前端预览)
            "title": str,
            "ext": str,         # 视频后缀
            "audio_ext": str,   # 音频后缀
            "mode": str,        # "video"
            "error": str|None,
        }
    """
    cfg = get_config()
    os.makedirs(cfg.download_dir, exist_ok=True)

    # 1. 下载视频
    vid_result = download_media(url, audio_only=False)
    if not vid_result.get("ok"):
        return {**vid_result, "video_path": "", "audio_ext": ""}

    video_path = vid_result["path"]
    base = os.path.splitext(video_path)[0]
    audio_path = base + ".mp3"

    # 2. 如果音频文件还不存在, 用 ffmpeg 提取
    if not os.path.isfile(audio_path):
        logger.info("ffmpeg提取音频 video=%s -> audio=%s", video_path, audio_path)
        subprocess.run(
            [_FFMPEG, "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame",
             "-q:a", "2", audio_path],
            capture_output=True, text=True, timeout=600,
        )
        if os.path.isfile(audio_path):
            logger.info("ffmpeg提取完成 audio=%s", audio_path)
        else:
            logger.warning("ffmpeg提取失败 audio=%s", audio_path)

    vid_result["video_path"] = video_path
    vid_result["path"] = audio_path if os.path.isfile(audio_path) else video_path
    vid_result["audio_ext"] = "mp3" if os.path.isfile(audio_path) else ""
    vid_result["mode"] = "video"
    if os.path.isfile(audio_path):
        vid_result["ext"] = "mp4"  # 视频后缀

    return vid_result


def _run_ytdlp(cmd: list, mode: str, skip_proxy_env: bool = False) -> dict:
    """执行 yt-dlp 并解析结果。"""
    # 构建环境变量
    env = {**os.environ}
    if not skip_proxy_env:
        env.update(proxy_env())
    else:
        # 国内站点: 清除代理环境变量
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.pop(key, None)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp 超时 cmd=%s", cmd[:4])
        return {"ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode, "error": "download timeout"}
    except Exception as e:
        logger.error("yt-dlp 异常 %s: %s", type(e).__name__, e)
        return {"ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode, "error": f"{type(e).__name__}: {e}"}

    if proc.returncode != 0:
        err_msg = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown yt-dlp error"
        logger.warning("yt-dlp 失败 retcode=%s error=%s", proc.returncode, err_msg)
        return {
            "ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode,
            "error": err_msg,
        }

    path = proc.stdout.strip().split("\n")[-1].strip()
    if not path or not os.path.isfile(path):
        path = _resolve_output(proc.stderr)
    if not path or not os.path.isfile(path):
        logger.warning("yt-dlp 完成后文件未找到 stdout=%s", proc.stdout.strip()[:200])
        return {"ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode, "error": "file not found after download"}

    file_size = os.path.getsize(path) if os.path.isfile(path) else 0
    logger.info("yt-dlp 完成 path=%s size=%dMB mode=%s", path, file_size // (1024 * 1024), mode)
    return {
        "ok": True,
        "path": path,
        "video_path": path if mode == "video" else "",
        "title": os.path.splitext(os.path.basename(path))[0],
        "ext": os.path.splitext(path)[1].lstrip("."),
        "mode": mode,
        "error": None,
    }


def _resolve_output(stderr: str) -> str:
    """从 yt-dlp 输出中解析已合并/已下载的文件路径。"""
    candidates = []
    for line in reversed(stderr.splitlines()):
        if "Destination:" in line:
            candidates.append(line.split("Destination:", 1)[1].strip())
        if "Merging formats into" in line:
            candidates.append(line.split('"', 1)[1].rstrip('"'))
    for path in candidates:
        if os.path.isfile(path):
            return path
        base = os.path.splitext(path)[0]
        for ext in (".mp3", ".m4a", ".webm", ".mp4", ".opus", ".ogg"):
            alt = base + ext
            if os.path.isfile(alt):
                return alt
    return ""
