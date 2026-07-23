"""ASR 转写层: Dell 3090 GPU 唯一解码。

方案:
  1. Dell 3090 (large-v3-turbo) → 高精度文本
  2. Mac 用 ffprobe 读音频真实时长 (只读元数据, 毫秒级, 不解码)
  3. Mac 算法估算时间戳 (三级切分)

注意: Dell 返回的 duration 是 GPU 推理耗时, 不是音频时长!
"""
import os
import re
import subprocess

import requests

from .logger import get_logger

logger = get_logger(__name__)

ASR_URL = os.getenv("ASR_URL", "http://192.168.121.99:7860/transcribe")
FFPROBE = "/opt/homebrew/bin/ffprobe"
_NO_PROXY = {"http": None, "https": None}


def _get_audio_duration(audio_path: str) -> float:
    """用 ffprobe 获取音频真实时长 (只读元数据, 不解码音频)。"""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def _estimate_segments(text: str, duration: float) -> list:
    """纯文本按三级切分估算时间戳。

    三级切分策略:
      1. 强标点: 。！？.!?  (自然语义)
      2. 弱标点: ，,、；;   (次级停顿)
      3. 空格 + 60字硬上限 (兜底, 防稀疏音频塌成一段)
    目标: 每段 5-15 秒, 绝不超过 20 秒
    """
    if not text or duration <= 0:
        return []

    total_chars = len(text.replace(" ", ""))
    if total_chars == 0:
        return [{"start": 0.0, "end": duration, "text": text}]

    chars_per_sec = total_chars / duration if duration > 0 else 4.0
    # 动态硬上限: 目标 ~12 秒/段, 下限 15 字, 上限 60 字
    MAX_SEG_CHARS = max(15, min(60, int(chars_per_sec * 12)))

    # 按字符数估算每个 fragment 的时长
    def char_dur(s: str) -> float:
        return len(s.replace(" ", "")) / chars_per_sec if chars_per_sec > 0 else 0

    # ---- 第一级: 按强标点 + 换行切分 ----
    raw_parts = [p.strip() for p in re.split(r"(?<=[。！？.!?\n])\s*", text) if p.strip()]
    if not raw_parts:
        raw_parts = [text.strip()]

    # ---- 第二级 + 第三级: 对超长部分进一步切分 ----
    final_parts = []
    for part in raw_parts:
        if len(part) <= MAX_SEG_CHARS:
            final_parts.append(part)
            continue
        # 先按弱标点切
        sub = re.split(r"(?<=[，,、；;])\s*", part)
        sub = [s.strip() for s in sub if s.strip()]
        # 再对每个 sub 按空格 + 硬上限切
        for s in sub:
            while len(s) > MAX_SEG_CHARS:
                cut = MAX_SEG_CHARS
                # 往回找空格
                sp = s.rfind(" ", 0, MAX_SEG_CHARS)
                if sp > MAX_SEG_CHARS * 0.4:
                    cut = sp
                final_parts.append(s[:cut].strip())
                s = s[cut:].strip()
            if s:
                final_parts.append(s)

    if not final_parts:
        return [{"start": 0.0, "end": duration, "text": text}]

    # ---- 按字符比例分配时长 ----
    total_chars_final = sum(len(p.replace(" ", "")) for p in final_parts) or 1
    segs = []
    buf = ""
    buf_chars = 0
    buf_start = 0.0
    tc = 0.0

    for i, txt in enumerate(final_parts):
        d = duration * len(txt.replace(" ", "")) / total_chars_final
        s_end = tc + d
        if not buf:
            buf_start = tc
        buf += (" " if buf else "") + txt
        buf_chars += len(txt.replace(" ", ""))

        # 切段条件
        is_last = (i == len(final_parts) - 1)
        seg_dur = s_end - buf_start
        # 加下一句会不会超过目标?
        if is_last:
            flush = True
        elif seg_dur >= 10.0:
            flush = True
        elif seg_dur >= 5.0:
            # 看下一句
            nxt = final_parts[i + 1]
            nxt_d = duration * len(nxt.replace(" ", "")) / total_chars_final
            flush = (seg_dur + nxt_d > 12.0)
        else:
            flush = False

        if flush and buf:
            segs.append({
                "start": round(buf_start, 1),
                "end": round(s_end, 1),
                "text": buf.strip(),
            })
            buf = ""
            buf_chars = 0
        tc = s_end

    # 收尾
    if buf:
        segs.append({
            "start": round(buf_start, 1),
            "end": round(duration, 1),
            "text": buf.strip(),
        })

    # 保证最后一段到 duration
    if segs and segs[-1]["end"] < duration:
        segs[-1]["end"] = round(duration, 1)

    return segs


