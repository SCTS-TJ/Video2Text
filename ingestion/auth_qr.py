"""网页内二维码登录（服务器端无头浏览器）。

设计: app 跑在无桌面的 Dell 上, 不能弹本地浏览器。因此由**服务器**用 Playwright
拉起无头 Chromium, 打开登录页 → 把**二维码图片**交给前端显示 → 用户用手机扫码 →
服务器检测到登录态后自动保存 cookie。用户侧零脚本、零安装。

当前支持: 小红书 (xiaohongshu)。后续可扩展 元宝(视频号) 等。
"""
import base64
import os
import threading
import time
import uuid

from .logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# 每个 provider: 登录页 / 二维码选择器 / cookie 归属域 / 落盘与合并目标
PROVIDERS = {
    "xiaohongshu": {
        "label": "小红书",
        "login_url": "https://www.xiaohongshu.com/login",
        "qr_selector": "img.qrcode-img",
        "probe_url": "https://www.xiaohongshu.com/explore",
        "cookie_domains": ("xiaohongshu.com", "rednote.com"),
        "cookies_out": os.path.join(BASE_DIR, "cookies_xhs.txt"),
        "merge_into": os.path.join(BASE_DIR, "cookies.txt"),
    },
}

_SESSIONS: dict = {}
_LOCK = threading.Lock()
_TTL = 420  # 会话最长存活(秒)


def _set(sid: str, **kw) -> None:
    with _LOCK:
        if sid in _SESSIONS:
            _SESSIONS[sid].update(kw)
            _SESSIONS[sid]["ts"] = time.time()


def _gc() -> None:
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _SESSIONS.items() if now - v.get("ts", 0) > _TTL]:
            _SESSIONS.pop(k, None)


def list_providers() -> list:
    return [{"key": k, "label": v["label"]} for k, v in PROVIDERS.items()]


def start(provider: str) -> dict:
    """启动一次扫码登录会话, 返回 {sid, provider, label}。"""
    _gc()
    if provider not in PROVIDERS:
        return {"error": f"unknown provider: {provider}"}
    sid = uuid.uuid4().hex[:12]
    with _LOCK:
        _SESSIONS[sid] = {"provider": provider, "state": "starting",
                          "detail": "正在启动浏览器...", "qr": "", "ts": time.time()}
    threading.Thread(target=_worker, args=(sid, provider), daemon=True).start()
    return {"sid": sid, "provider": provider, "label": PROVIDERS[provider]["label"]}


def status(sid: str) -> dict:
    _gc()
    with _LOCK:
        s = _SESSIONS.get(sid)
        if not s:
            return {"state": "gone", "detail": "会话不存在或已过期"}
        return {"state": s["state"], "detail": s.get("detail", ""),
                "qr": s.get("qr", ""), "provider": s["provider"],
                "label": PROVIDERS.get(s["provider"], {}).get("label", "")}


def _grab_qr(page, selector: str) -> str:
    """抓二维码为 data: URL。"""
    try:
        el = page.query_selector(selector)
        if el:
            src = el.get_attribute("src") or ""
            if src.startswith("data:image"):
                return src
            if src.startswith("http"):
                r = page.request.get(src, timeout=15000)
                return "data:image/png;base64," + base64.b64encode(r.body()).decode()
            return "data:image/png;base64," + base64.b64encode(el.screenshot()).decode()
    except Exception:
        pass
    return ""


def _is_logged_in(ctx, probe_url: str) -> bool:
    """用当前上下文请求须登录才可见的页面: 不再 302 到 /login 即视为已登录。"""
    try:
        r = ctx.request.get(probe_url, max_redirects=0, timeout=15000)
        if r.status in (301, 302, 303, 307, 308):
            return "/login" not in (r.headers.get("location", "") or "")
        return r.status == 200
    except Exception:
        return False


def _netscape(cookies: list) -> str:
    lines = ["# Netscape HTTP Cookie File", "# 由 Video2Text 授权登录生成", ""]
    for c in cookies:
        dom = c.get("domain") or ""
        if not dom:
            continue
        flag = "TRUE" if dom.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires") or 0)
        if exp <= 0:
            exp = int(time.time()) + 86400 * 30
        lines.append("\t".join([dom, flag, c.get("path") or "/", secure,
                                str(exp), c["name"], c.get("value") or ""]))
    return "\n".join(lines) + "\n"


def _save_cookies(prov: dict, cookies: list) -> None:
    """写出 provider 专属 cookie 文件, 并合并进主 cookies.txt(只换掉该站旧条目)。"""
    block = _netscape(cookies)
    try:
        with open(prov["cookies_out"], "w", encoding="utf-8") as f:
            f.write(block)
    except OSError as e:
        logger.warning("写 %s 失败: %s", prov["cookies_out"], e)

    main = prov.get("merge_into")
    if not main:
        return
    doms = tuple(prov["cookie_domains"])
    keep = []
    try:
        if os.path.isfile(main):
            with open(main, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f.read().splitlines():
                    if ln.startswith("#") or not ln.strip():
                        keep.append(ln)
                        continue
                    dom = ln.split("\t")[0].lstrip(".")
                    if any(dom.endswith(d) for d in doms):
                        continue  # 丢弃该站旧 cookie
                    keep.append(ln)
        new = keep + [l for l in block.splitlines() if not l.startswith("#") and l.strip()]
        with open(main, "w", encoding="utf-8") as f:
            f.write("\n".join(new) + "\n")
        logger.info("已合并 %d 条 %s cookie 到 %s", len(cookies), prov["label"], main)
    except OSError as e:
        logger.warning("合并 cookies 失败: %s", e)


def _worker(sid: str, provider: str) -> None:
    prov = PROVIDERS[provider]
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,
                                         args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(locale="zh-CN", user_agent=_UA,
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.goto(prov["login_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)

            qr = _grab_qr(page, prov["qr_selector"])
            if not qr:
                _set(sid, state="error", detail="未能获取二维码(页面结构可能已变)")
                return
            _set(sid, state="waiting", detail="请用手机 App 扫码登录", qr=qr)
            logger.info("授权登录[%s] 二维码就绪 sid=%s", prov["label"], sid)

            deadline = time.time() + _TTL
            while time.time() < deadline:
                time.sleep(3)
                if _is_logged_in(ctx, prov["probe_url"]):
                    cookies = [c for c in ctx.cookies()
                               if any((c.get("domain") or "").lstrip(".").endswith(d)
                                      for d in prov["cookie_domains"])]
                    _save_cookies(prov, cookies)
                    _set(sid, state="ok", qr="",
                         detail=f"✅ 已登录并保存 {len(cookies)} 条 cookie")
                    logger.info("授权登录[%s] 成功 sid=%s cookies=%d", prov["label"], sid, len(cookies))
                    return
                try:  # 二维码可能自动刷新
                    nq = _grab_qr(page, prov["qr_selector"])
                    if nq and nq != qr:
                        qr = nq
                        _set(sid, qr=qr)
                except Exception:
                    pass
            _set(sid, state="timeout", detail="超时未完成扫码, 请重试")
    except Exception as e:  # noqa: BLE001
        logger.error("授权登录[%s] 异常 sid=%s: %s", provider, sid, e, exc_info=True)
        _set(sid, state="error", detail=f"{type(e).__name__}: {e}")
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass

