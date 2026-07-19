"""视频/音频下载层, 基于 yt-dlp。

- 优先下载无水印视频源
- 若仅需音频(为下一步 ASR), 可指定 audio_only=True 抽音频流
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


def download_media(url: str, audio_only: bool = False) -> dict:
    """下载给定链接的媒体。

    返回:
        {
            "ok": bool,
            "path": str,        # 落地文件绝对路径
            "title": str,
            "ext": str,
            "mode": str,        # "video" | "audio"
            "error": str|None,
        }
    """
    cfg = get_config()
    os.makedirs(cfg.download_dir, exist_ok=True)

    # 输出模板: 标题前缀 + 自增编号避免重名
    out_tmpl = os.path.join(cfg.download_dir, "%(title)s [%(id)s].%(ext)s")

    cmd = [_YTDLP, "--no-playlist", "--restrict-filenames", "--proxy", cfg.http_proxy]
    # JS 运行时: 解决 YouTube n-challenge
    cmd += ["--js-runtimes", f"node:{cfg.ytdlp_js_runtime}", "--remote-components", "ejs:github"]
    # cookies: 优先用导出的 cookies.txt(后台服务无需 keychain 弹窗)
    # 否则借用本机浏览器登录态, 绕过 YouTube "confirm you're not a bot"
    if cfg.ytdlp_cookies_file and os.path.isfile(cfg.ytdlp_cookies_file):
        cmd += ["--cookies", cfg.ytdlp_cookies_file]
    elif cfg.ytdlp_cookies_from_browser:
        cmd += ["--cookies-from-browser", cfg.ytdlp_cookies_from_browser]
    if audio_only:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    # --print after_move:filepath 精确获取最终文件路径
    cmd += ["--print", "after_move:filepath", "-o", out_tmpl, url]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, **proxy_env()},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": "", "title": "", "ext": "", "mode": "", "error": "download timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": "", "title": "", "ext": "", "mode": "", "error": f"{type(e).__name__}: {e}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "path": "",
            "title": "",
            "ext": "",
            "mode": "audio" if audio_only else "video",
            "error": proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown yt-dlp error",
        }

    # 从 --print after_move:filepath 获取精确路径
    path = proc.stdout.strip().split("\n")[-1].strip()
    if not path or not os.path.isfile(path):
        # 兜底: 旧版解析
        path = _resolve_output(proc.stderr)
    if not path or not os.path.isfile(path):
        return {"ok": False, "path": "", "title": "", "ext": "", "mode": "", "error": "file not found after download"}
    return {
        "ok": True,
        "path": path,
        "title": os.path.splitext(os.path.basename(path))[0],
        "ext": os.path.splitext(path)[1].lstrip("."),
        "mode": "audio" if audio_only else "video",
        "error": None,
    }


def _resolve_output(stderr: str) -> str:
    """从 yt-dlp 输出中解析已合并/已下载的文件路径。
    
    注意: yt-dlp -x 会先下载 m4a 再转换为 mp3, Destination 行指向 m4a,
    但最终文件是 mp3。因此找到路径后检查实际存在的文件。
    """
    candidates = []
    for line in reversed(stderr.splitlines()):
        if "Destination:" in line:
            candidates.append(line.split("Destination:", 1)[1].strip())
        if "Merging formats into" in line:
            candidates.append(line.split('"', 1)[1].rstrip('"'))
    
    for path in candidates:
        if os.path.isfile(path):
            return path
        # 后缀可能变了 (m4a -> mp3), 尝试其他扩展名
        base = os.path.splitext(path)[0]
        for ext in (".mp3", ".m4a", ".webm", ".mp4", ".opus", ".ogg"):
            alt = base + ext
            if os.path.isfile(alt):
                return alt
    return ""
