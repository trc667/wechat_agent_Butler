# 小管家 — 个人微信 AI 智能管家

![CI](https://github.com/trc667/wechat_agent_Butler/actions/workflows/ci.yml/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

在**你的个人微信**里随时待命的私人 AI 管家：帮你记备忘、管待办、答技术问题。说一句「记住测试环境地址」，之后随时问「那个地址是什么」它直接答上来。

技术栈：`微信官方 ClawBot（iLink）通道`（2026 年腾讯官方开放的个人微信 Bot 接口，扫码授权、无封号风险）+ `DeepSeek API`（大脑）+ 本地 JSON 长期记忆。

> ⚠️ 本项目仅供个人学习研究使用，请遵守《微信 ClawBot 功能使用条款》与相关法律法规。

---

## ✨ 功能

- **备忘录**：说「记住XXX」秒存（不调模型、零成本）；备忘录常驻 system prompt，问「那个XX是什么/在哪/多少」管家直接检索回答；内容变化重新说一遍即自动更新
- **待办**：说「记一下周三交房租」→ 自动换算日期存入；「我有哪些待办」查看；「完成了交房租」划掉
- **定时提醒**：说「下午3点提醒我开会」「20分钟后提醒我关火」→ 到点微信直推；「我有哪些提醒」「取消3点的提醒」可查可取消
- **Function Calling**：DeepSeek 原生工具协议（无需框架），模型自主调用备忘/待办/天气/新闻/记账等工具；加新功能 = 在 `tools.py` 加一个工具
- **待办到期提醒**：待办到截止日自动推送提醒——**直接发到你的微信**（实测确认 iLink 官方通道支持用最近会话令牌主动推送）
- **每日晨报**：每天早上定时推送「问候 + 今日待办 + 备忘 + 天气」到微信
- **天气查询**：说「今天天气」「上海天气」直接答（wttr.in 免费接口，无需 key）
- **语音消息**：微信内置 ASR 自动转文字，直接对管家发语音也能聊
- **图片识别**：发图让管家「看图说话」（阿里云百炼 qwen-vl-plus，可选；配置 DASHSCOPE_API_KEY 后启用）
- **技术问答**：程序员向的简洁专业回复（先说结论，再给命令/步骤/示例）
- **长期记忆**：每 N 轮对话自动提炼用户事实（结构化 JSON 提取、相似度去重、50 条滚动保留），重启不丢
- **防 AI 腔**：回复物理过滤 emoji / 表情符号 / 波浪号

## 🏗 架构

```
微信用户 ──发消息──▶ iLink 官方通道 ──长轮询──▶ ilink.py
                                                    │  on_message
                                                    ▼
                                               bot.py（XiaoQiBot）
                                                    │
                                        ┌───────────┼───────────────┐
                                        ▼           ▼               ▼
                                   manager.py   memory.py       deepseek.py
                                   （备忘录/待办）（长期记忆）  （DeepSeek API）
                                        │           │               │
                                        ▼           ▼               ▼
                                  data/manager.json  data/memory.json  DeepSeek 大模型
                                                     data/history.jsonl
```

- **回复**：收到消息 → 关键词路由（管家命令先处理）→ 组装人设+记忆+备忘录+上下文 → DeepSeek 生成 → emoji 过滤 → 回发
- **成本控制**：路由纯关键词（0 成本）；只有「周三→日期」等才调用模型（小调用，200 tokens），失败有降级路径
- **稳定性**：回复频率限制防风控、断线自动重连、会话过期（ret=-14）自动重新扫码自愈、`context_token` 会话关联
- **主动提醒（微信直推）**：待办到期 / 每日晨报直接推微信（用最近收到的 context_token 主动发送，已实测可用）

## 🚀 快速开始

### 环境要求

- Python 3.8+，`pip install -r requirements.txt`
- 手机微信最新版，且「我 → 设置 → 插件」里有「微信 ClawBot」（官方灰度推送）
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com)）

### 1. 配置

```bash
cp config.example.json config.json
```

把密钥写入项目根目录的 `.env`（**不入库**，密钥优先级：环境变量 > `.env` > config.json）：

