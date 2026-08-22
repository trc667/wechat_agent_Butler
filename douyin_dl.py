# -*- coding: utf-8 -*-
"""抖音视频下载：Playwright 真实浏览器内核打开视频页 → 抖音 JS 自行生成签名
→ 拦截网络请求拿到真实 mp4 地址 → requests 下载（带浏览器 cookie + Referer）。

为什么用 Playwright：抖音 2026 年网页端要求浏览器 JS 动态生成 __ac_signature
签名（裸请求/yt-dlp 均无法伪造），真实浏览器内核能天然通过验证。

用法：
    path = download_douyin_video("https://v.douyin.com/xxx/", out_dir)
    # 返回本地 mp4 路径；失败返回 None
"""

import os
import re
import time

import requests

# 国内服务走直连，显式禁用系统代理
_NO_PROXY = {"http": None, "https": None}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# 视频地址特征（抖音 CDN / 播放地址）
_VIDEO_HINTS = (".mp4", "playwm", "video/tos", "aweme/v1/play", "douyinvod")

_PAGE_TIMEOUT = 35000   # 打开页面超时
_GRAB_TIMEOUT = 30      # 等视频请求出现的最长时间（秒）
_DL_TIMEOUT = 120       # 下载超时

# 登录态文件（扫码登录后保存，之后下载自动复用）
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "douyin_state.json")

# 登录成功后 cookie 里出现的字段（sessionid 是抖音登录标志）
_LOGIN_COOKIE_KEYS = ("sessionid", "sid_guard", "uid_tt",
                      "passport_auth_status")


def _load_state():
    """返回已保存的登录态路径（存在且非空才用），否则 None。"""
    try:
        if os.path.isfile(STATE_FILE) and os.path.getsize(STATE_FILE) > 50:
            return STATE_FILE
    except OSError:
        pass
    return None


