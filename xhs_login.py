#!/usr/bin/env python3
"""小红书登录 → 导出登录态 cookie（一次性）。

用法:
    uv run --with playwright python3 xhs_login.py

会弹出一个 Chrome 窗口 → 用**手机小红书扫码**或手机号登录 → 脚本检测到登录态后
自动导出 Netscape 格式 cookie 到 cookies_xhs.txt，然后关闭窗口。
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
OUT = BASE / "cookies_xhs.txt"
LOGIN_URL = "https://www.xiaohongshu.com/explore"


def netscape(cookies) -> str:
    lines = ["# Netscape HTTP Cookie File", "# 由 xhs_login.py 生成", ""]
    for c in cookies:
        dom = c.get("domain") or ""
        if not dom:
            continue
        flag = "TRUE" if dom.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires") or 0)
        if exp <= 0:
            exp = int(time.time()) + 3600 * 24 * 30
        lines.append("\t".join([dom, flag, c.get("path") or "/", secure,
                                str(exp), c["name"], c.get("value") or ""]))
    return "\n".join(lines) + "\n"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)
        ctx = browser.new_context(locale="zh-CN")
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        print(">>> 请在刚弹出的 Chrome 窗口里登录小红书（扫码或手机号）", flush=True)

        deadline = time.time() + 300
        ok = False
        while time.time() < deadline:
            names = {c["name"] for c in ctx.cookies()
                     if "xiaohongshu" in (c.get("domain") or "") or "rednote" in (c.get("domain") or "")}
            if "web_session" in names:
                ok = True
                break
            time.sleep(2)

        cookies = [c for c in ctx.cookies()
                   if "xiaohongshu" in (c.get("domain") or "") or "rednote" in (c.get("domain") or "")]
        # 也把 .xiaohongshu.com 的匿名 cookie 一并带上
        browser.close()

    if not ok:
        print(">>> 超时/未检测到登录态(web_session)，未导出。", flush=True)
        return 1
    OUT.write_text(netscape(cookies), encoding="utf-8")
    print(f">>> 已导出 {len(cookies)} 条 cookie -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
