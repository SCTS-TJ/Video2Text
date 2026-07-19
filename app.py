"""Video2Text Web 服务 (阶段一: 采集 + 预览)。

FastAPI 提供:
  GET  /                  -> 精美单页界面
  POST /api/ingest        -> 采集链接, 异步返回文字/视频信息
  GET  /api/task/{id}     -> 轮询任务进度 (ASR 在后台跑)
  GET  /files/{name}      -> 流式返回已下载的媒体文件 (支持 Range 分块)

前端零构建: 内联 HTML/CSS/JS, 左右分栏结构。
"""
import mimetypes
import os
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from ingestion import ingest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
TRANSCRIPT_DIR = os.path.join(BASE_DIR, "transcripts")
TEMPLATE = os.path.join(BASE_DIR, "templates", "index.html")

os.makedirs(TRANSCRIPT_DIR, exist_ok=True)


def _save_transcript(result: dict, url: str) -> str:
    """自动落盘: .txt(纯文本) + .md(带时间戳)"""
    text = result.get("text", "")
    if not text:
        return ""
    segs = result.get("segments", [])
    title = result.get("title") or "untitled"
    safe_title = re.sub(r'[\/:*?"<>|]', "_", title)[:80]
    ts = time.strftime("%Y%m%d_%H%M%S")

    # .txt
    txt_name = safe_title + "_" + ts + ".txt"
    txt_path = os.path.join(TRANSCRIPT_DIR, txt_name)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    # .md
    md_name = safe_title + "_" + ts + ".md"
    md_path = os.path.join(TRANSCRIPT_DIR, md_name)
    with open(md_path, "w", encoding="utf-8") as f:
        dur = result.get("duration", 0)
        lang = result.get("language", "?")
        src = result.get("source", "whisper")
        f.write("# " + title + "\n\n")
        f.write("> 来源: " + str(url) + " | 时长: " + str(round(dur, 1)) + "s | 语言: " + str(lang) + " | 模式: " + str(src) + "\n\n---\n\n")
        if segs:
            for s in segs:
                st = int(s.get("start", 0))
                m = st // 60
                sec = st % 60
                stamp = f"{m:02d}:{sec:02d}"
                f.write("**" + stamp + "** " + s.get("text", "") + "\n\n")
        else:
            f.write(text)

    return txt_name

app = FastAPI(title="Video2Text")

# ---- 异步任务管理 ----

tasks: dict = {}  # task_id -> {"status": "downloading"|"transcribing"|"done"|"error", "result": {}, "error": str|None}
tasks_lock = threading.Lock()


class IngestReq(BaseModel):
    url: str
    force_download: bool = False
    local_file: str = ""  # 离线模式: 直接用已有文件, 跳过下载


def _youtube_embed(url: str) -> "str | None":
    h = urlparse(url).hostname or ""
    if "youtube" in h or "youtu.be" in h:
        if h == "youtu.be":
            vid = urlparse(url).path.lstrip("/")
        elif "/shorts/" in url:
            vid = url.split("/shorts/")[1].split("/")[0]
        elif "/embed/" in url:
            vid = url.split("/embed/")[1].split("/")[0]
        else:
            from urllib.parse import parse_qs
            vid = parse_qs(urlparse(url).query).get("v", [None])[0]
        if vid:
            # YouTube 嵌入用 ?enablejsapi=1 支持 JS 控制
            return f"https://www.youtube.com/embed/{vid}?enablejsapi=1"
    return None


def _build_payload(result: dict, url: str) -> dict:
    """构建前端友好的响应载荷, 始终保留 file_url 以便前端预览"""
    payload = {k: result[k] for k in result}
    embed = _youtube_embed(url)
    payload["embed_url"] = embed

    # 只要有 video_path, 优先用视频预览
    if result.get("video_path"):
        name = os.path.basename(result["video_path"])
        payload["file_url"] = f"/files/{name}"
    elif result.get("path"):
        name = os.path.basename(result["path"])
        payload["file_url"] = f"/files/{name}"
    else:
        payload["file_url"] = ""

    # 判断是否有可预览的媒体文件
    payload["has_media"] = bool(result.get("video_path") or result.get("path"))
    # 标记是视频还是音频
    if result.get("video_path"):
        payload["media_type"] = "video"
    elif result.get("mode") == "audio":
        payload["media_type"] = "audio"
    else:
        payload["media_type"] = ""

    return payload