def _launch_browser(headless=True, state=None):
    """启动 Chromium（可选加载登录态），返回 (playwright, browser, ctx, page)。"""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = p.chromium.launch(
        headless=headless,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    kw = {}
    if state:
        kw["storage_state"] = state
    ctx = browser.new_context(
        user_agent=_UA, viewport={"width": 1280, "height": 800},
        locale="zh-CN", **kw)
    page = ctx.new_page()
    return p, browser, ctx, page


def login_douyin(on_qrcode=None, timeout=180, state_file=STATE_FILE):
    """扫码登录抖音网页版：打开登录弹窗 → 二维码截图回调（发微信）→
    等扫码 → 保存登录态。

    必须用有头模式（headless 下抖音风控不弹登录窗）。
    on_qrcode(png_bytes)：二维码图片回调。
    返回 True（登录成功）/ False（超时/失败）。
    """
    from playwright.sync_api import sync_playwright
    print("[douyin] 启动登录流程（会弹出浏览器窗口）...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=_UA,
                                  viewport={"width": 1280, "height": 800},
                                  locale="zh-CN")
        page = ctx.new_page()
        try:
            page.goto("https://www.douyin.com/", timeout=_PAGE_TIMEOUT,
                      wait_until="domcontentloaded")
        except Exception:
            pass
        try:
            page.get_by_text("登录", exact=True).first.click(timeout=8000)
        except Exception:
            pass
        deadline = time.time() + timeout
        last_shot = 0.0
        while time.time() < deadline:
            # 登录成功判断：cookie 出现 sessionid 等登录字段
            names = {c["name"] for c in ctx.cookies()}
            if any(k in names for k in _LOGIN_COOKIE_KEYS):
                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                ctx.storage_state(path=state_file)
                print("[douyin] 扫码登录成功，登录态已保存")
                browser.close()
                return True
            # 每 12 秒发一次最新二维码截图（二维码会过期刷新）
            if time.time() - last_shot > 12 and on_qrcode:
                try:
                    shot = page.screenshot(type="png")
                    on_qrcode(shot)
                    last_shot = time.time()
                except Exception as e:
                    print("[douyin] 截图失败: %s" % e)
            page.wait_for_timeout(1000)
        print("[douyin] 扫码登录超时")
        browser.close()
        return False


def _looks_video(url):
    u = url.lower()
    return any(h in u for h in _VIDEO_HINTS)


def download_douyin_video(url, out_dir, headless=True, state=None):
    """Playwright 打开抖音视频页 → 拦截真实 mp4 → 下载到 out_dir。
    返回文件路径；失败返回 None。headless=False 时用有头模式（防检测）。

    页面会同时加载多个视频流：优先选带音频的（playwm 带水印但有声音，
    纯 hevc 流是无声画面），否则转写会没有内容。
    state：登录态文件路径（扫码登录后保存），可显著降低风控。"""
    if state is None:
        state = _load_state()
    candidates = []  # 收集所有视频 URL，按优先级排序

    try:
        p, browser, ctx, page = _launch_browser(headless=headless, state=state)
        try:
            def on_response(resp):
                ct = (resp.headers.get("content-type") or "").lower()
                if ct.startswith("video") or _looks_video(resp.url):
                    if resp.url not in candidates:
                        candidates.append(resp.url)

            page.on("response", on_response)
            try:
                page.goto(url, timeout=_PAGE_TIMEOUT, wait_until="domcontentloaded")
            except Exception:
                pass  # 页面可能跳转/超时，继续等视频请求

            # 页面可能弹验证码（风控）：有验证码就放弃
            try:
                if page.locator("iframe[src*=captcha], [class*=captcha], [id*=captcha]").count() > 0:
                    print("[douyin_dl] 触发风控验证码，放弃")
                    browser.close()
                    return None
            except Exception:
                pass

            # 等视频请求出现（页面自动播放会触发视频资源加载）
            deadline = time.time() + _GRAB_TIMEOUT
            while time.time() < deadline and not candidates:
                # 每轮尝试触发播放：滚动 + 调用 video.play()（抖音懒加载/需交互才播）
                try:
                    page.mouse.wheel(0, 600)
                    page.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) { v.muted = true; v.play().catch(() => {}); }
                    }""")
                except Exception:
                    pass
                page.wait_for_timeout(800)

            # 备选：从 performance 资源列表找视频地址
            if not candidates:
                try:
                    found = page.evaluate("""() => {
                        const items = performance.getEntriesByType('resource').map(e => e.name);
                        return items.filter(u => {
                            const l = u.toLowerCase();
                            return l.includes('.mp4') || l.includes('playwm')
                                || l.includes('video/tos') || l.includes('douyinvod');
                        });
                    }""")
                    if found:
                        candidates.extend(found)
                except Exception:
                    pass

            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        finally:
            browser.close()
            p.stop()
    except Exception as e:
        print("[douyin_dl] 浏览器启动/抓取失败: %s" % e)
        return None

    if not candidates:
        print("[douyin_dl] 未抓到视频地址")
        return None

    # 优先级：playwm（带水印+音频）> 其他 mp4（可能无声）；去重保序
    def priority(u):
        u = u.lower()
        if "playwm" in u:
            return 0
        if ".mp4" in u:
            return 1
        return 2

    candidates.sort(key=priority)
    # 优先尝试第一个（音频优先）；带 cookie + Referer 防盗链
    headers = {
        "User-Agent": _UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    }
    for video_url in candidates[:3]:
        try:
            r = requests.get(video_url, headers=headers, cookies=cookies,
                             timeout=_DL_TIMEOUT, proxies=_NO_PROXY, stream=True)
            r.raise_for_status()
            path = os.path.join(out_dir, "douyin_%d.mp4" % int(time.time()))
            with open(path, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
            if os.path.getsize(path) < 1024:  # 防空/损坏文件
                os.remove(path)
                continue
            return path
        except Exception as e:
            print("[douyin_dl] 下载失败(尝试下一个): %s" % e)
            continue
    return None
