"""Bilibili 专用下载器。

yt-dlp 的 Bilibili extractor 被 B站 openresty WAF 拦截 (412)，但直接调 API 可用。
本模块绕过 yt-dlp，直接用 Python requests 调 Bilibili API：
  1. GET /x/web-interface/view?bvid=     → 视频信息
  2. GET /x/player/wbi/playurl?bvid=&cid= → DASH 流地址
  3. 流式下载 + ffmpeg 合并
"""

import os
import re
import subprocess
import time

import requests

from .logger import get_logger

logger = get_logger(__name__)

_FFMPEG = "/opt/homebrew/bin/ffmpeg"
_DOWNLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads"
)

_BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


def _extract_bvid(url: str) -> str | None:
    """从 Bilibili URL 中提取 BV 号。"""
    m = re.search(r"BV\w{10}", url)
    if m:
        return m.group(0)
    m = re.search(r"av(\d+)", url)
    if m:
        return m.group(0)
    return None


def _init_session() -> requests.Session:
    """初始化带 Bilibili cookies 的 requests Session。"""
    s = requests.Session()
    s.headers.update(_BILIBILI_HEADERS)
    try:
        s.get("https://www.bilibili.com/", timeout=15)
    except Exception as e:
        logger.warning("Bilibili首页访问失败 %s", e)
    return s


def _get_video_info(s: requests.Session, bvid: str) -> dict | None:
    """获取视频基本信息 (title, cid, duration)。"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        r = s.get(url, timeout=15)
        if r.status_code != 200:
            logger.warning("Bilibili API view HTTP %s", r.status_code)
            return None
        data = r.json()
        if data.get("code") != 0:
            logger.warning("Bilibili API view error code=%s msg=%s",
                           data.get("code"), data.get("message"))
            return None
        v = data["data"]
        return {
            "bvid": bvid,
            "title": v.get("title", ""),
            "cid": v.get("cid"),
            "duration": v.get("duration", 0),
            "pic": v.get("pic", ""),
            "owner": v.get("owner", {}).get("name", ""),
        }
    except Exception as e:
        logger.error("获取Bilibili视频信息失败 %s", e, exc_info=True)
        return None


def _get_playurl(s: requests.Session, bvid: str, cid: int, qn: int = 80) -> dict | None:
    """获取视频/音频 DASH 流地址。"""
    url = (
        f"https://api.bilibili.com/x/player/wbi/playurl"
        f"?bvid={bvid}&cid={cid}&qn={qn}&fnver=0&fnval=4048&fourk=1"
    )
    try:
        r = s.get(url, timeout=30)
        if r.status_code != 200:
            logger.warning("Bilibili playurl HTTP %s", r.status_code)
            return None
        data = r.json()
        if data.get("code") != 0:
            logger.warning("Bilibili playurl error code=%s msg=%s",
                           data.get("code"), data.get("message"))
            return None
        return data["data"]
    except Exception as e:
        logger.error("获取Bilibili播放地址失败 %s", e, exc_info=True)
        return None


def _download_stream(url: str, path: str, s: requests.Session, max_retries: int = 2) -> bool:
    """流式下载单个 m4s 流到文件（带重试）。"""
    for attempt in range(1 + max_retries):
        try:
            r = s.get(url, stream=True, timeout=120)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            ok = os.path.isfile(path) and os.path.getsize(path) > 0
            if ok:
                logger.info("流下载完成 path=%s size=%dMB (attempt=%d)",
                            path, downloaded // (1024 * 1024), attempt + 1)
                return True
        except Exception as e:
            if attempt < max_retries:
                logger.warning("流下载重试 %d/2 url=%s error=%s", attempt + 1, url[:80], e)
                time.sleep(2)
            else:
                logger.warning("流下载失败 url=%s error=%s", url[:80], e)
    return False


def _ffmpeg_merge(video_path: str, audio_path: str, output_path: str) -> bool:
    """用 ffmpeg 合并视频+音频为 mp4。"""
    if not os.path.isfile(video_path) and not os.path.isfile(audio_path):
        return False
    try:
        cmd = [_FFMPEG, "-y"]
        if os.path.isfile(video_path):
            cmd += ["-i", video_path]
        if os.path.isfile(audio_path):
            cmd += ["-i", audio_path]
        cmd += ["-c", "copy"]
        if os.path.isfile(video_path) and os.path.isfile(audio_path):
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        cmd += [output_path]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        ok = os.path.isfile(output_path) and os.path.getsize(output_path) > 0
        if ok:
            logger.info("ffmpeg合并完成 output=%s", output_path)
        return ok
    except Exception as e:
        logger.warning("ffmpeg合并失败 %s", e)
        return False


def _safe_filename(title: str) -> str:
    """将标题转为安全的文件名。"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip()
    return safe[:100] or "bilibili_video"


