# ai-chat-bot

> **改代码前请先读 [LESSONS.md](./LESSONS.md)** —— 里面是这个项目真实踩过的坑（大段替换删函数、空值覆盖配置、假成功、截图当状态断言等）和每次改动前的固定验证流程。读一遍比返工便宜得多。

**目标一句话**：用微信小号做一个"微信里的 AI 分身"——能在群聊/私聊里自主聊天，会识图、会发表情包、会发自己音色的语音，大脑是 DSH。

**当前状态**：M1/M2 已完成并在跑（截至 2026-10-22）。发送走 **UIA 控件树 + 数据库回读确认**（`send_via_uia_verified`），切会话 1.1–1.4s；支持看图（视觉描述）、发表情包（15 个收藏，带标签与喜好度）、休眠唤醒插话。进度与待办见 [ROADMAP.md](./ROADMAP.md)，测试见 [TEST-PLAN.md](./TEST-PLAN.md)。

**开工入口** → [ROADMAP.md](./ROADMAP.md) 的「四、明天从哪继续」：先跑三条自检，再做表情包。

## 发送机制（当前实现）

```json
"send_mode": "auto"      // auto | background | foreground
```

- **auto / background**：都走 **UIA**（发到微信控件树，不碰鼠标键盘）。发之前微信不在前台、发完会自动最小化，不长期占桌面
- **foreground**：老方式（SendInput 模拟点击），已从自动兜底里移除——它会抢用户的鼠标和焦点
- 发送成功与否**以数据库回读为准**，不再出现"返回 True 但没发出去"
- 切会话：先按数据库顺序点侧栏行，再用 UIA 读标题校验；失败才退库的 `open_chat`（慢，8–18s）
- **代价（物理限制）**：UIA 要求窗口可见，所以每次**切换聊天**会把它带到前台 2–4 秒；回同一个会话则完全不动
- 后台路径不支持 @ 某人

## 群聊（M2）怎么开

1. 用**大号**把小号拉进朋友群（群设置 → 加成员）。
2. `.venv\Scripts\python.exe bridge.py --list-groups` 拿到群名。
3. 把群名填进 `config.json` 的 `allow_groups[0].name`，触发词改成小号群昵称。
4. `.venv\Scripts\python.exe bridge.py --live`，然后在群里 **@小号** 试。

## 发图片（已验证）

```powershell
.venv\Scripts\python.exe bridge.py --send-image D:\path\to\pic.png
.venv\Scripts\python.exe bridge.py --send-image pic.png --to 小团体
```

走剪贴板粘贴，微信里显示为**图片消息**（不是文件）。默认发给白名单第一个会话。

**已拍板的降级**（省得反复纠结）：语音只发 mp3 文件、不发语音条；只接 1 个群 + 白名单好友，不自动加人。

**表情包已升级**：现在是发**真收藏贴纸**（走面板独立窗口点击），不再是 GIF 图片降级方案；15 个表情由视觉模型起名，按语义挑选。

**怎么跑**：见下文「快速开始」，或直接 `.venv\Scripts\python.exe bridge.py --live`。

## 从这里读

- [ROADMAP.md](./ROADMAP.md) —— **路线图与产品亮点**：表情包 / 语音 / 人格模组 / 开源，每项的路线、限制、验收。
- [project-manual.md](./project-manual.md) —— 项目手册：目标、能力与路线对照、推荐架构、里程碑、**任务禁忌**、验收标准、待确认项、参考资料。
- [probe.py](./probe.py) —— 只读探针：验微信版本/数据目录/能否解密库/群与好友可读性，**不含任何发送动作**。
- [bridge.py](./bridge.py) —— 主程序：白名单 + 频控 + 熔断 + 上下文 + 记忆库 + 按需检索，dry-run 默认，`--live` 才真发。
- `config.json` —— 白名单与后端配置（含 wxid，已 gitignore，首次运行自动生成模板）。
- [memory.md](./memory.md) —— 记忆库：`## 全局` 常生效，人名节只在跟那人说话时拼进去。
- **给别人部署** → [deploy-ai-chat-bot.md](../../docs/2026-10-20-ai-chat-bot-deploy/deploy-ai-chat-bot.md)：一份可交给对方 DSH 直接执行的完整部署包（含全部源码，已脱敏）。

## 怎么跑（目前只有探针）

```powershell
cd D:\dsh\projects\ai-chat-bot
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install wechatauto-replica
.venv\Scripts\python.exe probe.py --group 你的群名 --friend 好友备注
```

先保证微信 PC 端**已登录并在运行**（主密钥要从进程内存读）。默认打码输出，`--show` 显示原文与完整 wxid。

跑机器人（M1，好友私聊）：

```powershell
# 1. 把 DeepSeek key 填进 .env（文件已建好，等号后面直接贴）
notepad .env

# 2. 先 dry-run 看它打算回什么（不发）
.venv\Scripts\python.exe bridge.py

# 3. 确认没问题再真发
.venv\Scripts\python.exe bridge.py --live
```

## 一句话现状

官方路子（腾讯 iLink / ClawBot）只能私聊、**进不了群**；要"进群 + 自主回复 + 真表情包"必须走个人号自动化，代价是封号风险。所以本项目本质是**在小号上做可控的自动化**，禁忌比功能重要。
