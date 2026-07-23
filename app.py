"""Video2Text Web 服务 (阶段一: 采集 + 预览 + 停止 + 跳过已转写)。

FastAPI 提供:
  GET  /                  -> 精美单页界面
  POST /api/ingest        -> 采集链接, 异步返回文字/视频信息
  GET  /api/task/{id}     -> 轮询任务进度 (ASR 在后台跑)
  GET  /files/{name}      -> 流式返回已下载的媒体文件 (支持 Range 分块)
  POST /api/stop/{task_id} -> 停止正在运行的任务
  GET  /api/check-existing  -> 检查文件是否已转写过

前端零构建: 内联 HTML/CSS/JS, 左右分栏结构。
"""
import json
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
from ingestion.index import add_entry, get_entry
from ingestion.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
TRANSCRIPT_DIR = os.path.join(BASE_DIR, "transcripts")
TEMPLATE = os.path.join(BASE_DIR, "templates", "index.html")

os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

# ---- 停止标志 (用于取消正在运行的任务) ----
_stop_flags: dict[str, threading.Event] = {}
_stop_flags_lock = threading.Lock()


def _is_already_transcribed(file_name: str) -> dict | None:
    """检查文件名是否已经在转写索引中。
    返回索引记录或 None。
    """
    # 用文件名查 index.json
    entry = get_entry(file_name)
    if entry and entry.get("segments"):
        return entry
    # 也检查去掉后缀的情况 (mp4 -> mp3)
    base, _ = os.path.splitext(file_name)
    for alt_ext in (".mp3", ".m4a", ".wav", ".opus", ".ogg"):
        alt_name = base + alt_ext
        entry = get_entry(alt_name)
        if entry and entry.get("segments"):
            return entry
    # 检查反向: .mp3 -> .mp4
    if file_name.endswith(".mp3"):
        for video_ext in (".mp4", ".webm", ".mkv", ".mov"):
            alt_name = base + video_ext
            entry = get_entry(alt_name)
            if entry and entry.get("segments"):
                return entry
    return None


def _index_entry(result: dict) -> None:
    """将转写结果写入 transcripts/index.json 持久索引"""
    text = result.get("text", "")
    if not text:
        return
    segs = result.get("segments", [])
    title = result.get("title") or "untitled"
    dur = result.get("duration", 0)
    lang = result.get("language", "?")
    media_type = result.get("media_type", "audio")

    # 取音频文件名作为 key
    audio_path = result.get("path") or ""
    name = os.path.basename(audio_path)

    # file_url
    file_url = ""
    if result.get("video_path"):
        vn = os.path.basename(result["video_path"])
        file_url = f"/files/{vn}"
    elif audio_path:
        file_url = f"/files/{name}"

    add_entry(
        media_name=name,
        segments=segs,
        duration=dur,
        title=title,
        file_url=file_url,
        media_type=media_type,
        language=lang,
    )


def _save_transcript(result: dict, url: str) -> tuple[str, str]:
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

    return txt_name, md_name

app = FastAPI(title="Video2Text")

# ---- 异步任务管理 ----

tasks: dict = {}  # task_id -> {"status": "downloading"|"transcribing"|"done"|"error"|"cancelled", "result": {}, "error": str|None}
tasks_lock = threading.Lock()


class IngestReq(BaseModel):
    url: str
    force_download: bool = False
    local_file: str = ""  # 离线模式: 直接用已有文件, 跳过下载


class RenameReq(BaseModel):
    old_name: str
    new_name: str


class DeleteReq(BaseModel):
    name: str
    keep_index: bool = False  # 是否保留索引记录


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

    if payload.get("file_url"):
        ext = os.path.splitext(payload["file_url"])[1].lower()
        payload["media_type"] = "video" if ext in (".mp4", ".webm", ".mkv", ".mov") else "audio"
    else:
        payload["media_type"] = ""

    return payload


