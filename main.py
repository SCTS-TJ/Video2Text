"""Video2Text CLI 入口 (阶段一: 采集层)。

用法:
  python main.py "https://www.youtube.com/watch?v=XXXX"     # 有字幕则直取, 无则下音频
  python main.py "URL" --force-download                     # 强制下载视频
  python main.py "URL" --out result.txt                     # 字幕文本写入文件

退出码: 0 成功, 1 失败。
"""
import argparse
import json
import sys

from ingestion import ingest


def main() -> int:
    ap = argparse.ArgumentParser(description="Video2Text 采集层")
    ap.add_argument("url", help="社媒视频链接")
    ap.add_argument("--force-download", action="store_true", help="强制下载视频(不走字幕捷径)")
    ap.add_argument("--out", help="字幕文本输出文件路径(仅 transcript 通道有效)")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出完整结果")
    args = ap.parse_args()

    result = ingest(args.url, force_download=args.force_download)

    if not result["ok"]:
        print(f"[失败] {result.get('error')}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["channel"] == "transcript":
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(result["text"])
            print(f"[字幕直取] 已写入 {args.out} (来源: {result['source']}, 语言: {result['language']})")
        else:
            print(f"[字幕直取] 来源={result['source']} 语言={result['language']}")
            print("-" * 40)
            print(result["text"])
    else:
        print(f"[下载完成] {result['path']} (mode={result['mode']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
