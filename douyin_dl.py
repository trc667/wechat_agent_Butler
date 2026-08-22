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


def _fetch_detail_via_page(page, aweme_id):
    """在页面上下文里 fetch 抖音 detail API，拿真实视频地址。

    关键：必须在页面内 fetch（带上登录态 cookie + 页面 JS 签名环境），
    参数需模拟页面真实请求（带 version_code/webid 等），否则易被 WAF 拦截
    （实测返回 "Blocked by..."）。返回 (play_url, play_url_h265) 或 (None, None)。
    """
    if not aweme_id:
        return None, None
    try:
        result = page.evaluate("""async (id) => {
            const ttwid = (document.cookie.match(/ttwid=([^;]+)/) || [])[1] || '';
            const url = 'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=' + id
                + '&device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1'
                + '&version_code=190500&version_name=19.5.0&cookie_enabled=true'
                + '&screen_width=1280&screen_height=800&browser_language=zh-CN'
                + '&browser_platform=Win32&browser_name=Chrome&browser_version=130.0.0.0'
                + '&browser_online=true&engine_name=Blink&os_name=Windows&os_version=10'
                + '&cpu_core_num=8&device_memory=8&platform=PC&downlink=10&effective_type=4g'
                + '&round_trip_time=50&webid=' + ttwid;
            try {
                const r = await fetch(url, {
                    credentials: 'include',
                    headers: {'referer': location.href},
                });
                const text = await r.text();
                if (!text.startsWith('{')) return {error: 'blocked: ' + text.slice(0, 60)};
                const j = JSON.parse(text);
                if (j.status_code !== 0) return {error: 'status_code=' + j.status_code};
                const v = (j.aweme_detail || {}).video || {};
                const pick = (o) => ((o || {}).url_list || [])[0] || '';
                return {
                    play: pick(v.play_addr || v.play_addr_h264),
                    play265: pick(v.play_addr_265),
                };
            } catch (e) { return {error: String(e).slice(0, 120)}; }
        }""", aweme_id)
        if not result or result.get("error"):
            print("[douyin_dl] detail API 失败: %s" % (result or {}).get("error", ""))
            return None, None
        return result.get("play") or "", result.get("play265") or ""
    except Exception as e:
        print("[douyin_dl] detail API 异常: %s" % e)
        return None, None


def _download_from(url, headers, cookies, out_dir, tag=""):
    """下载视频到 out_dir，返回路径；失败返回 None。"""
    if not url:
        return None
    try:
        r = requests.get(url, headers=headers, cookies=cookies,
                         timeout=_DL_TIMEOUT, proxies=_NO_PROXY, stream=True)
        r.raise_for_status()
        path = os.path.join(out_dir, "douyin_%s%d.mp4" % (tag, int(time.time())))
        with open(path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        if os.path.getsize(path) < 1024:  # 防空/损坏文件
            os.remove(path)
            return None
        return path
    except Exception as e:
        print("[douyin_dl] 下载失败(%s): %s" % (tag or url[:50], e))
        return None


def download_douyin_video(url, out_dir, headless=True, state=None):
    """Playwright 打开抖音视频页 → 真实视频地址 → 下载到 out_dir。
    返回文件路径；失败返回 None。headless=False 时用有头模式（防检测）。

    主路径：页面内 fetch detail API 拿 play_addr（带音频无水印），
    失败回退 play_addr_265（可能无声）。再回退网络请求拦截（防占位视频干扰）。
    state：登录态文件路径（扫码登录后保存），可显著降低风控。"""
    if state is None:
        state = _load_state()
    candidates = []  # 兜底：拦截到的视频 URL
    headers = {"User-Agent": _UA, "Referer": "https://www.douyin.com/",
               "Accept": "*/*"}

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
                pass  # 页面可能跳转/超时，继续等

            # 页面可能弹验证码（风控）：有验证码就放弃
            try:
                if page.locator("iframe[src*=captcha], [class*=captcha], [id*=captcha]").count() > 0:
                    print("[douyin_dl] 触发风控验证码，放弃")
                    return None
            except Exception:
                pass

            cookies = {c["name"]: c["value"] for c in ctx.cookies()}

            # 主路径：从最终 URL 提取 aweme_id → detail API 拿真实地址 → 下载
            import re as _re
            m = _re.search(r"/video/(\d+)", page.url)
            if m:
                play, play265 = _fetch_detail_via_page(page, m.group(1))
                if play:
                    path = _download_from(play, headers, cookies, out_dir, tag="detail_")
                    if path:
                        return path
                if play265:
                    path = _download_from(play265, headers, cookies, out_dir, tag="detail265_")
                    if path:
                        return path

            # 兜底：等视频请求 + performance 资源（排除 uuu_265 占位视频）
            deadline = time.time() + _GRAB_TIMEOUT
            while time.time() < deadline and not candidates:
                try:
                    page.mouse.wheel(0, 600)
                    page.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) { v.muted = true; v.play().catch(() => {}); }
                    }""")
                except Exception:
                    pass
                page.wait_for_timeout(800)
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
        finally:
            browser.close()
            p.stop()
    except Exception as e:
        print("[douyin_dl] 浏览器启动/抓取失败: %s" % e)
        return None

    if not candidates:
        print("[douyin_dl] 未抓到视频地址")
        return None

    # 兜底下载：过滤掉抖音占位视频（uuu_265.mp4 是页面加载的通用资源，非用户视频）
    def is_placeholder(u):
        return "uuu_265" in u or "placeholder" in u.lower()

    def priority(u):
        u = u.lower()
        if "playwm" in u:
            return 0
        if ".mp4" in u:
            return 1
        return 2

    for video_url in sorted((u for u in candidates if not is_placeholder(u)),
                            key=priority)[:3]:
        path = _download_from(video_url, headers, cookies, out_dir, tag="grab_")
        if path:
            return path
    return None