def _check_cancelled(task_id: str) -> bool:
    """检查任务是否被取消, 取消时更新状态并返回 True"""
    with _stop_flags_lock:
        ev = _stop_flags.get(task_id)
        if ev and ev.is_set():
            with tasks_lock:
                tasks[task_id]["status"] = "cancelled"
                tasks[task_id]["error"] = "用户手动停止"
            return True
    return False


def _run_ingest_task(task_id: str, url: str, local_file: str = ""):
    """后台运行采集 + ASR, 完成后更新 tasks 字典"""
    try:
        logger.info("任务开始 task_id=%s url=%s local_file=%s", task_id, url, local_file)
        if local_file:
            # ===== 离线模式: 跳过下载, 直接用已有文件 =====
            logger.info("离线模式 task_id=%s local_file=%s", task_id, local_file)
            with tasks_lock:
                tasks[task_id]["status"] = "transcribing"
            file_path = os.path.join(DOWNLOAD_DIR, local_file)
            if not os.path.isfile(file_path):
                logger.warning("本地文件不存在 task_id=%s path=%s", task_id, file_path)
                with tasks_lock:
                    tasks[task_id]["status"] = "error"
                    tasks[task_id]["error"] = f"local file not found: {local_file}"
                return

            # 检查是否被取消
            if _check_cancelled(task_id):
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

            # 再次检查取消
            if _check_cancelled(task_id):
                return

            from ingestion.asr import transcribe
            asr_result = transcribe(audio_path, language="zh")
            # 检查取消 (ASR 完成后但还未落盘时)
            if _check_cancelled(task_id):
                return

            logger.info("离线ASR完成 task_id=%s ok=%s text_len=%d segments=%d",
                        task_id, asr_result["ok"], len(asr_result.get("text", "")),
                        len(asr_result.get("segments", [])))

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
                _index_entry(result)
            return

        # 阶段1: 采集 (下载 或 字幕直取)
        logger.info("开始下载 task_id=%s url=%s", task_id, url)
        with tasks_lock:
            tasks[task_id]["status"] = "downloading"

        if _check_cancelled(task_id):
            return

        result = ingest(url, transcribe=False)  # 先不转录, 只下载

        if not result["ok"]:
            logger.warning("下载失败 task_id=%s error=%s", task_id, result.get("error"))
            with tasks_lock:
                tasks[task_id]["status"] = "error"
                tasks[task_id]["error"] = result.get("error", "ingest failed")
                tasks[task_id]["result"] = _build_payload(result, url)
            return

        if _check_cancelled(task_id):
            return

        # 字幕直取通道: 直接完成
        if result["channel"] == "transcript":
            logger.info("字幕直取完成 task_id=%s text_len=%d", task_id, len(result.get("text", "")))
            with tasks_lock:
                tasks[task_id]["status"] = "done"
                tasks[task_id]["result"] = _build_payload(result, url)
                _save_transcript(result, url)
                _index_entry(result)
            return

        # 下载通道: 需要 ASR
        logger.info("下载完成 task_id=%s channel=%s path=%s video_path=%s",
                    task_id, result["channel"], result.get("path"), result.get("video_path"))
        with tasks_lock:
            tasks[task_id]["status"] = "transcribing"
            tasks[task_id]["result"] = _build_payload(result, url)

        if _check_cancelled(task_id):
            return

        # 阶段2: ASR 转写
        logger.info("开始ASR转写 task_id=%s audio_path=%s", task_id, result.get("path"))
        from ingestion.asr import transcribe
        asr_result = transcribe(result["path"], language="zh")

        if _check_cancelled(task_id):
            return

        if asr_result["ok"]:
            result["channel"] = "asr"
            result["text"] = asr_result["text"]
            result["segments"] = asr_result.get("segments", [])
            result["duration"] = asr_result.get("duration", 0)
            result["source"] = "whisper"
            result["language"] = "zh"
            logger.info("ASR转写完成 task_id=%s text_len=%d segments=%d duration=%.1fs",
                        task_id, len(asr_result.get("text", "")),
                        len(asr_result.get("segments", [])),
                        asr_result.get("duration", 0))
        else:
            logger.warning("ASR转写失败 task_id=%s error=%s", task_id, asr_result.get("error"))

        with tasks_lock:
            tasks[task_id]["status"] = "done"
            tasks[task_id]["result"] = _build_payload(result, url)
            _save_transcript(result, url)
            _index_entry(result)

    except Exception as e:
        logger.error("任务异常 task_id=%s error=%s", task_id, e, exc_info=True)
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
        tasks[task_id] = {"status": "queued", "result": {}, "error": None}

    # 初始化停止标志
    with _stop_flags_lock:
        _stop_flags[task_id] = threading.Event()

    source_desc = f"local_file={req.local_file}" if req.local_file else f"url={req.url}"
    logger.info("任务入队 task_id=%s %s force_download=%s", task_id, source_desc, req.force_download)

    t = threading.Thread(target=_run_ingest_task, args=(task_id, req.url), kwargs={"local_file": req.local_file}, daemon=True)
    t.start()

    return {"task_id": task_id, "status": "queued"}


