"""运行配置: 代理 / 存储路径。从环境变量读取, 无则使用默认值。"""
import os
from dataclasses import dataclass


@dataclass
class Config:
    # 旁路由代理 (iStoreOS), 用于访问被墙站点
    http_proxy: str = "http://192.168.121.44:7890"
    https_proxy: str = "http://192.168.121.44:7890"
    # 下载文件落地目录 (项目内 downloads/)
    download_dir: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads"
    )
    # ffmpeg 二进制 (Mac mini 已装 /opt/homebrew/bin/ffmpeg)
    ffmpeg_location: str = "/opt/homebrew/bin/ffmpeg"
    # yt-dlp 借用本机浏览器登录态, 绕过 YouTube bot 校验
    # 取值: safari | chrome | firefox | edge ; 空字符串 "" 关闭
    ytdlp_cookies_from_browser: str = "chrome"
    # cookies.txt 文件路径(绝对/相对项目根), 优先于上面的 browser 方式
    # 用浏览器插件(如 Get cookies.txt)导出后放项目根, 后台服务无需 keychain 弹窗
    ytdlp_cookies_file: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt"
    )
    # Node.js 运行时路径, 用于 yt-dlp 解决 YouTube JS challenge
    ytdlp_js_runtime: str = "/Users/giannibooth/.hermes/node/bin/node"


_CONFIG: "Config | None" = None


def get_config() -> Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = Config(
            http_proxy=os.getenv("HTTP_PROXY", Config.http_proxy),
            https_proxy=os.getenv("HTTPS_PROXY", Config.https_proxy),
            download_dir=os.getenv("V2T_DOWNLOAD_DIR", Config.download_dir),
            ffmpeg_location=os.getenv("FFMPEG_LOCATION", Config.ffmpeg_location),
        )
        os.makedirs(_CONFIG.download_dir, exist_ok=True)
    return _CONFIG


def proxy_env() -> dict:
    """返回供 subprocess 使用的代理环境变量。"""
    cfg = get_config()
    return {
        "HTTP_PROXY": cfg.http_proxy,
        "HTTPS_PROXY": cfg.https_proxy,
        "http_proxy": cfg.http_proxy,
        "https_proxy": cfg.https_proxy,
    }
