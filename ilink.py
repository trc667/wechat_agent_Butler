# -*- coding: utf-8 -*-
"""微信官方 iLink（ClawBot）客户端。

腾讯 2026 年官方开放的个人微信机器人通道，走 ilinkai.weixin.qq.com：
扫码授权登录（bot_token）-> getupdates 长轮询收消息 -> sendmessage 回消息。

关键规则（踩坑总结）：
- 回复必须原样回传入站消息的 context_token，且用最近一次收到的，不能复用旧的；
- get_updates_buf 是不透明游标，原样保存/回传，不要解析修改；
- 每个用户首次回复前调 getconfig 拿 typing_ticket（缓存 24h），可显示"正在输入"；
- 遇到 ret=-14 表示会话过期，需重新扫码登录。

本模块只管"登录+收发"，AI 人设/记忆在 bot 层（XiaoQiBot）。
"""
import base64
import json
import os
import random
import sys
import time
from collections import deque

import requests

from media import download_image

BASE = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.2"
CRED_FILE = os.path.join("data", "ilink_cred.json")
CURSOR_FILE = os.path.join("data", "ilink_cursor.txt")
TOKENS_FILE = os.path.join("data", "ilink_tokens.json")  # user -> 最近 context_token（主动推送用）

# 微信/DeepSeek 等国内服务走直连：显式禁用代理，避免被本地代理软件
# （魔戒/Clash 设置的系统代理）劫持导致 ProxyError
NO_PROXY = {"http": None, "https": None}


class ILinkError(Exception):
    pass