@app.post("/api/stop/{task_id}")
def api_stop(task_id: str) -> dict:
    """停止正在运行的任务"""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task["status"] in ("done", "error", "cancelled"):
            return {"task_id": task_id, "status": task["status"], "message": "任务已结束, 无需停止"}

    with _stop_flags_lock:
        ev = _stop_flags.get(task_id)
        if ev:
            ev.set()  # 设置停止标志
            logger.info("任务停止 signal task_id=%s", task_id)

    with tasks_lock:
        tasks[task_id]["status"] = "cancelling"

    return {"task_id": task_id, "status": "cancelling", "message": "正在停止..."}


@app.get("/api/check-existing")
def api_check_existing(file_name: str = "") -> dict:
    """检查文件是否已经转写过。
    返回: {"transcribed": bool, "entry": dict|None}
    """
    if not file_name:
        return {"transcribed": False, "entry": None}
    entry = _is_already_transcribed(file_name)
    transcribed = entry is not None
    logger.debug("检查已转写 file_name=%s transcribed=%s", file_name, transcribed)
    return {
        "transcribed": transcribed,
        "entry": entry,
    }


@app.post("/api/rename")
def api_rename(req: RenameReq) -> list[dict]:
    """重命名 downloads/ 下的文件，同步更新 index.json。
    支持配对重命名: .mp4 和 .mp3 同时重命名。
    """
    old = req.old_name.strip()
    new = req.new_name.strip()
    if not old or not new:
        raise HTTPException(status_code=400, detail="old_name and new_name required")
    if "/" in new or ".." in new or "/" in old or ".." in old:
        raise HTTPException(status_code=400, detail="invalid filename")
    
    old_ext = os.path.splitext(old)[1].lower()
    new_ext = os.path.splitext(new)[1].lower()
    if not old_ext or not new_ext:
        raise HTTPException(status_code=400, detail="filename must have extension")
    if old_ext != new_ext:
        raise HTTPException(status_code=400, detail="cannot change file extension")
    
    old_path = os.path.join(DOWNLOAD_DIR, old)
    if not os.path.isfile(old_path):
        raise HTTPException(status_code=404, detail=f"file not found: {old}")
    new_path = os.path.join(DOWNLOAD_DIR, new)
    if os.path.isfile(new_path):
        raise HTTPException(status_code=409, detail=f"target filename already exists: {new}")
    
    renamed = []
    
    # 1. 重命名主文件
    os.rename(old_path, new_path)
    renamed.append((old, new))
    
    # 2. 查找配对文件 (mp4 <-> mp3) 并重命名
    base_old, _ = os.path.splitext(old)
    base_new, _ = os.path.splitext(new)
    if old_ext == ".mp4":
        pair_old = base_old + ".mp3"
        pair_new = base_new + ".mp3"
        pair_old_path = os.path.join(DOWNLOAD_DIR, pair_old)
        if os.path.isfile(pair_old_path):
            pair_new_path = os.path.join(DOWNLOAD_DIR, pair_new)
            if not os.path.isfile(pair_new_path):
                os.rename(pair_old_path, pair_new_path)
                renamed.append((pair_old, pair_new))
    elif old_ext == ".mp3":
        pair_old = base_old + ".mp4"
        pair_new = base_new + ".mp4"
        pair_old_path = os.path.join(DOWNLOAD_DIR, pair_old)
        if os.path.isfile(pair_old_path):
            pair_new_path = os.path.join(DOWNLOAD_DIR, pair_new)
            if not os.path.isfile(pair_new_path):
                os.rename(pair_old_path, pair_new_path)
                renamed.append((pair_old, pair_new))
    
    logger.info("文件重命名 %s -> %s 配对=%s", old, new, renamed[1:] if len(renamed) > 1 else "无")
    
    # 3. 更新 index.json: 将旧 key 迁移到新 key
    from ingestion.index import _load, _save, remove_entry
    idx = _load()
    files_dict = idx.get("files", {})
    changed = False
    for old_name_in_idx, new_name_in_idx in renamed:
        if old_name_in_idx in files_dict:
            entry = files_dict.pop(old_name_in_idx)
            # 更新 file_url 和 title
            entry["file_url"] = f"/files/{new_name_in_idx}"
            entry["title"] = os.path.splitext(new_name_in_idx)[0]
            files_dict[new_name_in_idx] = entry
            changed = True
    if changed:
        _save(idx)
    
    # 4. 返回更新后的文件列表
    return api_files()


