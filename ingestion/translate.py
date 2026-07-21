"""\u4e2d\u82f1\u5bf9\u7167\u7ffb\u8bd1\u670d\u52a1: \u4f7f\u7528 Google Translate \u514d\u8d39 endpoint (\u65e0\u9700 key)\u3002

\u4e3a\u4e86\u907f\u514d\u8bf7\u6c42\u9891\u7e41\u88ab\u9650\u6d41, \u5c06\u591a\u4e2a segment \u5408\u5e76\u4e3a\u4e00\u4e2a\u8bf7\u6c42 (\u6279\u91cf\u7ffb\u8bd1)\u3002
\u4e3a\u4e86\u4fdd\u8bc1\u7ffb\u8bd1\u4e0e\u539f\u6587\u5bf9\u9f50, \u91c7\u7528\u53e5\u53e5\u7ffb\u8bd1 (\u4fdd\u7559\u539f\u8bed\u5e8f\u4e0e\u957f\u5ea6\u4e0d\u53d8)\u3002
"""
import os
import time
import re
from deep_translator import GoogleTranslator

# \u8bed\u8a00\u8bc6\u522b\u6620\u5c04: \u82f1\u6587 -> \u4e2d\u6587
LANG_DISPLAY = {
    "en": "\u82f1\u6587",
    "zh": "\u4e2d\u6587",
    "ja": "\u65e5\u6587",
    "ko": "\u97e9\u6587",
    "fr": "\u6cd5\u6587",
    "de": "\u5fb7\u6587",
    "es": "\u897f\u6587",
    "ru": "\u4fc4\u6587",
    "auto": "\u81ea\u52a8\u68c0\u6d4b",
}

# \u662f\u5426\u542f\u7528\u7ffb\u8bd1: \u4ece env \u8bfb (\u9ed8\u8ba4 True)
TRANSLATE_ENABLED = os.getenv("TRANSLATE_ENABLED", "true").lower() in ("1", "true", "yes")


def _clean_text_for_translate(text: str) -> str:
    """\u53bb\u6389\u5e26\u7740\u7684 [\u6700\u540e\u4e00\u4e2a\u8bcd] \u6807\u8bb0, \u907f\u514d\u7ffb\u8bd1\u51fa\u9519"""
    return text.strip()


def translate_segments(segments: list, target_lang: str = "zh-CN") -> list:
    """\u6279\u91cf\u7ffb\u8bd1 segments\u3002

    \u8f93\u5165: [{text: "Hello", ...}, ...]
    \u8f93\u51fa: \u540c\u6837\u957f\u5ea6\u7684\u6570\u7ec4, \u6bcf\u4e2a\u5143\u7d20\u589e\u52a0 translation \u5b57\u6bb5
    """
    if not segments or not TRANSLATE_ENABLED:
        return segments

    translator = GoogleTranslator(source="auto", target=target_lang)

    # \u4e3a\u4e86\u63d0\u9ad8\u6548\u7387 + \u51cf\u5c11\u8bf7\u6c42\u6b21\u6570, \u5408\u5e76\u591a\u4e2a segment \u4e3a\u4e00\u4e2a\u8bf7\u6c42
    # \u4f7f\u7528\u4e0a\u4e0b\u6587\u4f5c\u4e3a\u9694\u7b26, \u8fd9\u6837\u7ffb\u8bd1\u80fd\u4fdd\u6301\u4e0a\u4e0b\u6587\u4e00\u81f4\u6027
    texts = [_clean_text_for_translate(s.get("text", "")) for s in segments]
    if not any(texts):
        return segments

    # \u5408\u5e76: \u4e00\u6b21\u6700\u5911 2000 \u5b57\u7b26 (\u8d85\u8fc7\u4f1a\u88ab\u9650\u6d41)
    BATCH = 30
    results = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        joined = "\n[BREAK]\n".join(batch)
        try:
            translated_joined = translator.translate(joined)
            if translated_joined is None:
                translated_joined = ""
            batch_results = translated_joined.split("\n[BREAK]\n")
            # \u9632\u5fa1: \u4e0d\u8db3\u65f6\u586b\u7a7a
            while len(batch_results) < len(batch):
                batch_results.append("")
            results.extend(batch_results)
        except Exception as e:
            # \u67d0\u6279\u5931\u8d25\u4e0d\u5f71\u54cd\u6574\u4f53, \u586b\u7a7a\u7ffb\u8bd1
            print(f"[translate] batch {i} failed: {e}")
            results.extend([""] * len(batch))
        time.sleep(0.3)  # \u8c\u9047\u8bf7\u6c42\u9650\u6d41\u4fdd\u62a4

    # \u5199\u56de segments
    for s, t in zip(segments, results):
        s["translation"] = t.strip() if t else ""
    return segments