def transcribe(
    audio_path: str,
    model: str = "large-v3-turbo",
    language: str = "zh",
    timeout: int = 1800,
) -> dict:
    """转写音频: Dell 3090 唯一解码 + ffprobe 真实时长 + 三级切分。"""
    if not os.path.isfile(audio_path):
        return {"ok": False, "text": "", "segments": [], "duration": 0, "language": "",
                "error": f"audio not found: {audio_path}"}

    # 本地获取真实音频时长 (ffprobe, 只读元数据, 毫秒级)
    real_duration = _get_audio_duration(audio_path)
    logger.info("ASR请求 audio=%s language=%s duration=%.1fs", os.path.basename(audio_path), language, real_duration)

    try:
        with open(audio_path, "rb") as f:
            r = requests.post(ASR_URL, files={"file": (os.path.basename(audio_path), f)},
                            data={"language": language}, timeout=timeout, proxies=_NO_PROXY)
        if r.status_code != 200:
            logger.warning("ASR HTTP错误 status=%s url=%s", r.status_code, ASR_URL)
            return {"ok": False, "text": "", "segments": [], "duration": real_duration, "language": "",
                    "error": f"Dell ASR http {r.status_code}"}
        dell = r.json()
        if not dell.get("ok"):
            logger.warning("ASR服务返回失败 error=%s", dell.get("error"))
            return {"ok": False, "text": "", "segments": [], "duration": real_duration, "language": "",
                    "error": dell.get("error", "Dell ASR failed")}

        text = dell["text"]
        lang = dell.get("language", language)
        # 用真实时长, 丢弃 Dell 的推理耗时
        dur = real_duration

        logger.info("ASR响应成功 text_len=%d lang=%s dur=%.1fs", len(text), lang, dur)

        # 优先使用 Dell 返回的真实 segments (含 word-level timestamps)
        # 后退: 为兼容老服务端, 仍保留估算分支
        dell_segments = dell.get("segments")
        if dell_segments:
            segments = []
            for s in dell_segments:
                seg_obj = {
                    "start": float(s.get("start", 0)),
                    "end": float(s.get("end", 0)),
                    "text": (s.get("text", "") or "").strip(),
                }
                # 保留 word-level (子子孙项中能用)
                if s.get("words"):
                    seg_obj["words"] = s["words"]
                segments.append(seg_obj)
            logger.info("ASR使用Dell时间戳 segments=%d (含word-level)", len(segments))
        else:
            # 老服务端或错误时才走估算
            segments = _estimate_segments(text, dur)
            logger.info("ASR使用本地估算 segments=%d", len(segments))

        return {
            "ok": True, "text": text, "segments": segments,
            "duration": dur, "language": lang, "error": None,
        }
    except requests.Timeout:
        logger.warning("ASR超时 timeout=%ss url=%s", timeout, ASR_URL)
        return {"ok": False, "text": "", "segments": [], "duration": real_duration, "language": "",
                "error": "Dell ASR timeout"}
    except Exception as e:
        logger.error("ASR异常 %s: %s", type(e).__name__, e, exc_info=True)
        return {"ok": False, "text": "", "segments": [], "duration": real_duration, "language": "",
                "error": f"{type(e).__name__}: {e}"}