@app.post("/api/delete")
def api_delete(req: DeleteReq) -> list[dict]:
    """删除 downloads/ 下的文件, 可选是否同时从 index.json 移除。
    同名 mp3+mp4 会一起删除 (配对文件)。
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="invalid filename")

    path = os.path.join(DOWNLOAD_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"file not found: {name}")

    deleted = []
    try:
        os.remove(path)
        deleted.append(name)
        logger.info("文件删除 name=%s path=%s", name, path)
    except Exception as e:
        logger.warning("文件删除失败 name=%s error=%s", name, e)
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")

    # 同时删除配对文件 (mp4 <-> mp3)
    base, ext = os.path.splitext(name)
    if ext.lower() == ".mp4":
        pair_name = base + ".mp3"
    elif ext.lower() == ".mp3":
        pair_name = base + ".mp4"
    else:
        pair_name = None
    if pair_name:
        pair_path = os.path.join(DOWNLOAD_DIR, pair_name)
        if os.path.isfile(pair_path):
            try:
                os.remove(pair_path)
                deleted.append(pair_name)
            except Exception:
                pass  # 配对删除失败不影响主操作

    # 从 index.json 移除 (默认保留, 让用户可选)
    if not req.keep_index:
        from ingestion.index import _load, _save
        idx = _load()
        files_dict = idx.get("files", {})
        changed = False
        for n in deleted:
            # 主键 + 该键可能被别名引用 (related_files/aliases)
            if n in files_dict:
                del files_dict[n]
                changed = True
            else:
                # 例如删 mp4 但索引中是 mp3 为主键
                for k in list(files_dict.keys()):
                    entry = files_dict[k]
                    rels = entry.get("related_files") or entry.get("aliases") or []
                    if n in rels:
                        # 也移除该主键 (因为关联文件不存在了)
                        del files_dict[k]
                        changed = True
                        break
        if changed:
            _save(idx)

    return api_files()


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
                    "file_url": f"/files/{name}",
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
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end_str = match.group(2)
            end = int(end_str) if end_str else min(start + (1024 * 1024), file_size - 1)
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

    return FileResponse(path, media_type=content_type)