```bash
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

### （可选）调整主动提醒

待办到期提醒和每日晨报**默认自动启用**，无需任何额外配置：

- 发送目标：最近跟管家聊过的人（context_token 持久化在 `data/ilink_tokens.json`）
- 晨报时间：`config.json` → `daily_greeting.time`（默认 08:00）
- 晨报内容：问候 + 今日待办 + 备忘条数 + 天气
- 天气默认城市：`config.json` → `weather_city`（默认北京）

### （可选）开启图片识别

微信发图 → 管家自动识图回复描述（用阿里云百炼 qwen-vl-plus，DeepSeek 无原生视觉）：

1. 阿里云控制台 → 百炼（Model Studio）→ API-KEY 管理，拿 API Key
2. 填入 `.env`：
   ```bash
   DASHSCOPE_API_KEY=sk-xxx
   DASHSCOPE_MODEL=qwen-vl-plus
   ```
3. 重启生效。不配置则发图只提示「没看清」（不报错）

> 图片传输走微信 CDN（AES-128-ECB 加密），实现对齐腾讯官方 openclaw-weixin。

### 2. 登录并启动

```bash
python -u ilink_bot.py
```

首次运行会打印登录二维码链接：
1. 电脑浏览器打开链接 → 屏幕显示二维码
2. 手机微信扫码 → 手机上点「确认登录」→ 确认安装插件
3. 登录成功生成 `data/ilink_cred.json`，之后启动自动连接，免扫码

看到「小管家已上线」即成功，终端窗口会实时显示对话：

```
[21:35:12] 你：在干嘛呢
[21:35:12] 小管家：正在思考…
[21:35:22] 小管家：刚开完会，在写代码（想了 10 秒）
```

> 会话过期（提示重新扫码）：删掉 `data/ilink_cred.json` 重新运行即可。

## 💬 使用示例（微信里直接发）

| 你说 | 管家做 |
|---|---|
| 记住测试环境地址 http://10.10.0.8:8080 | 存入备忘录 |
| 那个测试环境的地址是什么 | 从备忘录检索直接回答 |
| 备忘录 / 我记过什么 | 列出所有备忘 |
| 忘掉测试环境地址 | 删除该备忘 |
| 记一下周三交房租 | 换算日期存入待办 |
| 我有哪些待办 | 列出未完成待办 |
| 完成了交房租 | 划掉该待办 |
| （直接发语音） | 自动转文字并回复 |
| 发一张图片 | 自动识图描述内容（需配置百炼 key） |
| 今天天气 / 上海天气 | 查天气直接答（默认城市可在 config 改） |
| 待办到期 / 每日晨报 | 自动推送到你的微信 |

## 🧪 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -v          # 58 个用例：路由/记忆/配置/过滤
python bot.py --dry-run      # 模拟全链路（不连微信）
python manager.py            # 管家模块离线自测
```

CI 已配置 GitHub Actions，push/PR 自动跑测试（Python 3.8 / 3.11 双版本）。

## 📁 项目结构

```
├── ilink_bot.py        # 主入口：python -u ilink_bot.py
├── ilink.py            # 微信官方 iLink 客户端（扫码登录/长轮询收消息/回复）
├── bot.py              # 核心：人设+记忆+回复调度+emoji 过滤
├── manager.py          # 备忘录+待办（关键词路由，数据 data/manager.json）
├── reminder.py         # 主动提醒：待办到期 + 每日晨报（企微 App 推送）
├── persona.py          # 管家系统提示词
├── memory.py           # 长期记忆（memory.json）+ 聊天流水（history.jsonl）+ 记忆提炼
├── deepseek.py         # DeepSeek API 封装（指数退避重试）
├── weather.py          # 天气查询（wttr.in 免费接口，无需 key）
├── vision.py           # 多模态识图（阿里云百炼 qwen-vl-plus）
├── media.py            # 微信媒体下载解密（CDN AES-128-ECB）
├── config.py           # 配置加载（config.json + .env）
├── tests/              # pytest 单元测试
├── config.example.json # 配置模板（真实配置用 config.json，不入库）
├── wecom*.py           # 企业微信方案遗留（未使用，备查）
└── data/               # 记忆/聊天记录/登录凭据（自动生成，不入库）
```

## ⚠️ 已知限制

- **主动推送依赖最近会话**：提醒用最近收到的 context_token 发送，若长时间没聊过可能失效（重新发一条消息即恢复）；腾讯官方保留对该能力的调整权利
- 只能私聊，不能拉群；图片/文件暂不处理（官方支持，可扩展）
- 单用户绑定：官方通道绑定一个微信账号
- Windows 下验证充分；其他平台未测试（控制台编码、守护脚本为 Windows 专属）

## 📄 许可证

[MIT](LICENSE)
