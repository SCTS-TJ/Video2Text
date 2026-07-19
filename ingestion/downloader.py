"""视频/音频下载层, 基于 yt-dlp。

- 下载无水印视频源 (mp4)
- 可指定 audio_only=True 只抽音频
- 下载视频后自动提取音频供 ASR 使用
- 通过旁路由代理访问被墙站点
"""
import os
import subprocess
import sys

from .config import get_config, proxy_env

# yt-dlp 可能只在 venv 中, subprocess 默认 PATH 不包含 venv/bin
_YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
if not os.path.isfile(_YTDLP):
    _YTDLP = "yt-dlp"  # 兜底: 系统 PATH

_FFMPEG = "/opt/homebrew/bin/ffmpeg"


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

    # 输出模板
    out_tmpl = os.path.join(cfg.download_dir, "%(title)s [%(id)s].%(ext)s")

    cmd = [_YTDLP, "--no-playlist", "--restrict-filenames", "--proxy", cfg.http_proxy]
    # JS 运行时: 解决 YouTube n-challenge
    cmd += ["--js-runtimes", f"node:{cfg.ytdlp_js_runtime}", "--remote-components", "ejs:github"]
    # cookies
    if cfg.ytdlp_cookies_file and os.path.isfile(cfg.ytdlp_cookies_file):
        cmd += ["--cookies", cfg.ytdlp_cookies_file]
    elif cfg.ytdlp_cookies_from_browser:
        cmd += ["--cookies-from-browser", cfg.ytdlp_cookies_from_browser]

    if audio_only:
        # 纯音频: 下载最佳音频格式并转 mp3
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        result_mode = "audio"
    else:
        # 视频: 下载最佳画质(含音频), 保证 mp4 容器
        cmd += ["--merge-output-format", "mp4", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]
        result_mode = "video"

    cmd += ["--print", "after_move:filepath", "-o", out_tmpl, url]
    return _run_ytdlp(cmd, result_mode)


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
        subprocess.run(
            [_FFMPEG, "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame",
             "-q:a", "2", audio_path],
            capture_output=True, text=True, timeout=600,
        )

    vid_result["video_path"] = video_path
    vid_result["path"] = audio_path if os.path.isfile(audio_path) else video_path
    vid_result["audio_ext"] = "mp3" if os.path.isfile(audio_path) else ""
    vid_result["mode"] = "video"
    if os.path.isfile(audio_path):
        vid_result["ext"] = "mp4"  # 视频后缀

    return vid_result


def _run_ytdlp(cmd: list, mode: str) -> dict:
    """执行 yt-dlp 并解析结果。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, **proxy_env()},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode, "error": "download timeout"}
    except Exception as e:
        return {"ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode, "error": f"{type(e).__name__}: {e}"}

    if proc.returncode != 0:
        return {
            "ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode,
            "error": proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown yt-dlp error",
        }

    path = proc.stdout.strip().split("\n")[-1].strip()
    if not path or not os.path.isfile(path):
        path = _resolve_output(proc.stderr)
    if not path or not os.path.isfile(path):
        return {"ok": False, "path": "", "video_path": "", "title": "", "ext": "", "mode": mode, "error": "file not found after download"}
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