class ILinkClient:
    def __init__(self, cred_file=CRED_FILE, cursor_file=CURSOR_FILE, tokens_file=TOKENS_FILE):
        self.cred_file = cred_file
        self.cursor_file = cursor_file
        self.tokens_file = tokens_file
        self.token = None       # Bearer 凭证
        self.bot_id = None      # xxx@im.bot
        self.user_id = None     # 绑定的微信用户
        self.base = BASE
        self._context_tokens = {}   # user_id -> 最近一次收到的 context_token
        self._typing_tickets = {}   # user_id -> {"ticket": str, "ts": float}
        self._load_tokens()

    # ---------- 登录 ----------

    def load_cred(self):
        """读取上次登录凭据，成功返回 True。"""
        try:
            with open(self.cred_file, encoding="utf-8") as f:
                cred = json.load(f)
            self.token = cred["bot_token"]
            self.bot_id = cred.get("ilink_bot_id")
            self.user_id = cred.get("ilink_user_id")
            self.base = cred.get("baseurl") or BASE
            return bool(self.token)
        except (OSError, ValueError, KeyError):
            return False

    def save_cred(self):
        os.makedirs(os.path.dirname(self.cred_file), exist_ok=True)
        with open(self.cred_file, "w", encoding="utf-8") as f:
            json.dump({
                "bot_token": self.token,
                "ilink_bot_id": self.bot_id,
                "ilink_user_id": self.user_id,
                "baseurl": self.base,
            }, f, ensure_ascii=False, indent=2)

    def login(self):
        """交互式登录：显示二维码（浏览器链接 + 尽量打印 ASCII 码），轮询扫码状态。"""
        try:
            r = requests.get(self.base + "/ilink/bot/get_bot_qrcode",
                             params={"bot_type": "3"}, timeout=10, proxies=NO_PROXY)
            data = r.json()
        except Exception as e:
            raise ILinkError("请求登录二维码失败: %s" % e)
        qr = str(data.get("qrcode_img_content") or "").strip()
        qrcode_id = str(data.get("qrcode") or "").strip()
        if not qr:
            raise ILinkError("获取二维码失败（返回: %s）" % data)

        print("=" * 52)
        print("  正在绑定你的微信（官方 ClawBot 通道）")
        print("  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        # 二维码可能是链接（浏览器打开）或图片内容（终端/日志直接扫）
        qr_url = data.get("qrcode_url") or (qr if qr.startswith("http") else "")
        if qr_url:
            print("  第 1 步：用【电脑浏览器】打开下面链接，屏幕上显示二维码：")
            print("          " + qr_url)
            print("  第 2 步：用【手机微信】扫屏幕上的码 → 手机上点「确认登录」")
            print("  第 3 步：如果手机上提示安装「微信 ClawBot」插件，点确认")
        else:
            print("  用【手机微信】扫下方二维码；终端显示不出就复制下面原文，")
            print("  用浏览器打开或在线二维码工具解析：")
            print("  " + qr[:300])
            try:
                import qrcode
                q_obj = qrcode.QRCode(border=1)
                q_obj.add_data(qr)
                q_obj.print_ascii(invert=True)
            except Exception:
                print("  （二维码无法在终端显示，用上面的原文解析）")
        print("  等待扫码中…（后台运行模式：这个链接就在 logs/bot.log 里，随时能查）")
        print("=" * 52)

        seen = set()
        while True:
            time.sleep(2)
            try:
                s = requests.get(self.base + "/ilink/bot/get_qrcode_status",
                                 params={"qrcode": qrcode_id}, timeout=10,
                                 proxies=NO_PROXY).json()
            except Exception as e:
                print("[重试] 查询扫码状态失败: %s" % e)
                continue
            status = str(s.get("status") or s.get("qrcode_status") or s.get("ret") or "")
            names = {
                "wait": "等待扫码…",
                "scaned": "已扫码！请在手机上点「确认登录」",
                "confirmed": "确认成功！",
                "expired": "二维码已过期，请重新运行",
                "0": "确认成功！",
            }
            if status in names and status not in seen:
                print("[状态] " + names[status])
                seen.add(status)
            if status in ("confirmed", "0"):
                self.token = s.get("bot_token") or s.get("token")
                self.bot_id = s.get("ilink_bot_id")
                self.user_id = s.get("ilink_user_id")
                self.base = s.get("baseurl") or self.base
                if not self.token:
                    raise ILinkError("扫码确认了但没拿到 token，原始返回: %s" % s)
                self.save_cred()
                print("[+] 登录成功！Bot ID: %s" % self.bot_id)
                return
            if status == "expired":
                raise ILinkError("二维码已过期，请重新运行登录")

    # ---------- 请求 ----------

    def _headers(self):
        uin = str(random.getrandbits(32))
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": "Bearer " + (self.token or ""),
            "X-WECHAT-UIN": base64.b64encode(uin.encode("utf-8")).decode("ascii"),
        }

    def _post(self, path, payload, timeout=30):
        r = requests.post(self.base + path, json=payload,
                          headers=self._headers(), timeout=timeout, proxies=NO_PROXY)
        if r.status_code != 200:
            raise ILinkError("%s HTTP %d: %s" % (path, r.status_code, r.text[:200]))
        try:
            return r.json()
        except ValueError:
            return {}

    # ---------- 收消息（长轮询） ----------

    def _load_tokens(self):
        """恢复上次保存的会话令牌（重启后仍可主动推送）。"""
        try:
            with open(self.tokens_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._context_tokens.update({k: v for k, v in data.items() if v})
        except (OSError, ValueError):
            pass

    def _save_tokens(self):
        """把最近收到的 context_token 落盘（低频，收到消息才写一次）。"""
        try:
            os.makedirs(os.path.dirname(self.tokens_file), exist_ok=True)
            with open(self.tokens_file, "w", encoding="utf-8") as f:
                json.dump(self._context_tokens, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_cursor(self):
        try:
            with open(self.cursor_file, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _save_cursor(self, buf):
        if not buf:
            return
        os.makedirs(os.path.dirname(self.cursor_file), exist_ok=True)
        with open(self.cursor_file, "w", encoding="utf-8") as f:
            f.write(buf)

    def get_updates(self):
        """拉一轮消息。返回 msgs 列表（可能为空）。游标自动持久化。

        ret=-1 常见于游标损坏/服务端不认旧游标（比如 bot 被强杀时写了一半），
        会清空游标重试一次，避免 bot 永久卡死在报错循环。
        """
        payload = {
            "get_updates_buf": self._load_cursor(),
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        data = self._post("/ilink/bot/getupdates", payload, timeout=60)
        ret = data.get("ret", data.get("errcode", 0))
        if ret == -14:
            raise ILinkError("SESSION_EXPIRED")
        if ret == -1 and payload["get_updates_buf"]:
            # 游标异常（实测：损坏/旧游标会让服务端返回 ret=-1）→ 清空重试一次
            print("[日志] getupdates ret=-1，疑似游标异常，清空游标重试…")
            self._save_cursor("")
            payload["get_updates_buf"] = ""
            data = self._post("/ilink/bot/getupdates", payload, timeout=60)
            ret = data.get("ret", data.get("errcode", 0))
            if ret == 0:
                buf = data.get("get_updates_buf") or ""
                self._save_cursor(buf)
                return data.get("msgs") or []
        if ret != 0:
            raise ILinkError("getupdates 失败: %s" % data)
        buf = data.get("get_updates_buf") or ""
        self._save_cursor(buf)
        return data.get("msgs") or []

    def update_loop(self, on_message, on_image=None):
        """长轮询循环。on_message(sender, text) 收到文本时调用；
        on_image(sender, image_bytes) 收到图片时调用（可空）。"""
        while True:
            try:
                msgs = self.get_updates()
            except ILinkError as e:
                if "SESSION_EXPIRED" in str(e):
                    print()
                    print("!" * 52)
                    print("[会话过期] 登录失效，正在重新申请扫码…")
                    print("[提示] 下面链接/二维码在终端和 logs/bot.log 里都有")
                    print("[提示] 手机微信扫一下就恢复；不在电脑前就先放着，回来再扫")
                    print("!" * 52)
                    time.sleep(5)
                    try:
                        self.token = None
                        self.login()
                        self._save_cursor("")  # 新会话清游标
                        continue
                    except Exception as e2:
                        print("[重试失败] %s" % e2)
                        time.sleep(60)
                        continue
                print("[错误] %s" % e)
                time.sleep(2)
                continue
            except Exception as e:
                print("[错误] 网络异常: %s" % e)
                time.sleep(2)
                continue

            seen_msgs = deque(maxlen=50)  # 最近 50 条消息 id：防游标重放导致重复处理
            for m in msgs:
                if m.get("message_type") != 1:   # 1=用户消息，2=机器人
                    continue
                msg_id = m.get("msg_id") or ""
                if msg_id:
                    if msg_id in seen_msgs:
                        continue  # 同一条消息重放，跳过
                    seen_msgs.append(msg_id)
                sender = m.get("from_user_id") or ""
                if not sender:
                    continue
                self._context_tokens[sender] = m.get("context_token") or ""
                self._save_tokens()
                texts = []
                image_done = False  # 一条消息只处理第一张图，避免多 item 重复回复
                for item in m.get("item_list") or []:
                    if item.get("type") == 1:    # 1=文本
                        ti = item.get("text_item") or {}
                        if ti.get("text"):
                            texts.append(ti["text"])
                    elif item.get("type") == 3:  # 3=语音（微信内置 ASR，voice_item.text 即转写）
                        vi = item.get("voice_item") or {}
                        text = (vi.get("text") or "").strip()
                        if text:
                            texts.append(text)
                        else:
                            print("[日志] 收到语音但无转写文本（原样打印 item 便于排查）：")
                            print("  " + json.dumps(item, ensure_ascii=False)[:300])
                    elif item.get("type") == 2:  # 2=图片（CDN AES-128-ECB 加密，需下载解密）
                        if on_image is None or image_done:
                            continue
                        image_done = True
                        img = download_image(item.get("image_item") or {})
                        if img:
                            on_image(sender, img)
                        else:
                            print("[日志] 图片下载/解密失败（原样打印 item 便于排查）：")
                            print("  " + json.dumps(item, ensure_ascii=False)[:400])
                if texts:
                    on_message(sender, "\n".join(texts))

    # ---------- 发消息 ----------

    def _getconfig(self, user_id):
        """拿 typing_ticket（每个用户首次回复前调用一次，缓存 24h）。"""
        now = time.time()
        cached = self._typing_tickets.get(user_id)
        if cached and now - cached["ts"] < 24 * 3600:
            return cached["ticket"]
        payload = {
            "to_user_id": user_id,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        try:
            data = self._post("/ilink/bot/getconfig", payload, timeout=15)
        except Exception:
            return ""
        ticket = (data.get("typing_ticket")
                  or (data.get("data") or {}).get("typing_ticket") or "")
        self._typing_tickets[user_id] = {"ticket": ticket, "ts": now}
        return ticket

    def _sendtyping(self, user_id, status):
        """status: 1=正在输入，2=取消。失败不影响回复。"""
        try:
            ticket = self._getconfig(user_id)
            payload = {
                "to_user_id": user_id,
                "status": status,
                "typing_ticket": ticket,
                "base_info": {"channel_version": CHANNEL_VERSION},
            }
            self._post("/ilink/bot/sendtyping", payload, timeout=15)
        except Exception:
            pass

    def send_text(self, to_user_id, text, timeout=30):
        """回复用户一条文字消息（用最近一次收到的 context_token 关联会话）。"""
        token = self._context_tokens.get(to_user_id)
        if not token:
            print("[警告] 没有 %s 的会话令牌，这次回复发不出去（等对方再发一条就好）" % to_user_id)
            return False
        self._sendtyping(to_user_id, 1)
        client_id = "xiaoqi-" + base64.b64encode(
            str(random.getrandbits(64)).encode("utf-8")).decode("ascii").rstrip("=")
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,      # BOT
                "message_state": 2,     # FINISH
                "context_token": token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        try:
            data = self._post("/ilink/bot/sendmessage", payload, timeout=timeout)
            if data.get("ret", 0) != 0:
                print("[错误] 发送失败: %s" % data)
                return False
            return True
        except Exception as e:
            print("[错误] 发送失败: %s" % e)
            return False
        finally:
            self._sendtyping(to_user_id, 2)


if __name__ == "__main__":
    # 单独测试登录流程
    client = ILinkClient()
    if not client.load_cred():
        client.login()
    else:
        print("[*] 已有凭据: %s" % client.bot_id)
        print("    想重新扫码就删掉 data/ilink_cred.json 再运行")