def download_bilibili(url: str) -> dict:
    """下载 Bilibili 视频。

    返回格式（与 downloader.download_with_audio 兼容）:
        {"ok", "path", "video_path", "title", "ext", "mode", "error"}
    """
    bvid = _extract_bvid(url)
    if not bvid:
        return {"ok": False, "path": "", "video_path": "", "title": "",
                "ext": "", "mode": "video", "error": f"无法从URL提取BV号: {url}"}

    logger.info("Bilibili下载 bvid=%s url=%s", bvid, url)
    s = _init_session()

    info = _get_video_info(s, bvid)
    if not info:
        return {"ok": False, "path": "", "video_path": "", "title": bvid,
                "ext": "", "mode": "video", "error": "无法获取Bilibili视频信息"}

    title = info["title"]
    cid = info["cid"]
    logger.info("Bilibili视频信息 title=%s cid=%s duration=%ss", title, cid, info["duration"])

    playdata = _get_playurl(s, bvid, cid, qn=80)
    if not playdata:
        playdata = _get_playurl(s, bvid, cid, qn=32)
    if not playdata:
        return {"ok": False, "path": "", "video_path": "", "title": title,
                "ext": "", "mode": "video", "error": "无法获取Bilibili播放地址"}

    video_url = None
    audio_url = None
    dash = playdata.get("dash")
    if dash:
        videos = dash.get("video", [])
        audios = dash.get("audio", [])
        if videos:
            best = max(videos, key=lambda v: v.get("id", 0))
            video_url = best.get("baseUrl") or (best.get("backupUrl") or [None])[0]
            logger.info("Bilibili视频流 id=%s codecs=%s", best.get("id"), best.get("codecs"))
        # 收集所有可用的音频流（如果首选失败就尝试下一个）
        if audios:
            # 按带宽从高到低排序
            audios_sorted = sorted(audios, key=lambda a: a.get("bandwidth", 0), reverse=True)
            logger.info("Bilibili可用音频流数=%d", len(audios_sorted))
    else:
        durls = playdata.get("durl", [])
        if durls:
            video_url = durls[0].get("url")

    if not video_url and not (audios_sorted if dash else False):
        return {"ok": False, "path": "", "video_path": "", "title": title,
                "ext": "", "mode": "video", "error": "无法解析Bilibili流地址"}

    os.makedirs(_DOWNLOAD_DIR, exist_ok=True)
    safe_title = _safe_filename(title)
    ts = time.strftime("%Y%m%d_%H%M%S")
    video_path = os.path.join(_DOWNLOAD_DIR, f"{safe_title}_{ts}.mp4")
    audio_path = os.path.join(_DOWNLOAD_DIR, f"{safe_title}_{ts}.mp3")
    temp_video = video_path + ".video.m4s"
    temp_audio = video_path + ".audio.m4s"

    dl_video_ok = _download_stream(video_url, temp_video, s) if video_url else False
    # 音频: 逐个尝试所有可用流（高带宽优先）, 附带重试
    dl_audio_ok = False
    if dash and audios_sorted:
        for idx, a_stream in enumerate(audios_sorted):
            a_url = a_stream.get("baseUrl")
            logger.info("尝试音频流 %d/%d bandwidth=%s", idx + 1, len(audios_sorted),
                        a_stream.get("bandwidth", 0))
            if _download_stream(a_url, temp_audio, s, max_retries=1):
                dl_audio_ok = True
                break
            # 尝试备用地址
            backup_urls = a_stream.get("backupUrl") or []
            for b_url in backup_urls:
                logger.info("尝试音频备用流 %s", b_url[:60])
                if _download_stream(b_url, temp_audio, s, max_retries=1):
                    dl_audio_ok = True
                    break
            if dl_audio_ok:
                break

    if not dl_video_ok and not dl_audio_ok:
        for f in [temp_video, temp_audio]:
            if os.path.isfile(f): os.remove(f)
        return {"ok": False, "path": "", "video_path": "", "title": title,
                "ext": "", "mode": "video", "error": "Bilibili流下载失败"}

    merge_ok = _ffmpeg_merge(
        temp_video if dl_video_ok else "",
        temp_audio if dl_audio_ok else "",
        video_path,
    )

    # 只要有合并后的视频, 就尝试提取音频 (确保 ASR 能读)
    if os.path.isfile(video_path) and not os.path.isfile(audio_path):
        logger.info("从视频提取音频 %s -> %s", video_path, audio_path)
        subprocess.run(
            [_FFMPEG, "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame",
             "-q:a", "2", audio_path],
            capture_output=True, text=True, timeout=300,
        )
        if os.path.isfile(audio_path):
            logger.info("音频提取完成 size=%dMB", os.path.getsize(audio_path) // (1024 * 1024))
        else:
            logger.warning("音频提取失败, 回退到视频文件")

    for f in [temp_video, temp_audio]:
        if os.path.isfile(f): os.remove(f)

    final_audio = audio_path if os.path.isfile(audio_path) else video_path
    final_video = video_path if os.path.isfile(video_path) else ""

    logger.info("Bilibili下载完成 video=%s audio=%s title=%s",
                final_video, final_audio, title)
    return {
        "ok": True,
        "path": final_audio,
        "video_path": final_video,
        "title": title,
        "ext": "mp4",
        "mode": "video",
        "error": None,
    }