def _run_ingest_task(task_id: str, url: str, local_file: str = ""):
    """后台运行采集 + ASR, 完成后更新 tasks 字典"""
    try:
        if local_file:
            # ===== 离线模式: 跳过下载, 直接用已有文件 =====
            with tasks_lock:
                tasks[task_id]["status"] = "transcribing"
            file_path = os.path.join(DOWNLOAD_DIR, local_file)
            if not os.path.isfile(file_path):
                with tasks_lock:
                    tasks[task_id]["status"] = "error"
                    tasks[task_id]["error"] = f"local file not found: {local_file}"
                return

            # 如果是视频文件, 提取音频给 ASR, 视频用于预览
            video_exts = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
            ext = os.path.splitext(local_file)[1].lower()
            is_video = ext in video_exts
            audio_path = file_path

            if is_video:
                # 从视频中提取音频
                audio_path = os.path.splitext(file_path)[0] + ".mp3"
                if not os.path.isfile(audio_path):
                    import subprocess
                    subprocess.run(
                        ["/opt/homebrew/bin/ffmpeg", "-y", "-i", file_path,
                         "-vn", "-acodec", "libmp3lame", "-q:a", "2", audio_path],
                        capture_output=True, text=True, timeout=600,
                    )
                    if not os.path.isfile(audio_path):
                        audio_path = file_path  # 回退: 直接用视频文件

            from ingestion.asr import transcribe
            asr_result = transcribe(audio_path, language="zh")
            result = {
                "channel": "asr",
                "ok": asr_result["ok"],
                "text": asr_result.get("text", ""),
                "path": audio_path,
                "video_path": file_path if is_video else "",
                "title": os.path.splitext(local_file)[0],
                "ext": ext.lstrip("."),
                "mode": "video" if is_video else "audio",
                "source": "whisper",
                "language": "zh",
                "segments": asr_result.get("segments", []),
                "duration": asr_result.get("duration", 0),
                "error": asr_result.get("error"),
            }
            with tasks_lock:
                tasks[task_id]["status"] = "done" if asr_result["ok"] else "error"
                tasks[task_id]["result"] = _build_payload(result, url)
                tasks[task_id]["error"] = result["error"]
                _save_transcript(result, url)
            return

        # ===== 在线模式: 下载 + ASR =====
        # 阶段1: 采集 (下载 或 字幕直取)
        with tasks_lock:
            tasks[task_id]["status"] = "downloading"
        result = ingest(url, transcribe=False)  # 先不转录, 只下载

        if not result["ok"]:
            with tasks_lock:
                tasks[task_id]["status"] = "error"
                tasks[task_id]["error"] = result.get("error", "ingest failed")
                tasks[task_id]["result"] = _build_payload(result, url)
            return

        # 字幕直取通道: 直接完成
        if result["channel"] == "transcript":
            with tasks_lock:
                tasks[task_id]["status"] = "done"
                tasks[task_id]["result"] = _build_payload(result, url)
                _save_transcript(result, url)
            return

        # 下载通道: 需要 ASR
        with tasks_lock:
            tasks[task_id]["status"] = "transcribing"
            tasks[task_id]["result"] = _build_payload(result, url)

        # 阶段2: ASR 转写
        from ingestion.asr import transcribe
        asr_result = transcribe(result["path"], language="zh")
        if asr_result["ok"]:
            result["channel"] = "asr"
            result["text"] = asr_result["text"]
            result["segments"] = asr_result.get("segments", [])
            result["duration"] = asr_result.get("duration", 0)
            result["source"] = "whisper"
            result["language"] = "zh"

        with tasks_lock:
            tasks[task_id]["status"] = "done"
            tasks[task_id]["result"] = _build_payload(result, url)
            _save_transcript(result, url)

    except Exception as e:
        with tasks_lock:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = f"{type(e).__name__}: {e}"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/ingest")
def api_ingest(req: IngestReq) -> dict:
    """提交采集任务, 立即返回 task_id, 后台执行"""
    task_id = uuid.uuid4().hex[:12]
    with tasks_lock:
        tasks[task_id] = {"status": "downloading", "result": {}, "error": None}

    t = threading.Thread(target=_run_ingest_task, args=(task_id, req.url), kwargs={"local_file": req.local_file}, daemon=True)
    t.start()

    return {"task_id": task_id, "status": "downloading"}


@app.get("/api/files")
def api_files() -> list[dict]:
    """列出 downloads 目录下已有的音频/视频文件, 用于离线模式选择"""
    audio_exts = {".mp3", ".wav", ".aiff", ".aac", ".m4a", ".opus", ".ogg", ".flac", ".webm", ".mp4"}
    files = []
    if not os.path.isdir(DOWNLOAD_DIR):
        return files
    for name in sorted(os.listdir(DOWNLOAD_DIR), key=lambda n: os.path.getmtime(os.path.join(DOWNLOAD_DIR, n)), reverse=True):
        path = os.path.join(DOWNLOAD_DIR, name)
        if os.path.isfile(path):
            ext = os.path.splitext(name)[1].lower()
            if ext in audio_exts:
                size_mb = os.path.getsize(path) / (1024 * 1024)
                mtime = os.path.getmtime(path)
                files.append({
                    "name": name,
                    "size_mb": round(size_mb, 1),
                    "ext": ext.lstrip("."),
                    "modified": datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M"),
                })
    return files


@app.get("/api/task/{task_id}")
def api_task(task_id: str) -> dict:
    """轮询任务状态"""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {
            "task_id": task_id,
            "status": task["status"],
            "result": task["result"],
            "error": task["error"],
        }

@app.get("/files/{name}")
def serve_file(name: str, request: Request):
    """流式返回媒体文件, 支持 Range 分块 (让视频可拖动进度条)。"""
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = os.path.join(DOWNLOAD_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")

    file_size = os.path.getsize(path)
    content_type, _ = mimetypes.guess_type(path)
    if not content_type:
        content_type = "application/octet-stream"

    range_header = request.headers.get("range")
    if range_header:
        # 解析 Range: bytes=start-end
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end_str = match.group(2)
            end = int(end_str) if end_str else min(start + (1024 * 1024), file_size - 1)  # 默认 1MB 分块
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(chunk_size)

            return Response(
                content=data,
                status_code=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Type": content_type,
                    "Content-Length": str(chunk_size),
                },
            )

    # 无 Range: 返回完整文件
    return FileResponse(path, media_type=content_type)


