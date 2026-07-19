"""transcripts/index.json 持久索引管理"""
import json, os, time
from pathlib import Path

INDEX_PATH = Path(__file__).parent.parent / "transcripts" / "index.json"

def _load() -> dict:
    if INDEX_PATH.exists():
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"version": 1, "files": {}}

def _save(data: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_entry(
    media_name: str,
    segments: list,
    duration: float,
    title: str,
    file_url: str,
    media_type: str,
    language: str = "?",
    transcript_md: str = "",
) -> None:
    """写入/更新一条索引记录"""
    idx = _load()
    idx["files"][media_name] = {
        "segments": segments,
        "duration": round(duration, 1),
        "title": title,
        "file_url": file_url,
        "media_type": media_type,
        "language": language,
        "transcript_md": transcript_md,
        "transcribed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save(idx)

def get_entry(media_name: str) -> dict | None:
    """按文件名查找索引，无则返回 None"""
    return _load().get("files", {}).get(media_name)

def list_all() -> dict:
    """返回所有已转写的文件"""
    return _load().get("files", {})

def remove_entry(media_name: str) -> bool:
    """删除一条索引"""
    idx = _load()
    if media_name in idx.get("files", {}):
        del idx["files"][media_name]
        _save(idx)
        return True
    return False
