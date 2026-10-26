#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bridge.py —— ai-chat-bot M1：好友私聊闭环（收 → 判断 → 生成 → 发）

安全设计（默认最保守）：
    * 默认 **dry-run**：只打印"本应发什么"，绝不真的发。
    * 只处理 config.json 白名单里的好友；其他人一律忽略。
    * 只处理**文本**消息，且只处理"对方发来的"（跳过自己发的）。
    * 单好友频控 + 连轮熔断 + 夜间延迟。
    * 首次运行不回补历史：水位从当前最新消息开始（避免上线瞬间把积压全回一遍）。

用法：
    python bridge.py --once --dry-run        # 单次轮询，打印将要回什么（不发）
    python bridge.py --once --dry-run --from-beginning   # 从最早消息开始回放，验证过滤逻辑
    python bridge.py                          # 常驻，仍然 dry-run
    python bridge.py --live                   # 真的发（必须先确认！）

退出码：0 正常；1 配置/数据库错误；130 中断。
"""

from __future__ import annotations

import argparse
import ctypes
import html
import atexit
import io
import json
import os
import random
import re
import sys
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

def _app_root() -> str:
    """数据目录：打包后是 exe 所在目录，源码运行时是本项目目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


HERE = _app_root()
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
LOCK_PATH = os.path.join(HERE, "bridge.lock")
ENV_PATH = os.path.join(HERE, ".env")


def _proc_create_time(pid: int) -> Optional[float]:
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:
        return None


def acquire_lock() -> bool:
    """单实例锁：两个 bridge 同时跑会互相覆盖 state.json，消息就乱了。

    只用 pid 判活不可靠 —— Windows 会回收 pid，旧锁会挡住新进程。
    所以锁里同时记进程创建时间，两个都对上才认定"真有人在跑"。
    """
    if os.path.exists(LOCK_PATH):
        pid, ct = 0, None
        try:
            with open(LOCK_PATH, "r", encoding="utf-8-sig") as f:
                raw = (f.read() or "").strip()
            try:
                info = json.loads(raw)
                pid = int(info.get("pid") or 0)
                ct = info.get("ct")
            except Exception:
                pid = int(raw or "0")      # 兼容旧格式：纯 pid
        except Exception:
            pid, ct = 0, None

        if pid:
            alive = _proc_create_time(pid) is not None
            same = True
            if alive and ct:
                now_ct = _proc_create_time(pid)
                same = bool(now_ct) and abs(float(now_ct) - float(ct)) < 5.0
            if alive and same:
                log("已经有一个 bridge 在跑（PID %d）。同时开两个会互相覆盖水位，先关掉那个。" % pid)
                return False
            if alive and not same:
                log("锁里的 PID %d 已被系统复用（不是 bridge），接管旧锁。" % pid)

    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "ct": _proc_create_time(os.getpid()),
                   "at": datetime.now().isoformat(timespec="seconds")}, f)
    return True


def release_lock() -> None:
    try:
        with open(LOCK_PATH, "r", encoding="utf-8-sig") as f:
            raw = (f.read() or "").strip()
        try:
            pid = int(json.loads(raw).get("pid") or 0)
        except Exception:
            pid = int(raw or "0")
        if pid == os.getpid():
            os.remove(LOCK_PATH)
    except Exception:
        pass


HISTORY_PATH = os.path.join(HERE, "history.json")


class History:
    """按会话保存最近若干条消息，作为模型的上下文窗口。

    为什么用"条数 + 总字数"双限：条数防跑偏，字数防账单。
    群聊窗口存的是所有人的发言，包括没 @ 它的那些 —— 这才是"在场感"的来源。
    """

    def __init__(self, cfg: Dict[str, Any]):
        c = cfg.get("context") or {}
        self.enabled = bool(c.get("enabled", True))
        self.max_chars = int(c.get("max_chars") or 1200)
        self.friend_turns = int(c.get("friend_max_turns") or 8)
        self.group_msgs = int(c.get("group_max_messages") or 12)
        self.data: Dict[str, List[Dict[str, str]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8-sig") as f:
                self.data = json.load(f) or {}
        except Exception:
            self.data = {}

    def save(self) -> None:
        if not self.enabled:
            return
        try:
            tmp = HISTORY_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, HISTORY_PATH)
        except Exception:
            pass

    def add(self, key: str, role: str, content: str, name: str = "") -> None:
        if not self.enabled or not content:
            return
        lst = self.data.setdefault(key, [])
        item = {"role": role, "content": content}
        if name:
            item["name"] = name
        lst.append(item)
        total = sum(len(i.get("content") or "") for i in lst)
        while len(lst) > 1 and total > self.max_chars:
            total -= len(lst.pop(0).get("content") or "")

    def window(self, key: str, limit: int) -> List[Dict[str, str]]:
        out = []
        for i in (self.data.get(key) or [])[-limit:]:
            content = i.get("content") or ""
            if i.get("name"):
                content = "%s：%s" % (i["name"], content)
            out.append({"role": i.get("role") or "user", "content": content})
        return out


MEMORY_PATH = os.path.join(HERE, "memory.md")
GLOBAL_SECTIONS = ("全局", "通用", "所有人", "global", "Global")


def load_memory(path: str = MEMORY_PATH) -> Dict[str, List[str]]:
    """解析 memory.md：`## 小节` → 条目列表。

    故意用 markdown 而不是 JSON —— 这是给人手改的东西，
    改一句设定不该需要懂逗号和引号。
    """
    out: Dict[str, List[str]] = {}
    cur: Optional[str] = None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if line.startswith("## "):
                    cur = line[3:].strip()
                    out.setdefault(cur, [])
                elif cur and line.strip().startswith(("- ", "* ")):
                    item = line.strip()[2:].strip()
                    if item and not item.startswith("示例："):
                        out[cur].append(item)
    except FileNotFoundError:
        return {}
    except OSError as e:
        log("读取记忆库失败：%s" % e)
        return {}
    return out


def memory_for(mem: Dict[str, List[str]], who: str, max_chars: int = 800) -> str:
    """拼出这次对话该带上的记忆：全局节 + 命中的个人节。"""
    if not mem:
        return ""
    parts = []
    for section, items in mem.items():
        if not items:
            continue
        hit = section in GLOBAL_SECTIONS or (
            who and (section in who or who in section))
        if hit:
            parts.append("【%s】%s" % (section, "；".join(items)))
    text = "\n".join(parts)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


def group_member_index(db, group_wxid: str,
                       cache: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """群成员显示名 → wxid。一个群只建一次，别每轮都开 contact.db。"""
    if group_wxid in cache:
        return cache[group_wxid]
    idx: Dict[str, str] = {}
    try:
        for m in (db.get_group_members(group_wxid) or []):
            w = m.get("username") or ""
            if not w:
                continue
            for nm in (m.get("remark"), m.get("nick_name")):
                nm = (nm or "").strip()
                if len(nm) >= 2:            # 单字名字太容易误命中，不收
                    idx[nm] = w
    except Exception as e:
        log("读群成员失败：%s" % e)
    cache[group_wxid] = idx
    return idx


def recall_block(db, cfg: Dict[str, Any], group_wxid: str, ask_text: str,
                 my_wxid: str, cache: Dict[str, Dict[str, str]],
                 my_names: List[str]) -> str:
    """按需检索：问里提到某个群成员，就把他最近的发言捞出来当参考。

    比"把窗口调到 200 条"便宜得多 —— 没人被点名时一次查询都不做。
    数据源是微信自己的库，不是另存一份，所以能翻到很久以前。
    """
    rc = cfg.get("recall") or {}
    if not rc.get("enabled", True):
        return ""
    idx = group_member_index(db, group_wxid, cache)
    if not idx:
        return ""

    # 一段话里可能提了两个人，最多取两个，避免拼出巨长的提示
    hit: List[str] = []
    for nm, w in idx.items():
        if nm in my_names or w == my_wxid:
            continue
        if nm in ask_text and w not in hit:
            hit.append(w)
        if len(hit) >= int(rc.get("max_people") or 2):
            break
    if not hit:
        return ""

    scan = int(rc.get("scan_messages") or 300)
    per = int(rc.get("per_person_limit") or 30)
    max_chars = int(rc.get("max_chars") or 1200)
    try:
        rows = db.get_messages(group_wxid, limit=scan) or []
    except Exception as e:
        log("检索历史失败：%s" % e)
        return ""

    nmap = {}
    try:
        nmap = db.nickname_map() or {}
    except Exception:
        pass

    blocks: List[str] = []
    for w in hit:
        picked = [r for r in rows
                  if (r.get("sender_username") or "") == w and r.get("type") == "文本"][:per]
        if not picked:
            continue
        lines = []
        for r in reversed(picked):
            ts = datetime.fromtimestamp(int(r.get("create_time") or 0)).strftime("%m-%d %H:%M")
            # 群消息正文带「wxid_xxx:\n」前缀，必须剥掉，否则检索出来的东西全是噪音
            _, body = split_group_content(r.get("content") or "", "")
            body = (body or "").strip()
            for n in my_names:                  # 记录里 @ 过 bot 的地方也去掉，免得它以为在叫它
                body = body.replace("@" + n, "").replace("\u2005", "").strip()
            if not body:
                continue
            lines.append("%s %s" % (ts, body[:80]))
        if not lines:
            continue
        name = nmap.get(w) or w[:10]
        blocks.append("【%s 最近说的话（从记录里翻出来的）】\n%s" % (name, "\n".join(lines)))

    text = "\n\n".join(blocks)
    if max_chars and len(text) > max_chars:
        text = text[-max_chars:]            # 留最近的，别留最老的
    return text


def load_dotenv(path: str = ENV_PATH) -> None:
    """极简 .env 读取：KEY=VALUE / # 注释 / 可选引号。不覆盖已有环境变量。"""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    os.environ.setdefault(k, v)
    except OSError:
        pass

DEFAULT_CONFIG: Dict[str, Any] = {
    "_说明": "白名单是硬边界：不在列表里的人，bot 一个字都不会回。",
    "allow_friends": [],
    "allow_groups": [],
    "backend": {"type": "echo"},
    "limits": {
        "per_friend_per_hour": 8,
        "delay_min_s": 2.0,
        "delay_max_s": 8.0,
        "quiet_hours": [0, 7],
        "quiet_extra_delay_s": 6.0,
        "max_rounds": 6,
        "round_window_s": 300,
    },
    "poll_interval_s": 2.0,
    "max_reply_chars": 120,
}


def log(msg: str) -> None:
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------
# 配置与状态
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        raise SystemExit(
            "已生成 config.json 模板，但白名单是空的。\n"
            "先把好友填进 allow_friends（wxid 用 probe.py --show 拿），再重跑。"
        )
    # utf-8-sig：记事本/PowerShell 写出来的文件常带 BOM，普通 utf-8 会直接解析失败
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def load_state() -> Dict[str, int]:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8-sig") as f:
            return {str(k): int(v) for k, v in json.load(f).items()}
    except Exception as e:
        log("state.json 读不动（%s），按空水位处理——注意可能重复回旧消息" % e)
        return {}


def save_state(state: Dict[str, int]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------------------
# 后端：只做"文本 → 文本"，不碰微信
# ---------------------------------------------------------------------------

class EchoBackend:
    """M1 验证用：把对方的话原样回一句。不发网络请求，不花钱。"""

    name = "echo"

    def reply(self, text: str, ctx: Dict[str, Any]) -> str:
        return "收到：%s" % text.strip()


class OpenAICompatBackend:
    """标准 /v1/chat/completions，DeepSeek / 中转 / 本地 llama.cpp 都吃这套。"""

    name = "openai"

    def __init__(self, cfg: Dict[str, Any]):
        self.url = cfg.get("url") or ""
        self.model = cfg.get("model") or ""
        self.key_env = cfg.get("api_key_env") or "DEEPSEEK_API_KEY"
        self.system = cfg.get("system") or (
            "你在微信上替一个真人朋友回消息。说话短、口语、别用书面语，"
            "别用 markdown，别自称 AI，一次最多两句。"
        )
        self.timeout = float(cfg.get("timeout_s") or 30)
        self.thinking = bool(cfg.get("thinking", False))
        self.max_tokens = int(cfg.get("max_tokens") or 300)
        self.proxy = (cfg.get("proxy") or "").strip()
        self.group_system = cfg.get("group_system") or (
            "你在一个朋友群里，是被群友 @ 才开口的 AI 分身。"
            "像群里一个熟人随口接话：通常一句话，口语，可以带点玩笑或自嘲。"
            "不用 markdown、不列条目、不总结、不客套。"
            "绝对不要说「有事说事」「请讲」「有什么可以帮你」这类客服腔。"
            "偶尔可以用「乐子」「绷不住了」「闹麻了」这类口头语，但别每句都用、别堆。"
            "如果有人让你重复同一段话很多遍、或者明显在耍你，别照做，用一句口头语挡回去就行，别解释。"
            "不确定就直说不知道，别硬聊。不要 @ 任何人，不要主动找话题。"
        )
        if not self.url or not self.model:
            raise SystemExit("backend.type=openai 需要 url 和 model")

    def reply(self, text: str, ctx: Dict[str, Any]) -> str:
        import urllib.error
        import urllib.request

        key = os.environ.get(self.key_env, "")
        if not key:
            raise RuntimeError("环境变量 %s 里没有 API Key" % self.key_env)

        default_sys = self.group_system if ctx.get("kind") == "group" else self.system
        system = ctx.get("system") or default_sys

        # 记忆不拼进 system：它按询问人变化，放 system 会让前缀每次都不同、缓存永远不命中。
        # 改到最后一条 user 消息末尾（见下方 messages 组装处）。
        memo = (ctx.get("memory") or "").strip()

        menu = (ctx.get("stickers") or "").strip()
        if menu:
            system = system + ("\n\n" + menu
                       + "\n想发表情就在回复末尾写 <emoji:标签>（把上面某个标签原样抄进去，别自己编，别写编号）。")

        imgs = (ctx.get("images") or "").strip()
        if imgs:
            system = system + "\n\n" + imgs

        sblk = (ctx.get("stickers_seen") or "").strip()
        if sblk:
            system = system + "\n\n【他们刚发的表情】\n" + sblk



        recall = (ctx.get("recall") or "").strip()
        if recall:
            system = system + ("\n\n【刚翻到的聊天记录，需要用就用，别照搬】\n" + recall)

        # 有上下文就整段带上；没有就单条
        msgs = ctx.get("messages")
        if not msgs:
            msgs = [{"role": "user", "content": text}]

        msgs = list(msgs)
        # 附件索引放在最后（靠近问题，显著性高）
        hint = (ctx.get("attach_hint") or "").strip()
        if hint:
            msgs.append({"role": "user", "content": hint})
        att = (ctx.get("attachments") or "").strip()
        if att:
            msgs.append({"role": "user", "content": att})
        if memo:
            if msgs:
                last = dict(msgs[-1])
                last["content"] = (last.get("content") or "") + \
                    "\n\n【关于这些人，你要记住】\n" + memo
                msgs[-1] = last
            else:
                msgs = [{"role": "user", "content": "【关于这些人，你要记住】\n" + memo}]

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + msgs,
            "temperature": 0.8,
            "max_tokens": int(self.max_tokens),
        }
        # DeepSeek 默认开"思考模式"：思考 token 会吃掉 max_tokens，导致 content 为空。
        # 聊天场景不需要思考，关掉——更快、更便宜、也不会再出现空回复。
        if self.thinking:
            payload["thinking"] = {"type": "enabled"}
        else:
            payload["thinking"] = {"type": "disabled"}
        dump_prompt(ctx.get("kind") or "?", ctx.get("who") or "?", system, msgs)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            opener = None
            if self.proxy:
                # 有的中转站只对特定地区放行，直连会被 Cloudflare 403
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
            if opener is not None:
                resp = opener.open(req, timeout=self.timeout)
            else:
                resp = urllib.request.urlopen(req, timeout=self.timeout)
            with resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                detail = str(e.reason)
            hint = ""
            if e.code == 401:
                hint = "（key 不对，或这个中转站要求别的鉴权头）"
            elif e.code == 404:
                hint = "（URL 路径不对：多半漏了 /v1/chat/completions）"
            elif e.code == 429:
                hint = "（限流或额度用完）"
            elif e.code == 402 or "Insufficient Balance" in detail:
                raise RuntimeError(
                    "BALANCE: 账户余额不足，请去平台充值（%s）" % detail[:120])
            raise RuntimeError("HTTP %s%s：%s" % (e.code, hint, detail))
        except Exception as e:
            raise RuntimeError("连不上 %s：%s" % (self.url, e))

        try:
            data = json.loads(raw)
        except Exception:
            raise RuntimeError("返回的不是 JSON（前 200 字）：%s" % raw[:200])

        return self._extract_text(data, raw)

    @staticmethod
    def _extract_text(data: Any, raw: str = "") -> str:
        """兼容各家：标准 OpenAI / 有的中转只给 text / 有的包了一层 data。"""
        def dig(obj, path):
            cur = obj
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                elif isinstance(cur, list) and isinstance(k, int) and len(cur) > k:
                    cur = cur[k]
                else:
                    return None
            return cur

        for path in (("choices", 0, "message", "content"),
                     ("choices", 0, "text"),
                     ("data", "choices", 0, "message", "content"),
                     ("content",),
                     ("reply",)):
            val = dig(data, path)
            if isinstance(val, str) and val.strip():
                return val.strip()
        # 思考模式下 content 可能为空，退回 reasoning_content（宁可说错也别沉默）
        rc = dig(data, ("choices", 0, "message", "reasoning_content"))
        if isinstance(rc, str) and rc.strip():
            log("模型 content 为空，退回 reasoning_content")
            return rc.strip()[:300]
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError("上游报错：%s" % str(data["error"])[:300])
        try:
            _dbg = (load_config().get("debug") or {})
        except Exception:
            _dbg = {}
        if _dbg.get("usage"):
            u = data.get("usage") or {}
            log("tokens: 提示 %s（缓存 %s）｜ 输出 %s"
                % (u.get("prompt_tokens"), (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                   u.get("completion_tokens")))
        fr = dig(data, ("choices", 0, "finish_reason"))
        raise RuntimeError("认不出返回结构（finish_reason=%s，前 200 字）：%s"
                           % (fr, (raw or str(data))[:200]))


def build_backend(cfg: Dict[str, Any]):
    b = cfg.get("backend") or {}
    t = (b.get("type") or "echo").lower()
    if t == "echo":
        return EchoBackend()
    if t == "openai":
        return OpenAICompatBackend(b)
    raise SystemExit("未知 backend.type：%s" % t)


# ---------------------------------------------------------------------------
# 频控与熔断
# ---------------------------------------------------------------------------

class Guard:
    def __init__(self, limits: Dict[str, Any]):
        self.limits = limits
        self.sent: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=64))
        self.turns: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=64))
        self.retries: Dict[str, int] = defaultdict(int)
        self.group_last: Dict[str, float] = {}

    def group_allowed(self, chat: str, min_interval_s: float) -> bool:
        """群聊防刷屏：单群两次发言至少隔 min_interval_s 秒。"""
        last = self.group_last.get(chat, 0.0)
        return (time.time() - last) >= min_interval_s

    def mark_group_sent(self, chat: str) -> None:
        self.group_last[chat] = time.time()

    def fail(self, chat: str, seq: int, consume) -> None:
        """处理失败：不推进水位，留着下轮重试；连续失败到上限才放弃。"""
        self.retries[chat] += 1
        cap = int(self.limits.get("max_retries", 3))
        if self.retries[chat] >= cap:
            log("连续 %d 次失败，放弃这条（seq=%s），避免堵住整个队列"
                % (self.retries[chat], seq))
            consume(seq)
            self.retries[chat] = 0
        else:
            log("第 %d/%d 次失败，水位停在原地，下轮重试" % (self.retries[chat], cap))

    def allow(self, chat: str) -> bool:
        now = time.time()
        cap = int(self.limits.get("per_friend_per_hour", 8))
        q = self.sent[chat]
        while q and now - q[0] > 3600:
            q.popleft()
        return len(q) < cap

    def circuit_open(self, chat: str) -> bool:
        """同一会话窗口内轮次过多 → 判定为对聊死循环，收尾退出。"""
        now = time.time()
        win = float(self.limits.get("round_window_s", 300))
        max_rounds = int(self.limits.get("max_rounds", 6))
        q = self.turns[chat]
        while q and now - q[0] > win:
            q.popleft()
        return len(q) >= max_rounds

    def note_sent(self, chat: str) -> None:
        now = time.time()
        self.sent[chat].append(now)
        self.turns[chat].append(now)

    def delay(self, quiet: bool) -> float:
        d = random.uniform(
            float(self.limits.get("delay_min_s", 2.0)),
            float(self.limits.get("delay_max_s", 8.0)),
        )
        if quiet:
            d += float(self.limits.get("quiet_extra_delay_s", 6.0))
        return d


def in_quiet_hours(limits: Dict[str, Any]) -> bool:
    qh = limits.get("quiet_hours") or []
    if len(qh) != 2:
        return False
    h = datetime.now().hour
    a, b = int(qh[0]), int(qh[1])
    return a <= h < b if a <= b else (h >= a or h < b)


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def connect_db():
    from wechatauto import WeChatDB
    try:
        return WeChatDB(), None
    except Exception as e:
        return None, e


def init_watermarks(db, chats: List[str], state: Dict[str, int]) -> Dict[str, int]:
    """首次出现某个会话时，把水位设到当前最新，避免回补历史。"""
    for who in chats:
        if who in state:
            continue
        try:
            rows = db.get_messages(who, limit=1) or []
            state[who] = int(rows[0].get("sort_seq") or 0) if rows else 0
            log("初始化水位 %s → %s（不回补历史）" % (short(who), state[who]))
        except Exception as e:
            log("初始化水位失败 %s：%s" % (short(who), e))
            state[who] = 0
    return state


def short(wxid: str) -> str:
    return wxid if len(wxid) <= 14 else wxid[:8] + "…" + wxid[-4:]


def one_round(db, cfg, backend, guard, state, dry_run: bool, from_beginning: bool,
              my_wxid: str = "", hist: Optional[History] = None,
              mem: Optional[Dict[str, List[str]]] = None) -> int:
    """跑一轮轮询，返回处理的消息数。

    注意：不要在这里调 db.get_self_info()。
    该函数内部 open 了 contact.db 却不 close，反复调用会泄漏连接，
    之后库自己合并 WAL 时 os.replace 会被残留句柄挡住（WinError 5）。
    my_wxid 只在启动时取一次，由调用方传进来。
    """
    limits = cfg.get("limits") or {}
    quiet = in_quiet_hours(limits)
    handled = 0

    for entry in cfg.get("allow_friends") or []:
        wxid = entry.get("wxid") if isinstance(entry, dict) else None
        name = entry.get("name") if isinstance(entry, dict) else None
        if not wxid or not name:
            log("白名单条目缺 wxid/name，跳过：%r" % (entry,))
            continue

        incoming_cap = int(limits.get("max_incoming_chars") or 800)

        since = 0 if from_beginning else int(state.get(wxid, 0))
        try:
            rows = db.get_new_messages(wxid, since_seq=since, limit=50) or []
        except Exception as e:
            log("读消息失败 %s：%s" % (short(wxid), e))
            continue

        def consume(s: int, _w: str = wxid) -> None:
            """推进水位 = 这条以后不再处理。"""
            state[_w] = max(state.get(_w, 0), s)

        for r in rows:
            seq = int(r.get("sort_seq") or 0)

            # ---- 过滤：这些是"有意不回"，水位可以直接推 ----
            # 引用回复是 appmsg type=57，先还原成文本，否则 @ 了也看不见
            norm = normalize_quote_row(r)
            if norm is None:
                consume(seq)
                continue
            r = norm
            sender = r.get("sender_username") or ""
            sender_id = r.get("sender_id")
            # 微信 4.x：自己发的消息 real_sender_id 恒为 2（库里的中继 id）。
            # 名字解析失败时靠这个 id 兜底，避免"自己回自己"。
            is_own = (bool(my_wxid) and sender == my_wxid) or (not sender and sender_id == 2)
            if is_own:
                consume(seq)
                continue
            content = cap_incoming(str(r.get("content") or "").strip(), incoming_cap)
            if not content:
                consume(seq)
                continue
            if content.startswith("#") or content.startswith("/"):
                log("跳过指令式消息：%s" % content[:20])
                consume(seq)
                continue

            if not guard.allow(wxid):
                log("频控拦截 %s（本小时已发满 %s 条）" % (short(wxid), limits.get("per_friend_per_hour")))
                consume(seq)
                continue
            if guard.circuit_open(wxid):
                log("熔断 %s：窗口内轮次过多，判定对聊循环，停止回复" % short(wxid))
                consume(seq)
                continue

            # ---- 以下任何一步失败，水位都**不推进**，下轮重试 ----
            # 上下文：窗口 + 当前这条（不是先写历史再读，失败时不会留半条）
            ctx: Dict[str, Any] = {"kind": "friend", "who": name, "wxid": wxid,
                                   "stickers": sticker_menu()}
            _REPLY_SNAPSHOT.clear()
            _REPLY_SNAPSHOT.update(_MENU_SNAPSHOT)
            if mem:
                mcfg = cfg.get("memory") or {}
                ctx["memory"] = memory_for(mem, name, int(mcfg.get("max_chars") or 800))
            try:
                vcfg = cfg.get("vision") or {}
                if vcfg.get("enabled", True):
                    blk = recent_images_block(
                        db, wxid, time.time() - float(vcfg.get("lookback_s") or 300),
                        int(vcfg.get("max_recent_images") or 2))
                    if blk:
                        ctx["images"] = blk
            except Exception as e:
                log("看图异常（忽略）：%s" % e)
            try:
                svcfg = cfg.get("sticker_vision") or {}
                if svcfg.get("enabled", True):
                    sblk = sticker_block(db, cfg, wxid,
                                         time.time() - float(svcfg.get("lookback_s") or 300),
                                         lambda w: name or "对方",
                                         int(svcfg.get("max_recent") or 3))
                    if sblk:
                        ctx["stickers_seen"] = sblk
            except Exception as e:
                log("表情上下文异常（忽略）：%s" % e)
            # 记忆卡：人卡 + 共同群的群卡（私聊里记得群里的事）
            try:
                _shared = []
                for g in (cfg.get("allow_groups") or []):
                    _gx = g.get("wxid") or ""
                    if _gx:
                        _shared.append(_gx)
                _facts_f = mem_facts_block("person", wxid, (content or "")[:200])
                if _facts_f:
                    content = content + "\n\n" + _facts_f
                _card = mem_friend_card(wxid, _shared[:2])
                if _card:
                    content = _card + "\n\n" + content
                    log("记忆卡注入（私聊）：%d 字" % len(_card))
            except Exception as e:
                log("私聊记忆卡注入失败（忽略）：%s" % e)
            if hist is not None and hist.enabled:
                window = hist.window(wxid, hist.friend_turns * 2)
                ctx["messages"] = window + [{"role": "user", "content": content}]

            try:
                reply = backend.reply(content, ctx)
            except Exception as e:
                if "BALANCE:" in str(e):
                    log("！！ 余额不足，无法生成回复 —— 请充值后重启。本条跳过")
                    consume(seq)
                    continue
                log("生成回复失败（水位不推进，下轮重试）：%s" % e)
                guard.fail(wxid, seq, consume)
                break
            if not reply:
                log("模型返回空内容，跳过这条")
                consume(seq)
                continue
            reply = guard_reply(reply, cfg, {})
            raw_reply = reply
            reply = strip_emoji_tag(reply)
            if not reply:
                # 只有表情没有文字也是正常回复
                if EMOJI_TAG.search(raw_reply or ""):
                    maybe_send_sticker(db, wxid, name, cfg, raw_reply, content)
                consume(seq)
                continue

            delay = guard.delay(quiet)
            incoming = content if len(content) <= 40 else content[:40] + "…"
            if dry_run:
                log("[dry-run] %s → 「%s」→ 回「%s」（拟等 %.1fs）"
                    % (name, incoming, reply, delay))
                ok = True
            else:
                time.sleep(delay)
                bcfg = bubble_cfg({}, cfg)
                ok = send_multi(lambda t: send(db, name, t, wxid), reply, bcfg)
                log("[live] %s → 「%s」→ 回「%s」（%s）"
                    % (name, incoming, reply, "成功" if ok else "失败"))

            if ok:
                consume(seq)
                guard.note_sent(wxid)
                handled += 1
                if hist is not None and hist.enabled:
                    hist.add(wxid, "user", content)
                    hist.add(wxid, "assistant", reply)
                maybe_send_sticker(db, wxid, name, cfg, raw_reply, content)
            else:
                # 关键：发送失败不能吞消息，否则这条永远消失
                guard.fail(wxid, seq, consume)
                break

    return handled


# ---------------------------------------------------------------------------
# 群聊（M2）
# ---------------------------------------------------------------------------

GROUP_PREFIX_RE = re.compile(r"^(?P<who>[A-Za-z0-9_\-]{4,}):\n")


def split_group_content(content: str, sender_username: str = "") -> Tuple[str, str]:
    """群消息正文常以「wxid_xxx:\\n」开头，剥掉它，返回 (发送者, 正文)。"""
    text = str(content or "")
    who = sender_username or ""
    m = GROUP_PREFIX_RE.match(text)
    if m:
        who = who or m.group("who")
        text = text[m.end():]
    return who, text.strip()


def group_fired(text: str, entry: Dict[str, Any]) -> bool:
    """该不该在群里开口。

    1. 先按旧语义算 base（被@ / 触发词）
    2. base 为真 → 一定回
    3. base 为假 → 只有「没被@ 且没勾必须@」时才按概率插话
    """
    triggers = [t for t in (entry.get("triggers") or []) if t]
    require_at = bool(entry.get("require_at", True))
    # 「被@」要按自己的名字判：@别人 不算叫我
    names = self_names(entry)
    at_me = bool("\u2005" in text)
    if not at_me and names:
        at_me = any(("@" + n) in text for n in names)
    if not at_me and not names:
        at_me = "@" in text          # 没配名字时退回旧行为
    at = at_me
    hit_trigger = any(t in text for t in triggers) if triggers else False

    if require_at and not at:
        base = False
    elif triggers:
        base = hit_trigger
    else:
        base = at
    if base:
        return True

    # 原本该不回：看概率（只对"没被@ 且允许未@插话"的群生效）
    if at or require_at:
        return False
    try:
        prob = float(entry.get("chime_prob") or 0)
    except Exception:
        prob = 0.0
    if prob <= 0:
        return False
    import random
    if random.random() * 100 < prob:
        log("群 %s：未@ 但命中插话概率 %.0f%%，决定开口"
            % (entry.get("name") or "?", prob))
        return True
    return False


def self_names(entry: Dict[str, Any]) -> List[str]:
    """小号在群里的所有可能叫法，长的排前面（"大肥鲸-aiBot" 要先于 "大肥鲸" 剥掉）。"""
    names = list(entry.get("self_names") or []) + list(entry.get("triggers") or [])
    return sorted({n for n in names if n}, key=len, reverse=True)


def clean_for_model(text: str, entry: Dict[str, Any]) -> str:
    """去掉 @ 标记和自我介绍，别把「@大肥鲸-aiBot」喂给模型。

    必须按"最长名字优先"剥：群里 @ 出来的通常是完整群昵称（大肥鲸-aiBot），
    只剥触发词（大肥鲸）会留下 -aiBot 这种残渣。
    """
    out = text
    for n in self_names(entry):
        out = out.replace("@" + n, "")
    for n in self_names(entry):
        out = out.replace(n, "")
    return out.replace("\u2005", "").strip()


def resolve_groups(db, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 config 里按群名写的白名单，解析成真实群 wxid。"""
    wanted = [e for e in (cfg.get("allow_groups") or []) if isinstance(e, dict)]
    if not wanted:
        return []
    try:
        all_groups = db.get_groups() or []
    except Exception as e:
        log("读群列表失败：%s" % e)
        return []

    out: List[Dict[str, Any]] = []
    for e in wanted:
        wxid = e.get("wxid") or ""
        name = e.get("name") or ""
        if not wxid and name:
            exact = [g for g in all_groups if (g.get("name") or "") == name]
            sub = [g for g in all_groups if name and name in (g.get("name") or "")]
            pick = (exact or sub or [None])[0]
            if pick:
                wxid = pick.get("username") or ""
                name = pick.get("name") or name
        if not wxid:
            log("群「%s」没找到 —— 小号是不是还没进群？" % (name or "?"))
            continue
        out.append({"wxid": wxid, "name": name or short(wxid), "entry": e})
    return out


SCENE_GAP_S = 120      # 群场景里超过这么久没说话，就插一条分隔线


QUOTE_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
QUOTE_REF_RE = re.compile(r"<refermsg>(.*?)</refermsg>", re.S)


def normalize_quote_row(r: dict) -> Optional[dict]:
    """把「引用回复」卡片还原成一条文本消息；其它类型原样返回，非可回复类型返回 None。

    只认 appmsg type=57（引用回复）。普通链接/小程序卡片仍然跳过，避免误回广告。
    """
    rtype = r.get("type")
    if rtype == "文本":
        return r
    if rtype != "文件/链接/卡片":
        return None
    content = str(r.get("content") or "")
    if "<appmsg" not in content or "<type>57</type>" not in content:
        return None
    m = QUOTE_TITLE_RE.search(content)
    if not m:
        return None
    title = html.unescape(m.group(1)).strip()
    if not title:
        return None

    quote_note = ""
    ref = QUOTE_REF_RE.search(content)
    if ref:
        blk = html.unescape(ref.group(1))
        nm = re.search(r"<displayname>(.*?)</displayname>", blk, re.S)
        who = (nm.group(1).strip() if nm else "") or "有人"
        md5 = re.search(r'md5\s*=\s*"([0-9a-fA-F]{32})"', blk)
        if md5:
            desc = ""
            try:
                import sticker_vision as sv
                acc = sv.find_account_dir()
                if acc:
                    desc = sv.describe_sticker(acc, md5.group(1).lower(), vision_describe, log)
            except Exception:
                desc = ""
            quote_note = "（引用 %s 发的表情：%s）" % (who, desc or "一个表情包")
        else:
            body = re.search(r"<content>(.*?)</content>", blk, re.S)
            text = html.unescape(body.group(1)).strip() if body else ""
            text = re.sub(r"<[^>]+>", "", text).strip()
            if text:
                quote_note = "（引用 %s 的话：%s）" % (who, text[:60])

    # 不要拼 `wxid: ` 前缀：GROUP_PREFIX_RE 只认 `:\n`，拼 `: ` 会剥不掉，
    # 结果问题文本里带着一长串 wxid（实测）。
    new_content = title
    if quote_note:
        new_content += "\n" + quote_note
    out = dict(r)
    out["content"] = new_content
    out["type"] = "文本"
    return out


STICKER_DESC_BUDGET = 8      # 整个窗口里最多给几个表情配描述


def _sticker_desc(md5: str) -> str:
    """md5 → 一句描述（走 sticker_vision 的缓存，命中时不花 API）。"""
    try:
        import sticker_vision as sv
        acc = sv.find_account_dir()
        if not acc:
            return ""
        return sv.describe_sticker(acc, md5, vision_describe, None) or ""
    except Exception:
        return ""


APPMSG_TYPE_RE = re.compile(r"<type>(\d+)</type>")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
APPMSG_LABEL = {
    "3": "音乐", "4": "视频链接", "5": "链接", "6": "文件", "8": "表情",
    "19": "合并消息", "33": "小程序", "36": "小程序", "51": "视频号",
    "57": "引用", "62": "拍一拍", "2000": "转账", "2001": "红包",
}


def _stamp(ts: int) -> str:
    """附件行的时间前缀：`MM-DD HH:MM `。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") + " "
    except Exception:
        return ""


ATTACH_HINT_RE = re.compile(r"(图|照片|截图|视频|文件|附件|表情包|几点|昨天|前天|刚才|早上|中午|晚上)")


CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11,
          "十二": 12, "十一": 11, "零点": 0}


def _cn_to_int(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in CN_NUM:
        return CN_NUM[text]
    if text.startswith("十") and len(text) == 2 and text[1] in CN_NUM:
        return 10 + CN_NUM[text[1]]
    return None


def parse_relative_time(text: str) -> Tuple[Optional[datetime], str]:
    """从一句话里解析相对时间，返回 (目标时间, 归一化说明)。

    只在高置信时返回时间（必须同时有"哪一天"和"几点"），否则返回 None，
    调用方据此决定要不要插提示——宁可不说，也别指错图。
    """
    t = text or ""
    day_off = None
    day_word = ""
    period = ""
    period = ""
    for word, off in (("大前天", -3), ("前天", -2), ("昨天", -1), ("昨晚", -1),
                      ("今早", 0), ("今天", 0), ("今晚", 0),
                      ("刚才", 0), ("刚刚", 0)):
        if word in t:
            day_off, day_word = off, word
            break
    no_day = day_off is None          # 没说日期：后面按"最近一次"处理
    # "昨晚/今晚"里的"晚"表示傍晚，别当成早上七点
    if "晚" in day_word or "夜" in day_word:
        period = "晚上"

    if not period:
        for w in ("凌晨", "早上", "早晨", "上午", "中午", "下午", "傍晚", "晚上", "夜里"):
            if w in t:
                period = w
                break

    m = re.search(r"(\d{1,2})\s*[:：点时]\s*(半|\d{1,2})?", t)
    hour = minute = None
    if m:
        hour = int(m.group(1))
        tail = m.group(2)
        if tail == "半":
            minute = 30
        elif tail and tail.isdigit():
            minute = int(tail)
        else:
            minute = 0
    else:
        m2 = re.search(r"([一二两三四五六七八九十]{1,2})\s*[点时](半)?", t)
        if m2:
            hour = _cn_to_int(m2.group(1))
            minute = 30 if m2.group(2) == "半" else 0

    if hour is None:
        return None, ""
    if period in ("下午", "傍晚", "晚上", "夜里") and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23):
        return None, ""

    now = datetime.now()
    if no_day:
        # 没说日期：取**最近一次已经过去**的那个时刻（今天，太晚就退回昨天）
        day = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day > now + timedelta(hours=1):
            day -= timedelta(days=1)
        return day, "（没提哪天，按最近的一次 = %s）" % day.strftime("%m-%d %H:%M")

    day = (now + timedelta(days=day_off)).replace(hour=hour, minute=minute,
                                                  second=0, microsecond=0)
    if day > now + timedelta(hours=1):        # 解析出未来时间，多半是解析错了
        return None, ""
    note = "（今天是 %s，%s = %s）" % (now.strftime("%m-%d"), t.strip()[:12],
                                      day.strftime("%m-%d %H:%M"))
    return day, note


SELF_REF_RE = re.compile(r"我(?:自己)?(?:\s*)(?:发|拍|截|照|传)的|我的图|我那张")
NEAR_ATTACH_RE = re.compile(r"(图|照片|截图|视频|拍|发)")


def parse_sender_constraint(text: str, asker: str, member_index: Dict[str, str],
                            my_wxid: str) -> Tuple[str, str]:
    """解析出"用户明确指认的发送者"。返回 (wxid 或空, 说明)。

    只在用户**明确**说了是谁的时候才给约束；没说就返回空，交给时间排序。
    """
    t = text or ""
    if SELF_REF_RE.search(t):
        me = my_wxid or ""
        if me:
            return me, "你说的「我发的」"
        return "", ""
    # 名字 + 附件词同时出现才算指认（避免"这张图里程好搞笑"被误判）
    best = ("", "")
    NAME_TAIL_RE = re.compile(r"^(?:昨天|今天|前天|刚才|早上|中午|下午|晚上|\s*发|\s*拍|\s*的|\s*那|\s*在)")
    for name, wxid in (member_index or {}).items():
        if not name or name not in t:
            continue
        i = t.find(name)
        if len(name) == 1:
            # 单字名（如「程」）必须在"名字位置"：句首或标点/空格之后，
            # 且后面紧跟时间词或动作词，否则「过程」也会命中
            before = t[i - 1] if i > 0 else ""
            after = t[i + 1:]
            if before and before not in " 　，。！？、：；@":
                continue
            if not NAME_TAIL_RE.match(after):
                continue
        window = t[max(0, i - 6): i + len(name) + 8]
        if NEAR_ATTACH_RE.search(window):
            if len(name) > len(best[1]):
                best = (wxid, name)
    if best[0]:
        return best[0], "你提到了「%s」" % best[1]
    return "", ""


def recent_own_attachments(db, chat_wxid: str, wxid: str, nameof,
                           my_wxid: str, n: int = 2) -> str:
    """兜底：列出这个人最近发的 n 条附件（时间说不清时给模型参考）。"""
    if not wxid:
        return ""
    try:
        rows = db.get_messages(chat_wxid, limit=400) or []
    except Exception:
        return ""
    out = []
    for r in rows:
        if r.get("type") not in ("图片", "视频", "文件/链接/卡片"):
            continue
        if (r.get("sender_username") or "") != wxid:
            continue
        content = str(r.get("content") or "")
        if r.get("type") == "文件/链接/卡片":
            if "<type>57</type>" in content or "<type>62</type>" in content:
                continue
        ts = int(r.get("create_time") or 0)
        try:
            stamp = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        except Exception:
            stamp = ""
        if r.get("type") == "图片":
            desc, _ = img_desc_try(db, chat_wxid, r, 1)
            body = ("图片：%s" % desc) if desc else "图片（未识别）"
        elif r.get("type") == "视频":
            body = "视频"
        else:
            m = APPMSG_TYPE_RE.search(content)
            tm = TITLE_RE.search(content)
            title = " ".join((tm.group(1) if tm else "").split())[:40]
            body = "%s：%s" % (APPMSG_LABEL.get(m.group(1) if m else "", "卡片"), title)
        out.append("  %s %s" % (stamp, body))
        if len(out) >= n:
            break
    if not out:
        return ""
    return "【他最近发的附件（时间没对上，可作参考）】\n" + "\n".join(out)


def find_attachment_near(db, chat_wxid: str, target: datetime, nameof,
                         my_wxid: str, asker: str = "",
                         window_min: int = 60,
                         only_sender: str = "") -> str:
    """找时间上最接近 target 的那一条附件，返回一行描述；没有就返回空。"""
    try:
        rows = db.get_messages(chat_wxid, limit=400) or []
    except Exception:
        return ""
    best = None
    for r in rows:
        rtype = r.get("type")
        if rtype not in ("图片", "视频", "文件/链接/卡片"):
            continue
        if rtype == "文件/链接/卡片" and "<type>57</type>" in str(r.get("content") or ""):
            continue
        ts = int(r.get("create_time") or 0)
        if not ts:
            continue
        try:
            when = datetime.fromtimestamp(ts)
        except Exception:
            continue
        if only_sender and (r.get("sender_username") or "") != only_sender:
            continue                       # 用户指认了是谁，别人发的直接排除
        delta = abs((when - target).total_seconds()) / 60.0
        if delta > window_min:
            continue
        if best is None or delta < best[0]:  # 只按时间最近排序（不再给身份加时间）
            best = (delta, when, r)

    if not best:
        return ""
    _, when, r = best
    sender = r.get("sender_username") or ""
    who = "你自己" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
    if r.get("type") == "图片":
        desc, _ = img_desc_try(db, chat_wxid, r, 1)
        body = ("图片：%s" % desc) if desc else "图片（未识别）"
    elif r.get("type") == "视频":
        body = "视频"
    else:
        content = str(r.get("content") or "")
        m = APPMSG_TYPE_RE.search(content)
        tm = TITLE_RE.search(content)
        title = " ".join((tm.group(1) if tm else "").split())[:40]
        body = "%s：%s" % (APPMSG_LABEL.get(m.group(1) if m else "", "卡片"), title)
    return "【最相关的一条（就是他在问的）】%s %s %s" % (
        when.strftime("%m-%d %H:%M"), who, body)


def attachment_index(db, chat_wxid: str, nameof, my_wxid: str,
                     limit: int = 12, cache: Optional[Dict] = None,
                     only_sender: str = "") -> str:
    """最近附件索引：一行一个，带时间和描述。

    为什么单独做一块：附件在 300+ 行场景里太不显眼，用户一问"那张图"模型就找不到。
    单独列出来放问题旁边，命中率会高很多。
    """
    try:
        rows = db.get_messages(chat_wxid, limit=400) or []
    except Exception:
        return ""
    out = []
    img_left = 6
    for r in rows:                       # 新→旧
        rtype = r.get("type")
        if rtype not in ("图片", "视频", "文件/链接/卡片"):
            continue
        ts = int(r.get("create_time") or 0)
        sender = r.get("sender_username") or ""
        if only_sender and sender != only_sender:
            continue                      # 用户指认了是谁，索引里也只留他的
        who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        if rtype == "图片":
            desc, img_left = img_desc_try(db, chat_wxid, r, img_left)
            body = ("图片：%s" % desc) if desc else "图片（未识别）"
        elif rtype == "视频":
            body = "视频"
        else:
            content = str(r.get("content") or "")
            m = APPMSG_TYPE_RE.search(content)
            sub = m.group(1) if m else ""
            tm = TITLE_RE.search(content)
            title = " ".join((tm.group(1) if tm else "").split())[:40]
            if sub in ("57", "62"):
                continue                   # 引用回复、拍一拍都不算附件
            body = "%s：%s" % (APPMSG_LABEL.get(sub, "卡片"), title) if title else \
                APPMSG_LABEL.get(sub, "卡片")
        try:
            stamp = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        except Exception:
            stamp = ""
        out.append("  %s %s %s" % (stamp, who, body))
        if len(out) >= limit:
            break
    if not out:
        return ""
    return "【最近的图片/文件（系统已看过并描述，可直接引用）】\n" + "\n".join(out)


def render_message_body(db, chat_wxid: str, r: dict, nameof,
                        my_names: Optional[List[str]] = None,
                        img_desc_left: int = 0,
                        desc_left: int = 0,
                        ts: int = 0) -> Tuple[str, str, int, int]:
    """把一条消息渲染成 (kind, body, img_desc_left', desc_left')。

    kind: "text" | "emoji"（emoji 会被合并成 [表情×N]）
    """
    rtype = r.get("type") or ""
    content = str(r.get("content") or "")

    if rtype == "文本":
        _, text = split_group_content(content, r.get("sender_username") or "")
        for n in (my_names or []):
            text = text.replace("@" + n, "").replace("\u2005", "")
        text = " ".join((text or "").split())[:120]
        return "text", text, img_desc_left, desc_left

    if rtype == "动画表情":
        m = re.search(r'md5\s*=\s*"([0-9a-fA-F]+)"', content)
        desc = ""
        if m and desc_left > 0:
            desc = _sticker_desc(m.group(1).lower())
            if desc:
                desc_left -= 1
        return "emoji", desc, img_desc_left, desc_left

    if rtype == "图片":
        desc, img_desc_left = img_desc_try(db, chat_wxid, r, img_desc_left)
        body = ("图片：%s" % desc) if desc else "图片（未识别）"
        return "text", "[" + _stamp(ts) + body + "]", img_desc_left, desc_left

    if rtype == "视频":
        return "text", "[%s视频]" % _stamp(ts), img_desc_left, desc_left

    if rtype == "语音":
        return "text", "[语音]", img_desc_left, desc_left

    if rtype == "系统消息":
        body = " ".join(content.split())[:40]
        return "text", ("[系统] %s" % body) if body else "[系统]", img_desc_left, desc_left

    if rtype == "文件/链接/卡片":
        m = APPMSG_TYPE_RE.search(content)
        sub = m.group(1) if m else ""
        tm = TITLE_RE.search(content)
        title = " ".join((tm.group(1) if tm else "").split())[:50]
        if sub == "57":
            # 引用回复：把被引内容和本文拼起来
            note = ""
            ref = QUOTE_REF_RE.search(content)
            if ref:
                blk = html.unescape(ref.group(1))
                nm = re.search(r"<displayname>(.*?)</displayname>", blk, re.S)
                who = (nm.group(1).strip() if nm else "") or "某人"
                md5 = re.search(r'md5\s*=\s*"([0-9a-fA-F]{32})"', blk)
                if md5:
                    d = _sticker_desc(md5.group(1).lower()) if desc_left > 0 else ""
                    if d:
                        desc_left -= 1
                    note = "[引用 %s 的表情%s]" % (who, ("：" + d) if d else "")
                else:
                    bm = re.search(r"<content>(.*?)</content>", blk, re.S)
                    q = html.unescape(bm.group(1)).strip() if bm else ""
                    q = " ".join(re.sub(r"<[^>]+>", "", q).split())[:40]
                    if q:
                        note = "[引用 %s：%s]" % (who, q)
            return "text", (note + " " + title).strip(), img_desc_left, desc_left
        if sub == "62":
            return "text", "[拍一拍]", img_desc_left, desc_left
        label = APPMSG_LABEL.get(sub, "卡片")
        stamp = _stamp(ts)
        body = ("[%s%s：%s]" % (stamp, label, title)) if title else ("[%s%s]" % (stamp, label))
        return "text", body, img_desc_left, desc_left

    return "text", "[%s]" % (rtype or "未知"), img_desc_left, desc_left


# ---------------- 记忆系统 M1（画像层） ----------------

_MEM = {"conn": None, "last_run": 0.0}
_MY_WXID = ""


def my_wxid_cached() -> str:
    return _MY_WXID


def mem_conn():

    

    """懒加载 memory.db 连接。"""
    if _MEM["conn"] is None:
        import memory_store
        path = (load_config().get("memory_v2") or {}).get("db") or "memory.db"
        _MEM["conn"] = memory_store.connect(os.path.join(HERE, path))
    return _MEM["conn"]


def mem_enabled() -> bool:
    # 老配置里没有这一节时默认开启（新装用户也是开的）
    return bool((load_config().get("memory_v2") or {}).get("enabled", True))


def mem_block(scope: str, owner: str, extra=None) -> str:
    """取注入文本；关闭/出错一律返回空串（绝不阻塞回复）。"""
    if not mem_enabled():
        return ""
    try:
        import memory_store
        budget = int((load_config().get("memory_v2") or {}).get("budget_chars") or 420)
        return memory_store.render_block(mem_conn(), scope, owner, extra, budget)
    except Exception as e:
        log("读取记忆卡失败（忽略）：%s" % e)
        return ""


def mem_group_card(group_wxid: str) -> str:
    return mem_block("group", group_wxid)


def mem_friend_card(friend_wxid: str, shared_groups: Optional[List[str]] = None) -> str:
    """私聊：人卡 +（有的话）共同群的群卡。"""
    extra = [("group", g) for g in (shared_groups or [])]
    return mem_block("person", friend_wxid, extra)


def mem_name_map(db, wxid: str, fallback: str = "") -> Dict[str, str]:
    """wxid → 可读名字（群用成员备注/昵称，私聊用白名单里的名字）。"""
    m: Dict[str, str] = {}
    try:
        if wxid.endswith("@chatroom"):
            for x in (db.get_group_members(wxid) or []):
                u = x.get("username") or ""
                if u:
                    m[u] = ((x.get("remark") or x.get("nick_name") or "") or u)[:20]
        elif fallback:
            m[wxid] = fallback
    except Exception as e:
        log("读成员名失败（忽略）：%s" % e)
    return m


def mem_transcript(db, wxid: str, my_wxid: str, since_ts: int,
                   limit: int = 400, names: Optional[Dict[str, str]] = None) -> tuple:
    """把 since_ts 之后的消息拼成纯文本，返回 (文本, 最新时间)。"""
    try:
        rows = db.get_messages(wxid, limit=limit) or []
    except Exception:
        return "", since_ts
    lines, newest = [], since_ts
    for r in reversed(rows):          # 库给的是新→旧，反成时间正序
        ts = int(r.get("create_time") or 0)
        if ts <= since_ts:
            continue
        sender, _raw = split_group_content(r.get("content") or "",
                                           r.get("sender_username") or "")
        if my_wxid and sender == my_wxid:
            who = "我"
        else:
            who = (names or {}).get(sender) or sender or "有人"
        text = (r.get("content") or "").strip().replace("\n", " ")
        if not text:
            continue
        lines.append("%s：%s" % (who, text[:200]))
        newest = max(newest, ts)
    return "\n".join(lines), newest


def _mem_embed(cfg, text: str):
    """算 embedding。支持两种格式，失败一律返回 None（退化到关键词）。

    format:
      ark_multimodal  方舟：input=[{type:text,text:...}]，返回 data.embedding
      openai          通用：input="..."，返回 data[0].embedding
    """
    v3 = ((cfg.get("memory_v2") or {}).get("v3") or {})
    if not v3.get("enabled"):
        return None
    fmt = (v3.get("format") or "ark_multimodal").lower()
    url = v3.get("url") or "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
    model = v3.get("model") or "doubao-embedding-vision-251215"
    key = (os.environ.get(v3.get("key_env") or "ARK_EMBED_KEY")
           or os.environ.get("ARK_EMBED_KEY")
           or os.environ.get("ARK_API_KEY") or "")
    if not key:
        log("embedding 跳过：没有 key")
        return None
    txt = (text or "")[:1000]
    payload = {"model": model}
    if fmt.startswith("ark"):
        payload["input"] = [{"type": "text", "text": txt}]
    else:
        payload["input"] = txt
    try:
        import json as _json
        import urllib.request
        req = urllib.request.Request(url, data=_json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=25) as resp:
            d = _json.loads(resp.read().decode("utf-8", "replace"))
        data = d.get("data")
        if isinstance(data, dict) and data.get("embedding"):
            return data["embedding"]
        if isinstance(data, list) and data and data[0].get("embedding"):
            return data[0]["embedding"]
        # 少数服务把向量放在 embeddings 字段
        if isinstance(d.get("embeddings"), list) and d["embeddings"]:
            return d["embeddings"][0]
        log("embedding 返回结构无法识别：%s" % str(d)[:160])
    except Exception as e:
        log("embedding 失败（忽略，退化关键词）：%s" % e)
    return None


def mem_vec_scores(conn, scope: str, owner: str, query: str):
    """向量相似度表 {fact_id: 0..1}；未启用/失败返回 None。"""
    try:
        cfg = load_config()
        v3 = ((cfg.get("memory_v2") or {}).get("v3") or {})
        if not v3.get("enabled"):
            return None
        import memory_store
        qv = _mem_embed(cfg, query)
        if not qv:
            return None
        allv = memory_store.all_vecs(conn, scope, owner)
        if not allv:
            return None
        return {fid: memory_store.cosine(qv, v) for fid, v in allv.items()}
    except Exception:
        return None


def mem_embed_fact(cfg, conn, fid: int, text: str) -> None:
    """给新事实算 embedding（未启用直接返回）。"""
    try:
        if not (((cfg.get("memory_v2") or {}).get("v3") or {}).get("enabled")):
            return
        vec = _mem_embed(cfg, text)
        if vec:
            import memory_store
            memory_store.set_vec(conn, fid, vec)
    except Exception:
        pass


def mem_backfill_vectors(cfg, limit: int = 80) -> int:
    """给还没有向量的事实补算（启动时跑一次，后台线程里）。"""
    v3 = ((cfg.get("memory_v2") or {}).get("v3") or {})
    if not v3.get("enabled"):
        return 0
    try:
        import memory_store
        conn = mem_conn()
        rows = conn.execute(
            "SELECT f.id AS id, f.text AS text FROM facts f"
            " LEFT JOIN vec v ON v.fact_id=f.id WHERE v.fact_id IS NULL LIMIT ?",
            (int(limit),)).fetchall()
        if not rows:
            return 0
        n = 0
        for r in rows:
            vec = _mem_embed(cfg, r["text"])
            if vec:
                memory_store.set_vec(conn, r["id"], vec)
                n += 1
        if n:
            log("补算向量完成：%d 条（之前缺向量的）" % n)
        return n
    except Exception as e:
        log("补算向量失败（忽略）：%s" % e)
        return 0


def mem_facts_block(scope: str, owner: str, query: str) -> str:
    """按当轮消息检索事实，返回注入文本（失败返回空串）。"""
    if not mem_enabled():
        return ""
    fcfg = (load_config().get("memory_v2") or {}).get("facts") or {}
    if not fcfg.get("enabled", True):
        return ""
    if not (query or "").strip():
        return ""
    try:
        import memory_store
        conn = mem_conn()
        vec_map = None
        try:
            vec_map = mem_vec_scores(conn, scope, owner, query)
        except Exception:
            vec_map = None
        return memory_store.render_facts(
            conn, scope, owner, query,
            int(fcfg.get("top_k") or 5), int(fcfg.get("max_chars") or 320),
            vec_map)
    except Exception as e:
        log("事实检索失败（忽略）：%s" % e)
        return ""


def mem_extract_facts(cfg, conn, scope: str, owner: str, transcript: str) -> int:
    """从一段对话里抽事实写库，返回新增条数。"""
    fcfg = (cfg.get("memory_v2") or {}).get("facts") or {}
    if not fcfg.get("enabled", True) or not transcript:
        return 0
    try:
        import memory_store
        prompt = memory_store.FACT_PROMPT % transcript[:5000]
        out = ask_model_brief(cfg, "你是一个只输出事实清单的整理员。", prompt,
                              max_tokens=500)
        pairs = memory_store.parse_facts(out)
        n = 0
        for subj, fact in pairs:
            text = ("%s：%s" % (subj, fact)) if subj else fact
            fid = memory_store.add_fact(conn, scope, owner, text, "fact", "")
            if fid:
                n += 1
                try:
                    mem_embed_fact(cfg, conn, fid, text)
                except Exception:
                    pass
        if n:
            log("抽到 %d 条事实（%s）" % (n, owner[:12]))
        return n
    except Exception as e:
        log("事实抽取失败（忽略）：%s" % e)
        return 0


def mem_maintain(db, cfg) -> None:
    """后台维护：每 5 分钟看一次，满足条件就给对应频道更新画像。"""
    now = time.time()
    if now - _MEM["last_run"] < 300:
        return
    _MEM["last_run"] = now
    if not mem_enabled():
        return
    try:
        import memory_store
        conn = mem_conn()
        my = my_wxid_cached() or ""
        targets = []
        for g in (cfg.get("allow_groups") or []):
            gxid = g.get("wxid") or ""
            if not gxid and g.get("name"):
                # 白名单里常只填了群名，按名字解析一次 wxid
                try:
                    for h in (db.search_contact(g["name"]) or []):
                        u = (h.get("username") or "")
                        if u.endswith("@chatroom"):
                            gxid = u
                            break
                except Exception:
                    pass
            if gxid:
                targets.append(("group", gxid, "群「%s」" % (g.get("name") or ""),
                                g.get("name") or ""))
        for f in (cfg.get("allow_friends") or []):
            if f.get("wxid"):
                targets.append(("person", f["wxid"], "和「%s」的私聊" % (f.get("name") or ""),
                                f.get("name") or ""))
        for scope, wxid, desc, name in targets:
            mark = memory_store.get_mark(conn, scope, wxid)
            rows = db.get_messages(wxid, limit=400) or []
            newer = [r for r in rows if int(r.get("create_time") or 0) > mark]
            if not newer:
                continue
            newest_ts = max(int(r.get("create_time") or 0) for r in newer)
            idle = now - newest_ts
            if not memory_store.due(len(newer), idle, cfg.get("memory_v2") or {}):
                continue
            nm = mem_name_map(db, wxid, name)
            text, newest = mem_transcript(db, wxid, my, mark, 400, nm)
            if not text:
                memory_store.set_mark(conn, scope, wxid, newest_ts)
                continue

            def _llm(prompt, _desc=desc):
                return ask_model_brief(cfg, "你是一个只会输出记忆卡的整理员。",
                                       prompt, max_tokens=600)

            card = memory_store.build_profile(conn, scope, wxid, desc, text, _llm)
            if card:
                memory_store.set_profile(conn, scope, wxid, card, len(newer))
                memory_store.set_mark(conn, scope, wxid, newest)
                log("记忆卡已更新：%s（%d 条新消息，%d 字）" % (desc, len(newer), len(card)))
                # 同一段对话顺带抽事实（M2）
                mem_extract_facts(cfg, conn, scope, wxid, text)
            else:
                log("记忆卡生成失败：%s（下次再试）" % desc)
    except Exception as e:
        log("记忆维护异常（忽略）：%s" % e)


def build_group_scene(db, wxid: str, my_wxid: str, max_chars: int,
                      max_msgs: int, nameof, my_names: Optional[List[str]] = None,
                      stats: Optional[Dict[str, Any]] = None) -> str:
    """回看这个群刚才的对话，按 CHAT-FORMAT.md 的提示词层格式渲染。

    窗口按**文本量**算：从最新一条往回数，累计到 max_chars 为止。
    群里安静时能覆盖几小时，刷屏时只取最近几百字——跨度自适应。
    """
    max_chars = max(500, int(max_chars or 6000))
    max_msgs = max(20, int(max_msgs or 200))
    try:
        rows = db.get_messages(wxid, limit=min(max_msgs * 3, 1200)) or []
    except Exception as e:
        log("回看失败：%s" % e)
        return ""

    picked = []      # (ts, who, kind, text) 从新往旧收集
    total = 0
    oldest_seq = 0
    img_desc_left = 4          # 整个窗口最多给 4 张图配描述，控制字数
    desc_left = STICKER_DESC_BUDGET
    for r in rows:                     # 库返回是新→旧
        sender, _raw = split_group_content(r.get("content") or "",
                                          r.get("sender_username") or "")
        ts = int(r.get("create_time") or 0)
        who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        kind, body, img_desc_left, desc_left = render_message_body(
            db, wxid, r, nameof, my_names, img_desc_left, desc_left, ts)
        body = (body or "").strip()
        if not body:
            continue
        picked.append((ts, who, kind, body))
        oldest_seq = int(r.get("sort_seq") or 0)
        total += len(body) + len(who or "") + 5
        if total >= max_chars or len(picked) >= max_msgs:
            break
        continue

    picked.reverse()                    # 换成时间正序

    # 同一个人的连续表情合并
    merged = []
    for item in picked:
        ts, who, kind, text = item
        if kind == "emoji" and merged and merged[-1][2] == "emoji" and merged[-1][1] == who:
            prev = merged[-1]
            prev[3].append(text or "")
            continue
        merged.append([ts, who, kind, [text] if kind == "emoji" else text])

    lines = []
    prev_ts = None
    prev_day = None
    for ts, who, kind, payload in merged:
        day = datetime.fromtimestamp(ts).strftime("%m-%d")
        if prev_day is not None and day != prev_day:
            lines.append("=== %s ===" % day)          # 日界线：保证能定位到日期
        elif prev_ts is not None and ts - prev_ts >= SCENE_GAP_S:
            minutes = max(1, int((ts - prev_ts) // 60))
            lines.append("—— %s（隔了 %d 分钟）——"
                         % (datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"), minutes))

        if kind == "emoji":
            descs = [d for d in payload if d]
            n = len(payload)
            if n == 1:
                body = "[表情：%s]" % (descs[0] if descs else "")
                body = body.replace("：]", "]")
            else:
                head = "（%s）" % "/".join(descs[:3]) if descs else ""
                body = "[表情×%d]%s" % (n, head)
        else:
            body = payload
        lines.append("【%s】%s" % (who, body))
        prev_ts = ts
        prev_day = day

    # 按**渲染后**的真实长度裁：从最旧的整行删起，绝不切半句
    omitted = False
    while lines and len("\n".join(lines)) + (20 if omitted else 0) > max_chars - 20:
        lines.pop(0)
        omitted = True
    if omitted and lines:
        lines.insert(0, "（更早的对话已省略）")
    while lines and len("\n".join(lines)) > max_chars:
        lines.pop(0)
    if stats is not None:
        stats["first_seq"] = oldest_seq
        stats["lines"] = len(lines)
        stats["chars"] = len("\n".join(lines))
    body = "\n".join(lines)
    # 记忆卡统一在这里注入（所有走群场景的入口都能吃到）
    try:
        card = mem_group_card(wxid)
        if card:
            if stats is not None:
                stats["card_chars"] = len(card)
            return card + "\n\n" + body
    except Exception as e:
        log("记忆卡注入失败（忽略）：%s" % e)
    return body


def _try_steal(db, cfg, groups, dry_run: bool) -> None:
    """每轮顺手看一眼有没有新表情可收。失败一律忽略，不影响正常回复。"""
    if dry_run:
        return
    if not (cfg.get("steal") or {}).get("enabled"):
        return
    for g in (groups or []):
        try:
            steal_on_arrival(db, cfg, g["wxid"], g["name"])
        except Exception as e:
            log("偷表情异常（忽略）：%s" % e)


def one_round_groups(db, cfg, backend, guard, state, dry_run: bool, from_beginning: bool,
                     my_wxid: str, groups: List[Dict[str, Any]],
                     hist: Optional[History] = None,
                     mem: Optional[Dict[str, List[str]]] = None,
                     member_cache: Optional[Dict[str, Dict[str, str]]] = None,
                     pending: Optional[Dict[str, Dict[str, Any]]] = None) -> int:
    """群聊一轮：先收集（debounce），再统一回复。

    两段式的原因：朋友一句话常拆成三五条，收到第一条就回必然答非所问。
    所以触发后先攒着，等这个人在 wait_s 内不再补话（或总时长到 max_wait_s），
    再把攒下的几句合并、连同"回看 N 分钟"的群场景一起喂给模型。
    """
    limits = cfg.get("limits") or {}
    handled = 0
    if pending is None:
        pending = {}

    dcfg = cfg.get("debounce") or {}
    debounce_on = bool(dcfg.get("enabled", True))
    wait_s = float(dcfg.get("wait_s") or 2.0)
    max_wait = float(dcfg.get("max_wait_s") or 6.0)
    lookback_s = float(dcfg.get("lookback_s") or 300)
    scene_max = int((cfg.get("context") or {}).get("group_max_messages") or 400)
    scene_max = max(50, scene_max)
    scene_chars = int((cfg.get("context") or {}).get("group_max_chars") or 6000)

    nmap: Dict[str, str] = {}

    def nameof(w: str) -> str:
        """群成员 wxid → 显示名。nickname_map 只取一次，避免反复开 contact.db。"""
        nonlocal nmap
        if w and not nmap:
            try:
                nmap = db.nickname_map() or {}
            except Exception:
                nmap = {}
        return nmap.get(w) or (short(w) if w else "有人")

    # ---- 第一段：收消息，攒请求 ----
    for g in groups:
        wxid, name, entry = g["wxid"], g["name"], g["entry"]
        since = 0 if from_beginning else int(state.get(wxid, 0))
        try:
            rows = db.get_new_messages(wxid, since_seq=since, limit=80) or []
        except Exception as e:
            log("读群消息失败 %s：%s" % (name, e))
            continue

        def consume(s: int, _w: str = wxid) -> None:
            state[_w] = max(state.get(_w, 0), s)

        for r in rows:
            seq = int(r.get("sort_seq") or 0)

            norm = normalize_quote_row(r)
            if norm is None:
                consume(seq)
                continue
            r = norm
            sender_id = r.get("sender_id")
            sender, text = split_group_content(r.get("content") or "",
                                               r.get("sender_username") or "")
            text = cap_incoming(text, int(limits.get("max_incoming_chars") or 800))
            if (bool(my_wxid) and sender == my_wxid) or (not sender and sender_id == 2):
                consume(seq)                       # 自己发的
                continue
            if not text:
                consume(seq)
                continue

            # 关键：如果这个人已经有待回复，后面的话**不管有没有 @** 都算补充。
            # 现实中补充一句不会重新 @ 一次，只判断 group_fired 会漏掉全部后续碎片。
            now = time.time()
            # 解析不出发送者时给个唯一 key，避免把两个人的话合并成一条
            key = "%s|%s" % (wxid, sender or ("#%d" % seq))
            ent = pending.get(key)
            if ent is not None and (now - ent["first"]) <= max_wait:
                ent["texts"].append(clean_for_model(text, entry) or text)
                ent["last"] = now
                consume(seq)
                continue

            # 没被叫到：静默，但仍记进上下文 —— 这正是"它一直在群里"的来源
            if not group_fired(text, entry):
                if hist is not None and hist.enabled:
                    hist.add(wxid, "user", text, name=nameof(sender))
                consume(seq)
                continue

            # 被叫到：先攒着，不马上回
            ent = pending.get(key)
            if ent is None:
                ent = {"g": g, "asker": nameof(sender), "asker_wxid": sender, "texts": [],
                       "first": now, "last": now, "tries": 0}
                pending[key] = ent
            ent["texts"].append(clean_for_model(text, entry) or text)
            ent["last"] = now
            if len(ent["texts"]) == 1:
                log("群 %s：%s 叫了，先等 %.0fs 看他有没有补充"
                    % (name, ent["asker"], wait_s if debounce_on else 0))
            consume(seq)

    # ---- 第二段：到期的合并回复 ----
    for key in list(pending.keys()):
        ent = pending[key]
        g, entry = ent["g"], ent["g"]["entry"]
        wxid, name = g["wxid"], g["name"]
        now = time.time()

        # 还在攒：对方可能正打字
        if debounce_on and (now - ent["last"] < wait_s) and (now - ent["first"] < max_wait):
            continue

        if in_quiet_hours(limits) and not entry.get("allow_quiet"):
            log("群 %s：静默时段，攒下的 %d 条不回了" % (name, len(ent["texts"])))
            del pending[key]
            continue

        raw_interval = entry.get("min_interval_s")
        min_interval = 60.0 if raw_interval is None else float(raw_interval)
        if not guard.group_allowed(wxid, min_interval):
            # 不是丢弃，是把计时重置一下，等冷却过去再发
            ent["first"] = now
            continue

        merged = " ".join(ent["texts"]).strip()
        if not merged:
            del pending[key]
            continue
        asker = ent["asker"]

        ctx: Dict[str, Any] = {
            "kind": "group", "who": name, "wxid": wxid, "asker": asker,
            "system": (cfg.get("backend") or {}).get("group_system"),
            # 群聊也要给表情菜单：否则模型乱报编号，_MENU_SNAPSHOT 也没有键
            "stickers": sticker_menu(),
        }
        _REPLY_SNAPSHOT.clear()
        _REPLY_SNAPSHOT.update(_MENU_SNAPSHOT)
        if mem:
            mcfg = cfg.get("memory") or {}
            ctx["memory"] = memory_for(mem, asker, int(mcfg.get("max_chars") or 800))

        # 附件：先算「时间 + 指认的发送者」，再按约束生成索引 + 点名最相关的一条
        q_for_prompt = merged
        try:
            name2wx: Dict[str, str] = {}
            for nm_, wx_ in group_member_index(db, wxid, {}).items():
                name2wx[nm_] = wx_
            for wx_, nm_ in mem_nick(db).items():
                name2wx.setdefault(nm_, wx_)
            asker_wxid = ent.get("asker_wxid") or ""
            only, why = parse_sender_constraint(merged or "", asker, name2wx, asker_wxid)

            idx = attachment_index(db, wxid, nameof, my_wxid, 12, None, only)
            if idx:
                ctx["attachments"] = idx

            target, note = parse_relative_time(merged or "")
            if target:
                q_for_prompt = merged + note
                hit = find_attachment_near(db, wxid, target, nameof, my_wxid,
                                           asker, only_sender=only)
                if hit:
                    ctx["attach_hint"] = hit
                    log("附件定位：%s%s → %s"
                        % (note.strip("（）"), ("（%s）" % why) if why else "", hit))
                elif only:
                    log("附件定位：按「%s」过滤后没找到 %s 附近的附件，不插提示"
                        % (why, target.strftime("%m-%d %H:%M")))
            elif only:
                fb = recent_own_attachments(db, wxid, only, nameof, my_wxid, 2)
                if fb:
                    ctx["attach_hint"] = fb
                    log("附件兜底：没提到时间，按「%s」给最近两条" % why)
        except Exception as e:
            log("附件处理异常（忽略）：%s" % e)

        try:
            vcfg = cfg.get("vision") or {}
            if vcfg.get("enabled", True):
                blk = recent_images_block(
                    db, wxid, now - float(vcfg.get("lookback_s") or 300),
                    int(vcfg.get("max_recent_images") or 2), nameof)
                if blk:
                    ctx["images"] = blk
        except Exception as e:
            log("看图异常（忽略）：%s" % e)

        try:
            svcfg = cfg.get("sticker_vision") or {}
            if svcfg.get("enabled", True):
                sblk = sticker_block(db, cfg, wxid,
                                     now - float(svcfg.get("lookback_s") or 300),
                                     nameof, int(svcfg.get("max_recent") or 3))
                if sblk:
                    ctx["stickers_seen"] = sblk
        except Exception as e:
            log("表情上下文异常（忽略）：%s" % e)

        try:
            recalled = recall_block(db, cfg, wxid, merged, my_wxid,
                                    member_cache, self_names(entry))
        except Exception as e:
            recalled = ""
            log("检索异常（忽略）：%s" % e)
        if recalled:
            ctx["recall"] = recalled
            log("群 %s：翻到 %d 字相关历史" % (name, len(recalled)))

        # 上下文：优先用"按时间回看"，拿不到再退回 history 窗口
        scene_stats: Dict[str, Any] = {}
        scene = build_group_scene(db, wxid, my_wxid, scene_chars, scene_max,
                                  nameof, self_names(entry), scene_stats)
        if scene:
            log("群场景 %d 字 / %d 行（上限 %d）"
                % (scene_stats.get("chars") or 0, scene_stats.get("lines") or 0, scene_chars))
            try:
                import rolling_summary as rs
                line = rs.summary_line(wxid)
                if line:
                    # 有摘要就不要"已省略"占位行，否则会重复表达同一件事
                    scene = scene.replace("（更早的对话已省略）\n", "", 1)
                    scene = line + "\n" + scene     # 摘要层放顶部（CHAT-FORMAT）
            except Exception as e:
                log("摘要读取失败（忽略）：%s" % e)
        # 事实检索：用当轮最后一句当查询
        _facts = mem_facts_block("group", wxid, (q_for_prompt or "")[:200])
        if _facts:
            scene = ((scene or "") + "\n\n" + _facts).strip()
            log("记忆注入（群）：%d 字事实" % len(_facts))
        if scene:
            ctx["messages"] = [{"role": "user", "content":
                                "【群里刚才的对话】\n%s\n\n【现在 @ 你的是 %s】\n%s"
                                % (scene, asker, q_for_prompt)}]
        elif hist is not None and hist.enabled:
            ctx["messages"] = hist.window(wxid, hist.group_msgs) + [
                {"role": "user", "content": "%s：%s" % (asker, merged)}]

        try:
            reply = backend.reply(merged, ctx)
        except Exception as e:
            ent["tries"] += 1
            if "BALANCE:" in str(e):
                log("！！ 余额不足，无法生成群回复 —— 请充值后重启。放弃这 %d 条"
                    % len(ent["texts"]))
                del pending[key]
                consume(max(ent["seqs"]) if ent.get("seqs") else seq)
                continue
            ent["last"] = now                  # 下一轮再试，别空转
            if ent["tries"] >= 3:
                log("群 %s：连续 3 次生成失败，放弃这 %d 条（%s）"
                    % (name, len(ent["texts"]), e))
                del pending[key]
            else:
                log("群 %s 生成回复失败（第 %d 次，稍后重试）：%s"
                    % (name, ent["tries"], e))
            continue

        if not reply:
            del pending[key]
            continue
        reply = guard_reply(reply, cfg, entry)
        raw_reply = reply
        reply = strip_emoji_tag(reply)
        if not reply:
            if EMOJI_TAG.search(raw_reply or ""):
                maybe_send_sticker(db, wxid, name, cfg, raw_reply, merged)
            consume(seq)
            continue
        incoming = "「%s：%s」" % (asker, merged[:36])

        if dry_run:
            log("[dry-run][群] %s %s → 回「%s」" % (name, incoming, reply))
            ok = True
        else:
            pre = entry.get("pre_send_delay_s") or [0.6, 1.5]
            time.sleep(random.uniform(float(pre[0]), float(pre[1])))
            at_name = (asker or "").strip() if entry.get("reply_with_at") else None
            bcfg = bubble_cfg(entry, cfg)
            ok = send_multi(
                lambda t: send_group(db, name, t, at_name if at_name else None, wxid),
                reply, bcfg)
            log("[live][群] %s %s → 回「%s」（%s）%s"
                % (name, incoming, reply, "成功" if ok else "失败",
                   "（@%s）" % at_name if at_name else ""))

        if ok:
            guard.mark_group_sent(wxid)
            guard.note_sent(wxid)
            handled += 1
            maybe_send_sticker(db, wxid, name, cfg, raw_reply, merged)
            try:
                import rolling_summary as rs
                rs.maybe_update(db, cfg, backend, wxid, name,
                                int(scene_stats.get("first_seq") or 0),
                                nameof, my_wxid, log)
            except Exception as e:
                log("摘要更新异常（忽略）：%s" % e)
            if hist is not None and hist.enabled:
                hist.add(wxid, "user", merged, name=asker)
                hist.add(wxid, "assistant", reply)
            del pending[key]
        else:
            ent["tries"] += 1
            ent["last"] = now
            if ent["tries"] >= 3:
                log("群 %s：连续 3 次发送失败，丢弃" % name)
                del pending[key]

    return handled


_FAIL_STREAK = 0


def _send_foreground(db, name: str, text: str, at_name: Optional[str] = None) -> bool:
    from wechatauto import Chat
    try:
        chat = Chat(who=name, db=db)
        if not gui_is_wechat(chat):
            return False
        if at_name:
            r = chat.SendMsg(text, who=name, at=at_name)
        else:
            r = chat.SendMsg(text, who=name)
        return bool(getattr(r, "is_success", False))
    except Exception as e:
        log("前台发送异常：%s" % e)
        return False


_UIA = None


def reset_uia() -> None:
    """丢弃 UIA 单例，下次调用会重新建树。

    窗口尺寸/DPI 变化后必须重置：UIA 元素矩形是按旧布局算的，不重置会整体错位。
    """
    global _UIA
    _UIA = None


ALLOW_ACTIVATE = False     # 只有真的要模拟输入时才允许抢前台


def set_focus_mode(on: bool) -> None:
    """打开/关闭"允许抢前台"。只在模拟输入前后短暂打开。"""
    global ALLOW_ACTIVATE
    ALLOW_ACTIVATE = bool(on)


def _install_activate_guard() -> None:
    """把库的窗口激活改造成"按需"——默认不抢前台。

    读界面（当前会话、会话列表、元素坐标）不需要焦点；
    只有投递点击/按键时，Qt 才要求窗口是激活态。
    """
    try:
        from wechatauto.uia_driver import WeChatUIA
        if getattr(WeChatUIA, "_focus_guarded", False):
            return
        orig = WeChatUIA._activate

        def guarded(self, w):
            if not ALLOW_ACTIVATE:
                return None
            try:
                return orig(self, w)
            except Exception:
                return None

        WeChatUIA._activate = guarded
        WeChatUIA._focus_guarded = True
        log("已启用按需激活：读界面不再抢前台")
    except Exception as e:
        log("激活守卫安装失败（忽略）：%s" % e)


def get_uia():
    """UIA 驱动单例。它读的是**控件树**，不依赖窗口有没有重绘——
    这正是替代"截图 OCR 核对标题"的关键。"""
    global _UIA
    if _UIA is None:
        from wechatauto.uia_driver import WeChatUIA
        _install_activate_guard()
        _UIA = WeChatUIA()
    return _UIA


def _impl_uia_current_chat(ensure: bool = True) -> str:
    """读当前聊天标题。读不到时先 ensure_window() 唤醒一次——
    窗口没激活/没打开会话时 current_chat() 会返回空，这一步能恢复（实测有效）。"""
    try:
        u = get_uia()
        cur = (u.current_chat() or "").strip()
        if not cur and ensure:
            try:
                u.ensure_window()
            except Exception:
                pass
            time.sleep(0.4)
            cur = (u.current_chat() or "").strip()
        return cur
    except Exception:
        return ""


_DB_CACHE = []
_STEAL_STATE: Dict[str, Any] = {"last": 0.0, "n": 0,
                               "seen_seq": {}, "fail": {}, "verdict": {}}


def _shared_db():
    if not _DB_CACHE:
        from wechatauto import WeChatDB
        _DB_CACHE.append(WeChatDB())
    return _DB_CACHE[0]


_BG = None


def get_bg():
    """懒加载后台发送器（wxbg.BgWeChat）。它持有窗口句柄和布局参数。"""
    global _BG
    if _BG is None:
        from wxbg import BgWeChat
        _BG = BgWeChat(verbose=True)
        atexit.register(_release_bg)
    return _BG


def _release_bg():
    global _BG
    if _BG is not None:
        try:
            _BG.dismiss_overlays()
        except Exception:
            pass
        _BG = None


def session_index_from_db(name: str, wxid: Optional[str] = None) -> Optional[int]:
    """按 wxid（首选）或名字，从会话列表算出它排第几行。"""
    try:
        for i, s in enumerate(_shared_db().get_sessions(limit=30) or []):
            if wxid and s.get("username") == wxid:
                return i
    except Exception as e:
        log("读会话列表失败：%s" % e)
    return None


CHAT_TAB_ICON = (42, 139)      # 左侧图标栏第一个图标（聊天）


def _impl_uia_session_items():
    """当前 UIA 树里能看到的会话项名字列表。"""
    names = []
    try:
        u = get_uia()
        if not u.ensure_window():
            return names
        root = u._win

        def walk(el, d=0):
            if d > 26:
                return
            try:
                aid = el.AutomationId or ""
            except Exception:
                return
            if aid.startswith("session_item_"):
                names.append(aid[len("session_item_"):])
            try:
                for k in el.GetChildren():
                    walk(k, d + 1)
            except Exception:
                pass

        walk(root)
    except Exception:
        pass
    return names


_UIA_HEAL = {"last": 0.0, "fails": 0}


user32 = ctypes.windll.user32


UIA_STATE_PATH = os.path.join(HERE, "uia_state.json")
_UIA_WATCH = {"ok": True, "last_check": 0.0, "fails": 0, "last_heal": 0.0,
              "attempts": 0, "gave_up": False}


def _write_uia_state() -> None:
    try:
        tmp = UIA_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            payload = {"ok": _UIA_WATCH["ok"],
                       "fails": _UIA_WATCH["fails"],
                       "gave_up": _UIA_WATCH.get("gave_up", False),
                       "attempts": _UIA_WATCH.get("attempts", 0),
                       "at": datetime.now().strftime("%F %T")}
            try:
                import uia_guard
                st = uia_guard.snapshot()
                payload["stats"] = {
                    "calls": st.get("calls"), "errors": st.get("errors"),
                    "slow": st.get("slow"),
                    "max_call_ms": st.get("max_call_ms"),
                    "lock_wait_ms": st.get("lock_wait_ms"),
                    "last_hresult": st.get("last_hresult"),
                    "last_error": st.get("last_error"),
                }
            except Exception:
                pass
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, UIA_STATE_PATH)
    except Exception:
        pass


def _impl_uia_session_items_quiet():
    """只读探测：不激活窗口、不唤醒、不抢前台。

    给"健康看门狗"用——它每 90 秒跑一次，绝不能让微信跳出来。
    """
    names = []
    try:
        import wxwin
        h = wxwin.find_wechat()
        if not h:
            return names
        import uiautomation as auto
        root = auto.ControlFromHandle(h)
        if root is None:
            return names

        def walk(el, d=0):
            if d > 26:
                return
            try:
                aid = el.AutomationId or ""
            except Exception:
                return
            if aid.startswith("session_item_"):
                names.append(aid[len("session_item_"):])
            try:
                for k in el.GetChildren():
                    walk(k, d + 1)
            except Exception:
                pass

        walk(root)
    except Exception:
        pass
    return names


BACKOFF = [60, 300, 900]       # 自愈间隔：1 分钟 → 5 分钟 → 15 分钟


def uia_repair_ladder(force: bool = False) -> bool:
    """UIA 树挂了时的修复阶梯。返回是否修好。

    只在"看门狗已经判定挂了"时调用，内部有节流，不会反复折腾。
    """
    now = time.time()
    if not force and now - float(_UIA_WATCH.get("last_ladder") or 0) < 180:
        return bool(_UIA_WATCH.get("ok"))
    _UIA_WATCH["last_ladder"] = now

    rp = ((load_config().get("wechat") or {}).get("repair") or {})
    max_step = int(rp.get("max_step") or 3)

    # 1) 热激活 + 重读
    try:
        reset_uia()
        u = get_uia()
        try:
            u.ensure_window()
        except Exception:
            pass
        time.sleep(1.2)
        if uia_session_items():
            log("UIA 修复成功：热激活（第 1 步）")
            return True
    except Exception as e:
        log("修复第 1 步异常（忽略）：%s" % e)
    if max_step < 2:
        return False

    # 2) 切两个 tab 强迫重绘（PostMessage，不抢焦点）
    try:
        bg = get_bg()
        for name in ("通讯录", "微信"):
            c = bg.uia_screen_center(lambda e, n=name: (e.Name or "") == n)
            if c:
                bg.click_screen(*c)
                time.sleep(0.8)
        time.sleep(1.0)
        if uia_session_items():
            log("UIA 修复成功：切换 tab 重绘（第 2 步）")
            return True
    except Exception as e:
        log("修复第 2 步异常（忽略）：%s" % e)
    if max_step < 3:
        return False

    # 3) 最小化 → 还原（会短暂抢焦点）
    try:
        import wxwin
        h = wxwin.find_wechat()
        if h:
            ctypes.windll.user32.ShowWindow(h, 6)   # MINIMIZE
            time.sleep(1.0)
            ctypes.windll.user32.ShowWindow(h, 9)   # RESTORE
            time.sleep(1.5)
        if uia_session_items():
            log("UIA 修复成功：最小化还原（第 3 步）")
            return True
    except Exception as e:
        log("修复第 3 步异常（忽略）：%s" % e)

    if not rp.get("auto_restart"):
        log("UIA 修复：前三步都失败；自动重启微信未开启（wechat.repair.auto_restart=false）")
        return False

    # 4) 重启微信（可选）
    try:
        if not uia_user_idle(rp.get("idle_s") or 300):
            log("UIA 修复：检测到你正在操作，暂不重启微信（空闲后再试）")
            return False
        exe = ""
        try:
            exe = ((load_config().get("wechat") or {}).get("exe_path") or "")
        except Exception:
            pass
        if not exe or not os.path.isfile(exe):
            log("UIA 修复：没有配置微信路径，无法自动重启")
            return False
        log("UIA 修复：准备重启微信（会自动重新登录；若停在扫码界面请手动扫）")
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() in ("weixin.exe", "wechat.exe"):
                try:
                    p.kill()
                except Exception:
                    pass
        time.sleep(6)
        import subprocess
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        for _ in range(40):
            time.sleep(1.5)
            if uia_session_items():
                log("UIA 修复成功：重启微信（第 4 步）")
                return True
        log("UIA 修复：重启后仍读不到 —— 可能停在扫码界面，请手动登录")
        return False
    except Exception as e:
        log("修复第 4 步异常：%s" % e)
        return False


def uia_user_idle(seconds: int) -> bool:
    """最近 N 秒没有键鼠输入（用 GetLastInputInfo）。"""
    try:
        import ctypes
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
        tick = ctypes.windll.kernel32.GetTickCount()
        idle_ms = tick - li.dwTime
        return idle_ms >= seconds * 1000
    except Exception:
        return True


def uia_healthy(force: bool = False, interval: float = 300.0) -> bool:
    """轻量健康探测 + 退避自愈。返回当前是否健康。

    P0：默认 300 秒（原 90 秒）；用户正在操作时不探（避免干扰）；
        已有 UIA 调用在进行时不插队。
    """
    now = time.time()
    if not force and now - _UIA_WATCH["last_check"] < interval:
        return _UIA_WATCH["ok"]

    # 已有 UIA 调用在进行 → 不插队
    try:
        import uia_guard
        if not force and uia_guard.busy():
            return _UIA_WATCH["ok"]
    except Exception:
        pass

    # 用户最近 60 秒有输入 → 只在"已经坏了"的时候才探（坏了要修）
    try:
        if not force and _UIA_WATCH["ok"] and not uia_user_idle(60):
            return _UIA_WATCH["ok"]
    except Exception:
        pass

    _UIA_WATCH["last_check"] = now

    try:
        # 用库自己的扫描：只读探针在树上不可靠（恒返回 0，会误判坏了）
        # 抢前台的问题已由按需激活守卫解决，这里读是安全的
        names = uia_session_items()
    except Exception:
        names = []

    if names:
        if not _UIA_WATCH["ok"]:
            log("UIA 已恢复正常（可见 %d 个会话）" % len(names))
        _UIA_WATCH["ok"] = True
        _UIA_WATCH["fails"] = 0
        _UIA_WATCH["attempts"] = 0             # 恢复即清零退避
        _UIA_WATCH["gave_up"] = False
        _write_uia_state()
        return True

    # ---- 探测失败 ----
    _UIA_WATCH["fails"] += 1
    attempts = _UIA_WATCH.get("attempts", 0)

    if _UIA_WATCH.get("gave_up"):
        _UIA_WATCH["ok"] = False
        _write_uia_state()
        return False

    wait = BACKOFF[attempts] if attempts < len(BACKOFF) else None
    if wait is None:
        # 退避用尽：停止自动重试，只留提示
        _UIA_WATCH["gave_up"] = True
        _UIA_WATCH["ok"] = False
        log("UIA 自愈已放弃（连续 %d 次失败）：请手动重启微信客户端，"
            "或重启 bot 后再试" % _UIA_WATCH["fails"])
        _write_uia_state()
        return False

    if now - _UIA_WATCH["last_heal"] >= wait:
        _UIA_WATCH["last_heal"] = now
        _UIA_WATCH["attempts"] = attempts + 1
        # 先走修复阶梯（比单次 uia_heal 更有层次），成功就直接返回
        try:
            if uia_repair_ladder():
                _UIA_WATCH["ok"] = True
                _UIA_WATCH["fails"] = 0
                _UIA_WATCH["attempts"] = 0
                _write_uia_state()
                return True
        except Exception:
            pass
        log("UIA 不健康，第 %d 次自愈（下次间隔 %s 秒）"
            % (_UIA_WATCH["attempts"], BACKOFF[_UIA_WATCH["attempts"]]
               if _UIA_WATCH["attempts"] < len(BACKOFF) else "不再重试"))
        try:
            _UIA_WATCH["ok"] = bool(uia_heal())
        except Exception:
            _UIA_WATCH["ok"] = False
        if _UIA_WATCH["ok"]:
            log("UIA 自愈成功")
            _UIA_WATCH["fails"] = 0
            _UIA_WATCH["attempts"] = 0
    else:
        _UIA_WATCH["ok"] = False

    _write_uia_state()
    return _UIA_WATCH["ok"]


def uia_reset_watch() -> None:
    """重启 bot / 重启微信后调用：清空退避状态。"""
    _UIA_WATCH.update({"ok": True, "fails": 0, "attempts": 0,
                       "gave_up": False, "last_heal": 0.0})
    _write_uia_state()


def uia_should_block() -> bool:
    """发送前调用：UIA 明确不健康时快速失败（force 探测一次）。"""
    return not uia_healthy(force=True, interval=30.0)


def _impl_uia_heal(cfg=None) -> bool:
    """尝试恢复 UIA 树。返回是否恢复成功。带冷却，避免死循环里反复折腾。"""
    now = time.time()
    if now - float(_UIA_HEAL["last"]) < 20:      # 20 秒内只尝试一次
        return False
    _UIA_HEAL["last"] = now
    log("UIA 树不可用，尝试自愈…")
    h = None
    try:
        import wxwin
        h = wxwin.find_wechat()
    except Exception:
        pass
    # ① 热激活
    try:
        if h:
            from wechatauto import accessibility as acc
            acc.AccessibilityGate._hot_activate_accessibility(h)
            time.sleep(0.8)
            reset_uia()
            time.sleep(0.4)
            if len(uia_session_items()) >= 1:
                _UIA_HEAL["fails"] = 0
                log("自愈成功（热激活）")
                return True
    except Exception as e:
        log("热激活失败：%s" % e)
    # ② 最小化→还原
    try:
        if h:
            user32.ShowWindow(h, 6)
            time.sleep(1.0)
            user32.ShowWindow(h, 9)
            time.sleep(1.2)
            reset_uia()
            time.sleep(0.5)
            if len(uia_session_items()) >= 1:
                _UIA_HEAL["fails"] = 0
                log("自愈成功（重建窗口）")
                return True
    except Exception as e:
        log("重建窗口失败：%s" % e)
    _UIA_HEAL["fails"] += 1
    log("自愈失败（第 %d 次）：微信的无障碍树没有暴露出来。"
        "建议手动重启微信客户端；在此之前不会发送任何消息。" % _UIA_HEAL["fails"])
    return False


def ensure_chat_tab(force: bool = False) -> bool:
    """确保停在聊天列表页。返回是否可见多个会话。"""
    names = uia_session_items()
    if len(names) >= 2 and not force:
        return True
    try:
        bg = get_bg()
        bg.activate()
        bg.click_chat_tab()            # UIA 优先，读不到才退回固定坐标
        time.sleep(0.9)
    except Exception as e:
        log("回聊天页失败：%s" % e)
        return False
    names = uia_session_items()
    if len(names) < 2:
        # 树没暴露出来：先自愈一次再判断
        uia_heal()
        names = uia_session_items()
        if len(names) < 2:
            log("回聊天页后仍只看到 %d 个会话" % len(names))
    return len(names) >= 2


def _impl_uia_find_session(name: str):
    """在 UIA 树里按名字找会话项，返回元素或 None。"""
    try:
        u = get_uia()
        if not u.ensure_window():
            return None
        from wechatauto.uia_driver import _find_by
        root = u._win

        def pred(el):
            try:
                aid = el.AutomationId or ""
                nm = el.Name or ""
            except Exception:
                return False
            if not aid.startswith("session_item_"):
                return False
            return (name in aid) or (nm and name in nm)

        return _find_by(root, pred, max_depth=24)
    except Exception as e:
        log("UIA 找会话失败：%s" % e)
        return None


def _impl_uia_click_session(name: str) -> bool:
    """按名字点会话项。返回是否成功点到（不代表已切换完成）。"""
    el = uia_find_session(name)
    if el is None:
        return False
    try:
        # UIA 的 BoundingRectangle 是**屏幕坐标**，我们的点击要客户区坐标
        r = el.BoundingRectangle
        sx = (r.left + r.right) // 2
        sy = (r.top + r.bottom) // 2
        bg = get_bg()
        wr = bg._rect()
        cx_off, cy_off = bg._client()[0], bg._client()[1]
        cx = int(sx - wr[0] - cx_off)
        cy = int(sy - wr[1] - cy_off)
        if cx < 0 or cy < 0 or cx > 2000 or cy > 2000:
            log("会话项坐标异常（%d,%d），放弃" % (cx, cy))
            return False
        bg.activate()
        bg.click_px(cx, cy)
        return True
    except Exception as e:
        log("点击会话项失败：%s" % e)
        return False


PANEL_OPEN = False      # 表情面板是否还开着（复用可减少开合次数）


def close_panel_if_open() -> None:
    """切会话前把表情面板收掉：它挡在聊天区，切走也容易出状态问题。"""
    global PANEL_OPEN
    if not PANEL_OPEN:
        return
    try:
        bg = get_bg()
        if bg.find_emoji_panel():
            bg.close_emoji_panel()
    except Exception:
        pass
    PANEL_OPEN = False


def _impl_uia_open_chat(name: str, tries: int = 2, wxid: Optional[str] = None) -> bool:
    close_panel_if_open()
    """切到目标会话。

    失败过的三种情况：
      * 窗口里没有打开的会话时 current_chat() 返回 None → 先 ensure_window()
      * 窗口被挪到屏幕外 → UIA 读不到 → 先 ensure_onscreen()
      * 库的 open_chat 偶发返回 False 但其实切成功了 → 以 current_chat() 为准
    """
    try:
        get_bg().ensure_onscreen()
    except Exception:
        pass

    cur = uia_current_chat()
    if cur and (name in cur or cur in name):
        return True
    if not cur:
        # 没有活动会话：唤醒窗口并把界面带回聊天页
        try:
            u = get_uia()
            u.ensure_window()
            u.back_to_chat_tab()
            time.sleep(0.5)
            cur = uia_current_chat()
            if cur and (name in cur or cur in name):
                return True
        except Exception as e:
            log("唤醒窗口失败：%s" % e)

    # ⓿ UIA 明确坏了就快速失败（不要每条都白等几十秒）
    if uia_should_block():
        log("UIA 不可用，跳过本次发送（避免长时间等待）")
        return False

    # ① 必须在聊天列表页，否则按名字找不到会话项
    ensure_chat_tab()

    # ② 按名字点（实测 1.3~1.7 秒，且不依赖行号顺序）
    try:
        for attempt in range(2):
            if attempt > 0:
                ensure_chat_tab(force=True)
            if uia_click_session(name):
                time.sleep(0.45)
                cur = uia_current_chat()
                if cur and (name in cur or cur in name):
                    return True
            time.sleep(0.25)
    except Exception as e:
        log("按名字切会话异常：%s" % e)

    u = get_uia()
    for i in range(1, tries + 1):
        try:
            u.open_chat(name)
        except Exception as e:
            log("UIA 切会话异常（第 %d 次）：%s" % (i, e))
        time.sleep(0.6)
        cur = uia_current_chat()
        if cur and (name in cur or cur in name):
            return True
        log("UIA 切会话第 %d 次：当前「%s」，目标「%s」" % (i, cur, name))
    return False


def send_via_uia(db, name: str, text: str) -> bool:
    """UIA 发送：切会话（校验标题）→ send_text。全程不需要窗口可见。"""
    try:
        if not get_uia().is_running():
            return False
        if not uia_open_chat(name):
            return False
        return bool(get_uia().send_text(text))
    except Exception as e:
        log("UIA 发送异常：%s" % e)
        return False


def send_emoji_via_uia(db, name: str, index: int) -> bool:
    """发第 index 个收藏表情：UIA 切会话（可靠）→ 坐标点表情面板 → 数据库确认。"""
    try:
        if not get_uia().is_running():
            return False
        if not uia_open_chat(name):
            log("表情包：切会话失败，放弃（避免发错聊天）")
            return False
        bg = get_bg()
        bg.ensure_onscreen()      # 别挪出屏幕：UIA 和面板点击都要求窗口可见
        bg.send_emoji(index)
        return True
    except Exception as e:
        log("发表情异常：%s" % e)
        return False


EMOJI_TAG = re.compile(r"<emoji:([^>]{1,40})>")   # 数字（旧）或标签（新）


HTML_TAG = re.compile(r"</?(?:br|p|div|span|b|i|u|strong|em|hr)\s*/?>", re.I)
HTML_LIKE = re.compile(r"<[^>\n]{0,24}>")


def strip_emoji_tag(text: str) -> str:
    """去掉 <emoji:N> 标记，并清掉模型偶尔吐出来的 HTML 标签。

    实测模型会把换行写成 <br>，而分条逻辑按换行切句，结果 <br> 被当成
    独立一条消息发了出去——用户收到一条光秃秃的 "<br>"。
    """
    t = EMOJI_TAG.sub("", text or "")
    t = HTML_TAG.sub(" ", t)
    t = HTML_LIKE.sub("", t)
    return t.strip()


STICKER_ASK_RE = re.compile(r"(发|来|给|整|搞|甩|丢)[^。！？\n]{0,6}(表情|图)")
STICKER_ASK = ("表情包", "逗图", "斗图")

def _clipboard_put_image(path: str) -> bool:
    """把图片放进剪贴板（CF_DIB）。需要 pywin32。"""
    try:
        from PIL import Image
        import win32clipboard
        import win32con
    except Exception:
        return False
    try:
        img = Image.open(path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "BMP")
        data = buf.getvalue()[14:]          # 去掉 BMP 头，只要 DIB
        buf.close()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log("放剪贴板失败：%s" % e)
        return False


def send_image_paste(db, wxid: str, name: str, image_path: str,
                     timeout: float = 8.0) -> bool:
    """用剪贴板粘贴的方式发一张图片。

    返回 True 表示数据库里确实出现了新的图片消息（不是"看起来点了"）。
    """
    if not os.path.isfile(image_path):
        log("粘贴发送失败：文件不存在 %s" % image_path)
        return False
    if not _clipboard_put_image(image_path):
        log("粘贴发送失败：剪贴板不可用（缺 pywin32？）")
        return False

    # 记水位
    seq0 = 0
    try:
        rows = db.get_messages(wxid, limit=1) or []
        seq0 = int(rows[0]["sort_seq"]) if rows else 0
    except Exception:
        seq0 = 0

    if not uia_open_chat(name, wxid=wxid):
        log("粘贴发送失败：切会话失败")
        return False
    bg = get_bg()
    bg.click_input()
    time.sleep(0.25)
    bg.paste()                      # Ctrl+V
    time.sleep(1.2)                 # 等图片进入输入框
    bg.press_enter()
    time.sleep(0.4)

    # 回读确认：出现新的图片消息才算成功
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.6)
        try:
            rows = db.get_messages(wxid, limit=6) or []
        except Exception:
            continue
        for r in rows:
            if int(r.get("sort_seq") or 0) > seq0 and r.get("type") == "图片":
                log("粘贴发送确认：新图片消息 seq=%s" % r.get("sort_seq"))
                return True
    log("粘贴发送未确认：%.0f 秒内没看到新图片消息" % timeout)
    return False


def match_tag(items, want: str) -> str:
    """把模型写的标签匹配到具体表情，返回 md5（空串=没匹配上）。"""
    want = (want or "").strip().strip("「」『』\"' ")
    if not want:
        return ""
    cands = [(it.get("tag") or "", (it.get("md5") or "").lower()) for it in items]
    # ① 完全相等
    for tag, md5 in cands:
        if tag == want:
            return md5
    # ② 互相包含
    for tag, md5 in cands:
        if want and (want in tag or tag in want):
            return md5
    # ③ 最大字符重合
    best, best_md5 = 0, ""
    for tag, md5 in cands:
        score = len(set(want) & set(tag))
        if score > best:
            best, best_md5 = score, md5
    if best >= max(2, len(want) // 3):
        return best_md5
    return ""


def maybe_send_sticker(db, wxid: str, name: str, cfg, reply: str,
                       incoming: str = "") -> bool:
    """发一个收藏表情。

    优先级：模型显式 <emoji:N> → 用户明说要表情包（强制发）→ 按概率随机。
    不能全指望模型自觉：提示词里写了约定，它照样可能只回文字。
    """
    ec = cfg.get("emoji") or {}
    if not ec.get("enabled"):
        return False
    idx = None
    tag_want = ""
    m = EMOJI_TAG.search(reply or "")
    if m:
        raw = (m.group(1) or "").strip()
        if raw.isdigit():
            idx = int(raw)
        else:
            tag_want = raw
    elif incoming and (any(k in incoming for k in STICKER_ASK)
                       or STICKER_ASK_RE.search(incoming)):
        # 用户点名要表情但模型没给标签：先按用户原话去匹配标签，匹配不到再随机
        guess = match_tag(load_stickers(), incoming) if incoming else ""
        if guess:
            try:
                import sticker_match
                ok = sticker_match.send_sticker_by_md5(db, wxid, name, guess)
                if ok:
                    log("用户要表情包，按描述匹配到 %s 发出" % guess[:8])
                    return True
            except Exception as e:
                log("按描述发表情失败：%s" % e)
        idx = pick_sticker_by_love()
        if idx is None:
            idx = random.randint(0, int(ec.get("max_index") or 14))
        log("用户要表情包，强制发第 %d 个" % idx)
    elif random.random() < float(ec.get("chance") or 0.25):
        idx = pick_sticker_by_love()
        if idx is None:
            idx = random.randint(0, int(ec.get("max_index") or 14))
    if tag_want:
        try:
            items_all = load_stickers()
            md5 = match_tag(items_all, tag_want)
            if not md5:
                log("表情标签「%s」没匹配上，跳过" % tag_want[:20])
                return False
            import sticker_match
            ok = sticker_match.send_sticker_by_md5(db, wxid, name, md5)
            if ok:
                for it in items_all:
                    if (it.get("md5") or "").lower() == md5:
                        note_sticker_used(int(it.get("index") or 0))
                        break
                log("发表情「%s」（%s）：已发出并校验" % (tag_want[:12], md5[:8]))
            else:
                log("发表情「%s」：面板里没匹配上" % tag_want[:12])
            return ok
        except Exception as e:
            log("按标签发表情异常：%s" % e)
            return False

    if idx is None:
        return False
    # 用"菜单序号 → md5"换算真实格子：运行期间收藏顺序可能变了
    try:
        md5 = _MENU_SNAPSHOT.get(int(idx))
        if md5:
            real = panel_index_of(db, md5)
            if real >= 0 and real != idx:
                log("表情索引校正：菜单 %d → 面板 %d（收藏顺序变了）" % (idx, real))
                idx = real
    except Exception as e:
        log("表情索引校正失败（忽略）：%s" % e)
    # 按 md5 走缩略图匹配（序号会漂移，认图才可靠）
    try:
        snap = _REPLY_SNAPSHOT or _MENU_SNAPSHOT
        md5 = snap.get(int(idx)) or ""
        if not md5:
            # 兜底①：直接按 stickers.json 的 index 找
            try:
                items = load_stickers()
                for it in items:
                    if int(it.get("index") or -1) == int(idx):
                        md5 = (it.get("md5") or "").lower()
                        break
                # 兜底②：取编号最接近的一条
                if not md5 and items:
                    near = min(items, key=lambda x: abs(int(x.get("index") or 0) - int(idx)))
                    md5 = (near.get("md5") or "").lower()
                    log("表情编号 %d 不存在，改用最接近的第 %s 个" % (idx, near.get("index")))
            except Exception as e:
                log("表情编号兜底失败：%s" % e)
        if not md5:
            log("发表情：编号 %d 无法解析，跳过" % idx)
            return False
        import sticker_match
        ok = sticker_match.send_sticker_by_md5(db, wxid, name, md5)
        if ok:
            note_sticker_used(idx)
        log("发表情 %s（%s）：%s" % (md5[:8], "成功" if ok else "放弃",
                                  "已发出并校验" if ok else "面板里没匹配上"))
        return ok
    except Exception as e:
        log("发表情异常（忽略）：%s" % e)
        return False


STICKERS_PATH = os.path.join(HERE, "stickers.json")


def load_stickers():
    """读表情目录（index → 标签 + 喜好度）。"""
    try:
        with open(STICKERS_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f) or []
    except Exception:
        return []


def sticker_block(db, cfg, wxid: str, since_ts: float, nameof, max_n: int = 3) -> str:
    """把回看窗口里的表情消息解出来，拼成"谁发了个什么表情"。

    走 sticker_vision：md5 -> 本地文件 -> AES 解密 -> wxgf 转码 -> 视觉描述
    -> 按 md5 缓存。同一张表情只分析一次。
    """
    try:
        import sticker_vision as sv
        acc = sv.find_account_dir()
        if not acc:
            return ""
        rows = [r for r in (db.get_messages(wxid, limit=60) or [])
                if int(r.get("create_time") or 0) >= since_ts]
        if not any(r.get("type") == "动画表情" for r in rows):
            return ""
        return sv.recent_stickers_block(acc, rows, nameof, vision_describe, log, max_n)
    except Exception as e:
        log("表情识别异常（忽略）：%s" % e)
        return ""


def sticker_order_md5s(db) -> list:
    """按收藏顺序返回 md5 列表（rowid 顺序，未倒排）。"""
    try:
        rel = [r for r, _, _ in db._db_files if "emoticon" in r][0]
        con = db._open(rel)
        rows = con.execute("select md5 from kFavEmoticonOrderTable").fetchall()
        return [(str(r[0]) or "").lower() for r in rows if r and r[0]]
    except Exception as e:
        log("读收藏顺序失败：%s" % e)
        return []


def panel_index_of(db, md5: str) -> int:
    """md5 → 面板格子序号（-1 表示找不到）。

    面板顺序是收藏顺序的倒序，所以 index = N-1-pos。
    """
    md5 = (md5 or "").lower()
    order = sticker_order_md5s(db)
    if not order or md5 not in order:
        return -1
    n = len(order)
    return n - 1 - order.index(md5)


def sync_sticker_indices(db, path: str = "") -> int:
    """按数据库真实顺序重建 stickers.json 的 index（标签/喜好按 md5 保留）。"""
    order = sticker_order_md5s(db)
    if not order:
        return 0
    path = path or os.path.join(HERE, "stickers.json")
    # 跳过配置里屏蔽的表情（面板装不下的那些）
    try:
        hidden = {(x or "").lower() for x in
                  ((load_config().get("emoji") or {}).get("hidden") or [])}
    except Exception:
        hidden = set()
    if hidden:
        order = [m for m in order if m not in hidden]
    # 只保留最新的 max_total 个（收藏表是"新→旧"），更老的自动挤出菜单
    try:
        ec = (load_config().get("emoji") or {})
        cap = int(ec.get("max_total") or 25)
    except Exception:
        cap = 25
    overflow = []
    if len(order) > cap:
        # 收藏表是「旧 → 新」：保留最新 cap 个，挤掉最旧的
        overflow = order[:-cap]
        order = order[-cap:]
        # 记下溢出，供控制台提示"请手动删除"
        try:
            with open(os.path.join(HERE, "stickers_overflow.json"), "w", encoding="utf-8") as f:
                json.dump({"overflow": overflow, "total": len(order) + len(overflow),
                           "cap": cap, "at": time.strftime("%F %T")},
                          f, ensure_ascii=False)
        except Exception:
            pass
    old = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            old = json.load(f)
    except Exception:
        old = []
    by_md5 = {(x.get("md5") or "").lower(): x for x in old if isinstance(x, dict)}
    desc = {}
    try:
        with open(os.path.join(HERE, "sticker_desc.json"), "r", encoding="utf-8-sig") as f:
            desc = {k.lower(): v for k, v in (json.load(f) or {}).items()}
    except Exception:
        pass
    n = len(order)
    out = []
    for pos, md5 in enumerate(order):
        prev = by_md5.get(md5) or {}
        out.append({
            "index": n - 1 - pos,
            "md5": md5,
            "tag": prev.get("tag") or desc.get(md5) or "（待补描述）",
            "love": int(prev.get("love") or 3),
            "uses": int(prev.get("uses") or 0),
        })
    out.sort(key=lambda x: x["index"])
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        log("写 stickers.json 失败：%s" % e)
    return len(out)


def startup_sync_stickers(db) -> None:
    """启动时按数据库真实顺序重建表情索引。

    必要性：新收藏的表情会插到面板最前面，导致所有旧索引 +1；
    用快照索引去点，就会"想发 A 发出 B"。
    """
    try:
        n = sync_sticker_indices(db)
        if n:
            log("表情索引已同步：%d 个（按数据库收藏顺序）" % n)
    except Exception as e:
        log("表情索引同步失败（忽略）：%s" % e)


_REPLY_SNAPSHOT = {}   # 本轮生成用的编号映射（发送时以它为准）
_MENU_SNAPSHOT = {}      # 菜单序号 → md5（模型看到的那份）


def sticker_menu() -> str:
    """拼成一行给模型看：编号=标签。让它可以按语义挑，而不是瞎点。

    同时把「编号 → md5」记进快照：模型看到的编号就是这份快照里的编号，
    发送时用它能换算出"现在面板上的第几格"（收藏顺序会变）。
    """
    # 注意：这里**不再同步数据库**。
    # 每次同步都会重排编号，而模型已经按旧编号回了 <emoji:N>，一同步就全错。
    # 同步只在启动时和"偷到新表情之后"做。
    items = load_stickers()
    _MENU_SNAPSHOT.clear()
    for s in (items or []):
        if isinstance(s, dict):
            _MENU_SNAPSHOT[int(s.get("index") or 0)] = (s.get("md5") or "").lower()
    if not items:
        return ""
    parts = ["%s" % (s.get("tag") or "") for s in items if (s.get("tag") or "").strip()]
    return "你能发的收藏表情（想发就原样抄一个，写在回复末尾）：" + " | ".join(parts)


def pick_sticker_by_love() -> Optional[int]:
    """按喜好度加权随机：它偏爱某些表情，不是均匀随机。"""
    items = load_stickers()
    if not items:
        return None
    pool = []
    for s in items:
        w = max(1, int(s.get("love") or 1))
        pool += [s.get("index")] * w
    return random.choice(pool) if pool else None


def note_sticker_used(index: int) -> None:
    try:
        items = load_stickers()
        for s in items:
            if s.get("index") == index:
                s["uses"] = int(s.get("uses") or 0) + 1
                break
        with open(STICKERS_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


IMAGE_CACHE_PATH = os.path.join(HERE, "image_desc.json")
_IMG_CACHE: Dict[str, str] = {}


def _img_cache_load() -> None:
    if _IMG_CACHE:
        return
    try:
        with open(IMAGE_CACHE_PATH, "r", encoding="utf-8-sig") as f:
            _IMG_CACHE.update(json.load(f) or {})
    except Exception:
        pass


def _img_cache_save() -> None:
    try:
        with open(IMAGE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_IMG_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def describe_image_row(db, user: str, row: dict) -> str:
    """把一条图片消息变成一句话描述（带缓存，同一张只调一次视觉）。"""
    _img_cache_load()
    key = "%s:%s" % (user, row.get("local_id"))
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    vcfg = (load_config().get("vision") or {})
    if not vcfg.get("enabled", True):
        return ""
    try:
        from wechatauto import MediaDownloader
        save_dir = os.path.join(HERE, vcfg.get("save_dir") or "imgs")
        os.makedirs(save_dir, exist_ok=True)
        path = MediaDownloader(db).download_image(
            user, int(row.get("local_id")), save_dir=save_dir,
            tier="best", shard=row.get("shard"))
        if not path:
            return ""
        desc = vision_describe(path, "用一句不超过 20 字的话描述这张图的内容，不要客套")
        desc = (desc or "").replace("\n", " ").strip()[:60]
        if desc:                      # 空结果不缓存，否则以后再也不会重试
            _IMG_CACHE[key] = desc
            _img_cache_save()
        log("看图：%s" % desc)
        return desc
    except Exception as e:
        log("看图失败：%s" % e)
        return ""


def img_desc_cached(user: str, row: dict) -> str:
    """只查缓存，不调视觉。命中就直接用，不消耗任何预算。"""
    _img_cache_load()
    return _IMG_CACHE.get("%s:%s" % (user, row.get("local_id")), "")


def img_desc_try(db, user: str, row: dict, budget: int) -> Tuple[str, int]:
    """取图片描述：命中缓存直接返回（不扣预算）；否则调视觉并扣预算。

    返回 (描述, 剩余预算)。
    """
    cached = img_desc_cached(user, row)
    if cached:
        return cached, budget          # 缓存命中原样返回，不扣
    if budget <= 0:
        return "", budget
    try:
        desc = describe_image_row(db, user, row)
    except Exception:
        desc = ""
    if desc:
        budget -= 1
    return desc, budget


def recent_images_block(db, user: str, since_ts: float, max_n: int = 2,
                        nameof=None) -> str:
    """回看窗口里最近几张图，各给一句描述，拼成提示词里的一段。"""
    try:
        rows = db.get_messages(user, limit=80) or []
    except Exception:
        return ""
    pics = [r for r in rows
            if r.get("type") == "图片" and int(r.get("create_time") or 0) >= since_ts]
    out = []
    for r in pics[:max_n]:
        d = describe_image_row(db, user, r)
        if d:
            who = "？"
            try:
                if nameof is not None:
                    who = nameof(r.get("sender_username") or "") or "？"
            except Exception:
                pass
            out.append("【%s 发了张图：%s】" % (who, d))
    return "\n".join(out)


VISION_FALLBACK_LOGGED = {"n": 0}


def _vision_once(url: str, model: str, key: str, b64: str, mime: str,
                 prompt: str, max_tokens: int, timeout: float) -> str:
    """调一次视觉接口，返回文本（失败抛异常）。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": "data:%s;base64,%s" % (mime, b64), "detail": "low"}},
        ]}],
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""


def vision_describe(image_path: str, prompt: str = "",
                    max_tokens: int = 60) -> str:
    """看图并返回一句话描述。

    优先走 config 里的 vision_backend（豆包），失败自动退回主后端（DeepSeek）。
    detail=low 会把图缩到 512x512，单图 token 可控。
    """
    import base64

    cfg = load_config()
    bk = cfg.get("backend") or {}
    vb = cfg.get("vision_backend") or {}
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    text_prompt = prompt or "描述这张图"

    tries = []
    if vb.get("enabled", False) and vb.get("url") and vb.get("model"):
        k = os.environ.get(vb.get("api_key_env") or "", "")
        if k:
            tries.append(("vision", vb.get("url"), vb.get("model"), k,
                          float(vb.get("timeout_s") or 45)))
    k2 = os.environ.get(bk.get("api_key_env") or "", "")
    if k2:
        tries.append(("backend", bk.get("url"), bk.get("model"), k2,
                      float(bk.get("timeout_s") or 30)))
    if not tries:
        raise RuntimeError("没有可用的视觉 API Key")

    last_err = None
    for idx, (label, url, model, key, timeout) in enumerate(tries):
        try:
            out = _vision_once(url, model, key, b64, mime, text_prompt, max_tokens, timeout)
            if out:
                if idx > 0 and VISION_FALLBACK_LOGGED["n"] < 3:
                    VISION_FALLBACK_LOGGED["n"] += 1
                    log("视觉：%s 失败，已退回 %s" % (tries[0][0], label))
                return out
            last_err = "空返回"
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError("视觉调用失败：%s" % last_err)



def verify_sent(db, wxid: str, text: str, since_seq: int, timeout: float = 6.0) -> bool:
    """回读数据库确认这条真的发出去了（UIA 返回成功只代表调用没报错）。"""
    needle = (text or "").strip()[:12]
    if not needle or not wxid:
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            for r in db.get_new_messages(wxid, since_seq=since_seq, limit=10) or []:
                if needle in str(r.get("content") or ""):
                    return True
        except Exception:
            pass
        time.sleep(0.35)
    return False


GUI_ACTIVE_PATH = os.path.join(HERE, "gui", "active.json")


def gui_active_seconds() -> float:
    """GUI 最近一次"用户操作"距今多少秒；读不到就返回一个很大的数。"""
    try:
        with open(GUI_ACTIVE_PATH, "r", encoding="utf-8-sig") as f:
            at = float((json.load(f) or {}).get("at") or 0)
        return max(0.0, time.time() - at)
    except Exception:
        return 1e9


def wait_for_gui_idle(cfg, reason: str = "") -> None:
    """要抢焦点前等一下：用户在 GUI 里操作时先别动微信窗口。

    只影响"需要切会话"的发送；回当前会话不受影响。
    """
    dc = cfg.get("defer") or {}
    if not dc.get("enabled", True):
        return
    window = float(dc.get("active_window_s") or 30)
    limit = float(dc.get("max_wait_s") or 60)
    idle = gui_active_seconds()
    if idle > window:
        return
    t0 = time.time()
    log("检测到你在操作控制台（%.0fs 前），先不切微信窗口%s" % (idle, ("：" + reason) if reason else ""))
    while time.time() - t0 < limit:
        time.sleep(3.0)
        if gui_active_seconds() > window:
            log("你已经停手了，继续发送")
            return
    log("等满 %.0f 秒仍未停手，强制发送（可能会抢一次焦点）" % limit)


def _current_chat_is(name: str) -> bool:
    try:
        cur = uia_current_chat()
        return bool(cur) and (name in cur or cur in name)
    except Exception:
        return False


def send_via_uia_verified(db, wxid: str, name: str, text: str) -> bool:
    """UIA 发送 + 回读确认。发出去就认成功，避免"校验超时→重发"造成重复。"""
    set_focus_mode(True)          # 要投递点击/按键，这段时间允许激活
    try:
        return _send_via_uia_verified_inner(db, wxid, name, text)
    finally:
        set_focus_mode(False)     # 发完立刻回到"不抢前台"


def _send_via_uia_verified_inner(db, wxid: str, name: str, text: str) -> bool:
    try:
        u = get_uia()
        if not u.is_running():
            return False
        seq = 0
        try:
            rows = db.get_messages(wxid, limit=1) or []
            seq = int(rows[0]["sort_seq"]) if rows else 0
        except Exception:
            seq = 0
        # 只有"需要切会话"时才可能抢焦点；已经在这个会话就直接发
        if not _current_chat_is(name):
            wait_for_gui_idle(load_config(), "发往「%s」" % name)
        if not uia_open_chat(name, wxid=wxid):
            return False
        clean = sanitize_out(text)
        if not clean:
            return False
        # 发之前先清一次：上一次失败可能留了半截在输入框，接上去会越滚越长
        try:
            get_bg().clear_input()
            time.sleep(0.2)
        except Exception:
            pass
        if not u.send_text(clean):
            try:
                get_bg().clear_input()
            except Exception:
                pass
            return False
        if not verify_sent(db, wxid, clean, seq):
            # 没读到就把输入框清掉：避免残留文本被下一次发送接上（会越来越长）
            log("已发送但没在库里读到，清一次输入框防残留")
            try:
                get_bg().clear_input()
            except Exception:
                pass
        return True
    except Exception as e:
        log("UIA 发送异常：%s" % e)
        return False


def looks_like_loop(text: str, window: int = 6, repeat: int = 5) -> bool:
    """检测复读：同一小段反复出现 5 次以上，基本就是被要求"重复五百遍"。"""
    t = (text or "").strip()
    if len(t) < window * repeat:
        return False
    seen = set()
    for i in range(0, len(t) - window + 1):
        chunk = t[i:i + window]
        if chunk in seen or not chunk.strip():
            continue
        seen.add(chunk)
        if t.count(chunk) >= repeat:
            return True
    return False

def guard_reply(text: str, cfg: Dict[str, Any], entry: Dict[str, Any]) -> str:
    """回复的出口闸门：先拦复读，再按句号截断到上限（不切半句）。"""
    if looks_like_loop(text):
        pool = cfg.get("refusals") or ["这个我不重复哈"]
        return random.choice(pool)

    cap = int(entry.get("max_reply_chars") or cfg.get("max_reply_chars") or 180)
    if len(text) > cap:
        cut = text[:cap]
        m = list(re.finditer(r"[。！？!?~～…]", cut))
        text = cut[:m[-1].end()] if m else cut
    return text.strip()

def _partition(parts: List[str], n: int) -> List[str]:
    """把句子序列切成 n 段，使最长那段尽量短（不丢内容、条目均衡）。

    贪心装填会翻车：最后一段超出格子就被丢掉。这里用 DP 求最优切点。
    """
    m = len(parts)
    if n <= 1 or m <= n:
        return parts[:]
    prefix = [0]
    for p in parts:
        prefix.append(prefix[-1] + len(p))
    INF = 10 ** 9
    dp = [[INF] * (n + 1) for _ in range(m + 1)]
    cut = [[0] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0
    for j in range(1, n + 1):
        for i in range(1, m + 1):
            for k in range(j - 1, i):
                if dp[k][j - 1] >= INF:
                    continue
                val = max(dp[k][j - 1], prefix[i] - prefix[k])
                if val < dp[i][j]:
                    dp[i][j] = val
                    cut[i][j] = k
    segs: List[Tuple[int, int]] = []
    i, j = m, n
    while j > 0:
        k = cut[i][j]
        segs.append((k, i))
        i, j = k, j - 1
    segs.reverse()
    return ["".join(parts[a:b]) for a, b in segs]

def split_bubbles(text: str, max_bubbles: int = 3, min_len: int = 4,
                  max_len: int = 60) -> List[str]:
    """把一段回复切成几条"气泡"，模拟真人分几次发。

    先按句子切，再用 DP 均分成最多 max_bubbles 组 ——
    保证内容一句不丢（以前贪心会把末尾那句吃掉）。
    """
    text = (text or "").strip()
    if not text:
        return []

    hard = max_bubbles * max_len
    if len(text) > hard:
        cut = text[:hard]
        m = list(re.finditer(r"[。！？!?~～…]", cut))
        text = cut[:m[-1].end()] if m else cut

    parts = [p.strip() for p in re.split(r"\|\||\n+", text) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?~～…])", text) if p.strip()]
    parts = [p for p in parts if p]
    if not parts:
        return [text]

    if len(parts) <= max_bubbles:
        return parts

    out = _partition(parts, max_bubbles)

    # 兜底：某一"句"本身超长时只能硬切
    final: List[str] = []
    for b in out:
        while len(b) > max_len:
            final.append(b[:max_len])
            b = b[max_len:]
        if b:
            final.append(b)
    return final

def bubble_cfg(entry: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """分条发送的配置：群条目优先，其次全局，默认关。"""
    b = entry.get("bubbles")
    if b is None:
        b = cfg.get("bubbles") or {}
    if isinstance(b, bool):
        b = {"enabled": b}
    d = {"enabled": False, "max": 3, "delay_min_s": 0.4,
         "delay_max_s": 1.0, "max_len": 60}
    d.update(b or {})
    return d

def send_multi(send_one, text: str, bcfg: Dict[str, Any]) -> bool:
    """按配置分条发送。send_one(str) -> bool。"""
    if not bcfg.get("enabled"):
        return send_one(text)
    parts = split_bubbles(text, int(bcfg.get("max") or 3),
                          max_len=int(bcfg.get("max_len") or 60))
    ok = True
    for i, part in enumerate(parts):
        if i:
            time.sleep(random.uniform(float(bcfg.get("delay_min_s") or 0.8),
                                      float(bcfg.get("delay_max_s") or 2.5)))
        ok = send_one(part)
        if not ok:
            break
    return ok

def send_image(db, name: str, path: str) -> bool:
    """发图片。走剪贴板粘贴，微信里显示为图片消息（不是文件）。"""
    from wechatauto import Chat
    if not os.path.exists(path):
        log("图片不存在：%s" % path)
        return False
    try:
        chat = Chat(who=name, db=db)
        r = chat.SendFiles(os.path.abspath(path), who=name)
        return bool(getattr(r, "is_success", False))
    except Exception as e:
        log("发图异常：%s" % e)
        return False


WECHAT_EXES = ("weixin.exe", "wechat.exe")


def cap_incoming(text: str, cap: int) -> str:
    """入站单条消息限长：防有人粘贴几万字把提示词撑爆。"""
    t = text or ""
    if cap > 0 and len(t) > cap:
        return t[:cap] + "…（后面还有 %d 字，省略）" % (len(t) - cap)
    return t


def wechat_running() -> bool:
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() in WECHAT_EXES:
                return True
    except Exception:
        return True
    return False


def gui_is_wechat(chat) -> bool:
    """确认要操作的是微信窗口：微信关掉/最小化时会误附着到别的窗口。"""
    gui = getattr(chat, "_gui", None)
    pid = getattr(gui, "pid", 0) if gui is not None else 0
    if not pid:
        return True
    try:
        import psutil
        name = (psutil.Process(int(pid)).name() or "").lower()
    except Exception:
        return True
    if name not in WECHAT_EXES:
        log("窗口校验失败：附着到 %s，不是微信" % name)
        return False
    return True



PROMPT_DUMP = os.path.join(HERE, "prompt_dump.log")


def dump_prompt(kind: str, who: str, system: str, messages) -> None:
    """把本次发给模型的提示词追加落盘（调试用，默认关闭）。

    群聊归属出错时，光看日志看不出模型到底看到了什么——这份 dump 能直接对账。
    """
    try:
        cfg = load_config()
        if not (cfg.get("debug") or {}).get("dump_prompt"):
            return
        import json as _json
        with open(PROMPT_DUMP, "a", encoding="utf-8") as f:
            f.write("\n===== %s | %s =====\n" % (datetime.now().strftime("%H:%M:%S"), kind))
            f.write("[system]\n%s\n" % system)
            for m in (messages or []):
                f.write("[%s] %s\n" % (m.get("role"), str(m.get("content"))[:800]))
    except Exception:
        pass


def sticker_is_favorite(md5: str) -> bool:
    """查 emoticon.db：这张表情是否已经在收藏里。"""
    try:
        db = _shared_db()
        rel = [r for r, _, _ in db._db_files if "emoticon" in r][0]
        conn = db._open(rel)
        row = conn.execute(
            "select count(*) from kNonStoreEmoticonTable where lower(md5)=?",
            ((md5 or "").lower(),)).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def steal_sticker(db, wxid: str, name: str, md5: str, mine: bool = False) -> bool:
    """把最新一条表情消息加进收藏。

    安全前置（顺序不能变）：
      1. 该 md5 还没收藏 —— 否则菜单首项是「转发…」，点下去会弹转发框
      2. 目标会话已经打开（UIA 切好），且这条表情就是最后一条消息
    """
    if sticker_is_favorite(md5):
        log("表情 %s 已在收藏里，跳过" % md5[:8])
        return False
    # 安全前置：目标必须是这个会话的**最后一条**消息。
    # 否则右键会点到别的消息，而文本菜单首项是「转发…」——会弹转发框。
    try:
        last = (db.get_messages(wxid, limit=1) or [None])[0]
        # 先让模型看描述决定收不收（提示词可在控制台编辑）
        try:
            _md5 = ""
            _m0 = re.search(r'md5\s*=\s*"([0-9a-fA-F]+)"', str(last.get("content") or "")) if last else None
            if _m0:
                _md5 = _m0.group(1).lower()
            _desc = ""
            try:
                with open(os.path.join(HERE, "sticker_desc.json"), "r", encoding="utf-8-sig") as _f:
                    _desc = (json.load(_f) or {}).get(_md5, "")
            except Exception:
                _desc = ""
            if not model_wants_sticker(load_config(), _desc):
                _STEAL_STATE["last"] = time.time()   # 记一次冷却，别一直问
                return 0
        except Exception as _e:
            log("偷表情判断异常（按不收处理）：%s" % _e)
            return 0
        if not last or last.get("type") != "动画表情":
            log("偷表情：最后一条不是表情，放弃（避免误点转发）")
            return False
        m = re.search(r'md5\s*=\s*"([0-9a-fA-F]+)"', str(last.get("content") or ""))
        if not m or m.group(1).lower() != (md5 or "").lower():
            log("偷表情：最后一条不是目标表情，放弃（避免误点转发）")
            return False
    except Exception as e:
        log("偷表情：读取最后一条失败，放弃（%s）" % e)
        return False
    try:
        if not uia_open_chat(name, wxid=wxid):
            log("偷表情：切会话失败，放弃")
            return False
        bg = get_bg()
        bg.ensure_onscreen()
        if not bg.steal_latest_sticker(mine=mine):
            try:
                bg.close_forward_dialog()
            except Exception:
                pass
            return False
        if sticker_is_favorite(md5):
            log("偷表情成功：%s" % md5[:8])
        try:
            post_steal_refresh(md5)
        except Exception as _e:
            log("偷后刷新失败（忽略）：%s" % _e)
            return True
        log("偷表情：点了「添加到表情」但数据库里没出现 %s" % md5[:8])
        return False
    except Exception as e:
        log("偷表情异常：%s" % e)
        return False



def ask_model_brief(cfg, system: str, user: str, max_tokens: int = 16) -> str:
    """一次极短的模型请求（用于"收/不收""删哪个"这类判断）。

    失败返回空串，调用方按"不做动作"处理——宁可不偷不删，也不乱来。
    """
    try:
        backend = build_backend(cfg)
        ctx = {"kind": "judge", "system": system, "messages": [
            {"role": "user", "content": user}]}
        out = backend.reply(user, ctx) or ""
        return out.strip()
    except Exception as e:
        log("判断请求失败（忽略）：%s" % e)
        return ""


def model_wants_sticker(cfg, desc: str) -> bool:
    """偷之前问一句：这张值不值得收。"""
    p = ((cfg.get("emoji") or {}).get("prompts") or {})
    system = (p.get("steal") or "").strip()
    if not system:
        return True                      # 没配提示词就沿用旧行为
    ans = ask_model_brief(cfg, system, "这张表情的描述：%s" % (desc or "（无描述）"), 8)
    ok = ("收" in ans) and ("不收" not in ans)
    log("偷表情判断：%s → %s" % ((ans or "空")[:12], "收" if ok else "不收"))
    return ok


def model_pick_delete(cfg, items) -> str:
    """库满时让模型挑一个删，返回 md5（空串表示不删）。"""
    p = ((cfg.get("emoji") or {}).get("prompts") or {})
    system = (p.get("delete") or "").strip()
    if not system or not items:
        return ""
    listing = "\n".join("%d=%s（用了%d次）" % (x.get("index"), x.get("tag"), int(x.get("uses") or 0))
                        for x in items)
    ans = ask_model_brief(cfg, system, listing, 8)
    m = re.search(r"\d+", ans or "")
    if not m:
        log("删表情判断：没给出编号（%s），跳过" % (ans or "空")[:12])
        return ""
    idx = int(m.group(0))
    for x in items:
        if int(x.get("index") or -1) == idx:
            log("删表情判断：选中第 %d 个「%s」" % (idx, x.get("tag")))
            return (x.get("md5") or "").lower()
    log("删表情判断：编号 %d 不存在，跳过" % idx)
    return ""


def drop_from_pool(md5: str) -> bool:
    """从我们自己的表情菜单里移除一条（不动微信收藏）。"""
    try:
        items = load_stickers()
        kept = [x for x in items if (x.get("md5") or "").lower() != (md5 or "").lower()]
        if len(kept) == len(items):
            return False
        kept.sort(key=lambda x: x.get("index") or 0)
        for i, x in enumerate(kept):
            x["index"] = i
        tmp = STICKERS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STICKERS_PATH)
        log("表情池腾位：已移除 %s（剩 %d）" % ((md5 or "")[:8], len(kept)))
        return True
    except Exception as e:
        log("表情池移除失败：%s" % e)
        return False


def post_steal_refresh(md5: str) -> None:
    """偷到新表情后：同步菜单 + 补描述，让它立刻能发。"""
    try:
        db = _shared_db()
        sync_sticker_indices(db)
        log("表情菜单已刷新（新表情进列表）")
    except Exception as e:
        log("刷新表情菜单失败：%s" % e)
    try:
        import sticker_vision as sv
        d = sv.describe_sticker(None, md5, vision_describe, None, None)
        if d:
            items = load_stickers()
            for it in items:
                if (it.get("md5") or "").lower() == md5:
                    it["tag"] = d[:24]
            tmp = STICKERS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STICKERS_PATH)
            log("新表情描述：%s → %s" % (md5[:8], d[:20]))
    except Exception as e:
        log("补描述失败（忽略）：%s" % e)


def steal_on_arrival(db, cfg, wxid: str, name: str) -> int:
    """群里有新表情时自动收藏。

    条件（全部满足才动手）：
      * steal.enabled 打开
      * 这张还没在收藏里
      * 总数没超过 max_total
      * 距上次收藏超过 min_interval_s
      * 该表情确实是当前会话最后一条（steal_sticker 内部还会再校验一次）
    """
    sc = cfg.get("steal") or {}
    if not sc.get("enabled"):
        return 0
    now = time.time()
    if now - float(_STEAL_STATE.get("last") or 0) < float(sc.get("min_interval_s") or 20):
        return 0
    try:
        favs = sticker_favorite_count()
    except Exception:
        favs = 0
    hard_cap = int((cfg.get("emoji") or {}).get("hard_cap") or 40)
    if favs >= hard_cap:
        # 别静默：每小时提示一次，并在控制台留标记
        if now - float(_STEAL_STATE.get("cap_warn") or 0) > 3600:
            _STEAL_STATE["cap_warn"] = now
            log("表情已达采集上限（%d/%d）：请到微信里删掉不用的表情，"
                "然后点控制台「同步表情」" % (favs, hard_cap))
            try:
                p = os.path.join(HERE, "stickers_overflow.json")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"overflow": [], "cap_reached": True,
                               "total": favs, "cap": hard_cap,
                               "at": time.strftime("%F %T")}, f, ensure_ascii=False)
            except Exception:
                pass
        return 0
    try:
        last = (db.get_messages(wxid, limit=1) or [None])[0]
        if not last:
            return 0
        # ① 只有"新消息"才判断：同一 seq 判过就不再重复
        seq = int(last.get("sort_seq") or 0)
        if seq and _STEAL_STATE["seen_seq"].get(wxid) == seq:
            return 0
        if seq:
            _STEAL_STATE["seen_seq"][wxid] = seq
        if last.get("type") != "动画表情":
            return 0
        m = re.search(r'md5\s*=\s*"([0-9a-fA-F]+)"', str(last.get("content") or ""))
        if not m:
            return 0
        md5 = m.group(1).lower()
    except Exception:
        return 0
    if sticker_is_favorite(md5):
        return 0
    # ② 同一张连续失败 3 次就不再试（避免每轮重试）
    if int(_STEAL_STATE["fail"].get(md5) or 0) >= 3:
        return 0
    mine = bool(my_wxid_cached()) and (last.get("sender_username") or "") == my_wxid_cached()
    if mine:
        return 0       # 自己发的一定是收藏过的，偷它只会点到「转发」
    if steal_sticker(db, wxid, name, md5, mine=mine):
        _STEAL_STATE["last"] = now
        _STEAL_STATE["n"] = int(_STEAL_STATE.get("n") or 0) + 1
        _STEAL_STATE["fail"].pop(md5, None)
        log("偷到第 %d 个表情（收藏共 %d 个）" % (_STEAL_STATE["n"], favs + 1))
        return 1
    # 失败累计；到 3 次就放弃这一张
    n = int(_STEAL_STATE["fail"].get(md5) or 0) + 1
    _STEAL_STATE["fail"][md5] = n
    if n >= 3:
        log("偷表情放弃：%s 连续失败 %d 次，本次运行不再尝试" % (md5[:8], n))
    return 0


def sticker_favorite_count() -> int:
    try:
        db = _shared_db()
        rel = [r for r, _, _ in db._db_files if "emoticon" in r][0]
        row = db._open(rel).execute(
            "select count(*) from kNonStoreEmoticonTable").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def sanitize_out(text: str) -> str:
    """出站文本消毒。

    微信输入框里打 `@` 会弹联系人选择框，后续字符会被它吃掉、回车变成选中联系人，
    结果消息卡在输入框发不出去（实测踩过）。所以出站一律把 `@` 换掉。
    """
    t = text or ""
    t = t.replace("\u2005", " ").replace("\u200b", "")
    t = t.replace("@", "艾特")
    return t.strip()


def dispatch_send(db, wxid: Optional[str], name: str, text: str,
                  at_name: Optional[str] = None) -> bool:
    """发送总入口。

    只用 UIA：它读控件树，不依赖窗口重绘，也不需要窗口在前台。
    不再保留任何"坐标点击"或"前台 SendInput"兜底——前者会假成功，
    后者会抢用户的鼠标和焦点（实测被投诉过）。
    UIA 失败就放弃这一条，宁可不发也不动用户的桌面。
    """
    global _FAIL_STREAK, SEND_MODE
    t0 = time.time()
    was_foreground = False
    try:
        was_foreground = get_bg().is_foreground()
    except Exception:
        pass

    if SEND_MODE == "foreground":
        ok = _send_foreground(db, name, text, at_name)
    else:
        # UIA 读控件树要求窗口"可见"（离屏时读出来是空），所以保持窗口在屏幕上，
        # 但不主动置前；只有确实需要时才由 UIA 自己激活。
        try:
            get_bg().ensure_onscreen()
        except Exception as e:
            log("窗口归位失败：%s" % e)
        if at_name:
            log("后台路径不支持 @，已忽略")
        ok = send_via_uia_verified(db, wxid, name, text) if wxid else False
        if not ok:
            log("发送失败，本条放弃（不抢前台、不猜落点）")

    cost = time.time() - t0
    if ok:
        _FAIL_STREAK = 0
        if cost > 12:
            log("本次发送耗时 %.1fs" % cost)
    else:
        _FAIL_STREAK += 1
        if _FAIL_STREAK >= 3:
            log("连续 %d 次发送失败：请检查微信窗口是否被关闭/最小化" % _FAIL_STREAK)
    return ok


def send_group(db, name: str, text: str, at_name: Optional[str] = None,
               wxid: Optional[str] = None) -> bool:
    return dispatch_send(db, wxid, name, text, at_name)


def send(db, name: str, text: str, wxid: Optional[str] = None) -> bool:
    """真正发送。只有 --live 会走到这里。"""
    return dispatch_send(db, wxid, name, text)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ai-chat-bot M1 好友私聊闭环")
    ap.add_argument("--live", action="store_true", help="真的发消息（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（本来就是默认）")
    ap.add_argument("--once", action="store_true", help="只跑一轮就退出")
    ap.add_argument("--from-beginning", action="store_true", help="忽略水位，从最早消息回放（仅测试）")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--list-groups", action="store_true",
                    help="列出小号所在的所有群（含 wxid、人数、我是不是群主），用于填 config")
    ap.add_argument("--send-image", default=None, metavar="路径",
                    help="测试用：把一张图发给白名单里的第一个会话")
    ap.add_argument("--send-file", default=None, metavar="路径",
                    help="把任意文件（如 md/zip）发出去，微信里显示为文件消息")
    ap.add_argument("--to", default=None, help="配合 --send-image/--send-file 指定发给谁（备注名或群名）")
    ap.add_argument("--check-api", action="store_true",
                    help="只测模型通道：打一次极短的请求，验证 url/model/key 是否通")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    load_dotenv()
    if args.list_groups:            # 只读，不需要抢锁
        return list_groups()
    if args.send_image:             # 单次动作，不需要常驻
        return send_image_test(load_config(), args.send_image, args.to)
    if args.send_file:
        return send_image_test(load_config(), args.send_file, args.to)
    if args.check_api:
        return check_api(load_config())
    if not acquire_lock():
        return 1
    try:
        return _run(args)
    finally:
        release_lock()


def list_groups() -> int:
    db, err = connect_db()
    if db is None:
        log("打开数据库失败：%s" % err)
        return 1
    me = (db.get_self_info() or {})
    my = me.get("username") or ""
    try:
        groups = db.get_groups() or []
    except Exception as e:
        log("读群列表失败：%s" % e)
        return 1
    if not groups:
        log("小号一个群都没有 —— 先把小号拉进你的朋友群")
        return 1
    log("小号所在的群（%d 个）：" % len(groups))
    for g in groups:
        owner = "✅ 我是群主" if g.get("owner") == my else "（群主是别人）"
        log("  %-24s %-3s人  %s  %s"
            % (g.get("name") or "?", g.get("member_count"), g.get("username"), owner))
    log("把要接的群名，原样填进 config.json 的 allow_groups[0].name")
    return 0


def send_image_test(cfg: Dict[str, Any], path: str, to: Optional[str]) -> int:
    db, err = connect_db()
    if db is None:
        log("打开数据库失败：%s" % err)
        return 1
    if not to:
        friends = [e for e in (cfg.get("allow_friends") or []) if e.get("name")]
        groups = [e for e in (cfg.get("allow_groups") or []) if e.get("name")]
        if friends:
            to = friends[0]["name"]
        elif groups:
            to = groups[0]["name"]
        else:
            log("白名单是空的，用 --to 指定发给谁")
            return 1
    log("发图给「%s」：%s" % (to, os.path.abspath(path)))
    ok = send_image(db, to, path)
    log("结果：%s" % ("成功" if ok else "失败"))
    return 0 if ok else 1


def check_api(cfg: Dict[str, Any]) -> int:
    """只验模型通道，不碰微信。换中转站之后先跑这个。"""
    b = cfg.get("backend") or {}
    log("通道：%s" % b.get("type"))
    log("URL  ：%s" % b.get("url"))
    log("模型 ：%s" % b.get("model"))
    env = b.get("api_key_env") or "DEEPSEEK_API_KEY"
    key = os.environ.get(env, "")
    log("Key  ：%s（读 %s）" % (("%s…%s" % (key[:6], key[-4:])) if len(key) > 12 else "未读到", env))
    if not key:
        log("环境里没有 %s。把 .env 里的变量名改成 %s，或在 .env 里加一行。" % (env, env))
        return 1
    try:
        backend = build_backend(cfg)
    except SystemExit as e:
        log(str(e))
        return 1
    t0 = time.time()
    try:
        out = backend.reply("只回两个字：通了", {"kind": "friend"})
    except Exception as e:
        log("失败（%.1fs）：%s" % (time.time() - t0, e))
        log("对照：401=key；404=URL 少了 /v1/chat/completions；429=额度；认不出结构=这家中转返回格式不同")
        return 1
    log("成功（%.1fs）：%r" % (time.time() - t0, out[:60]))
    return 0


WAKEUP_DEFAULT = {
    "enabled": True,
    "min_gap_s": 900,        # 最早一条积压超过这么久，才算"睡了一觉"
    "min_messages": 3,       # 积压少于这么多条就不插话，照常处理
    "max_context": 24,       # 给模型看的积压消息条数上限
    "max_chars": 40,
}


def build_backlog_context(db, wxid: str, since_seq: int, limit: int,
                          my_wxid: str, nameof, is_group: bool) -> str:
    """把积压的消息渲染成几行，给模型当"我不在的时候发生了什么"。"""
    try:
        rows = db.get_new_messages(wxid, since_seq=since_seq, limit=limit) or []
    except Exception:
        return ""
    lines = []
    for r in rows:
        rtype = r.get("type")
        if rtype not in ("文本", "动画表情", "图片"):
            continue
        sender, text = split_group_content(r.get("content") or "",
                                          r.get("sender_username") or "")
        if rtype == "动画表情":
            text = "[发了个表情包]"
        elif rtype == "图片":
            text = "[发了张图]"
        text = (text or "").strip()
        if not text:
            continue
        if is_group:
            who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        else:
            who = nameof(sender) if sender and sender != my_wxid else "对方"
        lines.append("【%s】%s" % (who, text[:60]))
    return "\n".join(lines[-limit:])


def catch_up_wakeup(db, cfg, backend, guard, state, dry_run: bool, my_wxid: str,
                    groups, hist, mem) -> int:
    """休眠后醒来：读懂积压，只插一句；不发逐条回复。

    返回插话的会话数。
    """
    wc = dict(WAKEUP_DEFAULT)
    wc.update(cfg.get("wakeup") or {})
    if not wc.get("enabled", True):
        return 0

    limits = cfg.get("limits") or {}
    gmap = {g["wxid"]: g for g in (groups or [])}
    friends = {e["wxid"]: e.get("name") or "" for e in (cfg.get("allow_friends") or [])
               if isinstance(e, dict) and e.get("wxid")}
    nameof = lambda w: (mem_nick(db).get(w) or (w[:8] if w else ""))
    done = 0

    for wxid in list(state.keys()):
        since = int(state.get(wxid) or 0)
        is_group = wxid in gmap
        if not is_group and wxid not in friends:
            continue
        try:
            rows = db.get_new_messages(wxid, since_seq=since, limit=400) or []
        except Exception as e:
            log("唤醒检查失败（%s）：%s" % (short(wxid), e))
            continue
        if not rows:
            continue

        try:
            latest = int(rows[-1].get("sort_seq") or since)
            oldest_ts = min(int(r.get("create_time") or 0) for r in rows)
        except Exception:
            continue
        gap = time.time() - oldest_ts
        # 积压不多 or 间隔不长：交给正常流程，一条条回
        if len(rows) < int(wc.get("min_messages") or 3) or gap < float(wc.get("min_gap_s") or 900):
            continue
        # 群聊在静默时段就不插话（私聊不受静默限制）
        if is_group and in_quiet_hours(limits):
            state[wxid] = latest
            log("群 %s：醒来时在静默时段，积压 %d 条不插话" % (gmap[wxid]["name"], len(rows)))
            continue

        ctx_text = build_backlog_context(
            db, wxid, since, int(wc.get("max_context") or 24), my_wxid, nameof, is_group)
        if not ctx_text:
            state[wxid] = latest
            continue

        name = gmap[wxid]["name"] if is_group else (friends.get(wxid) or "对方")
        hours = gap / 3600.0
        when = ("%.1f 小时" % hours) if hours >= 1 else ("%d 分钟" % int(gap // 60))
        system = (cfg.get("backend") or {}).get("group_system" if is_group else "system") or ""
        system += (
            "\n\n【场景】你刚回到微信，之前大约 %s没看消息。下面是你不在时他们聊的内容。"
            "请只插一句很短的话，像一个人刚上线随口接一句，比如「刚睡醒，哥几个聊得挺欢」、"
            "「接着吵，我就看血流成河」这种。要求：不逐条回答、不总结、不复述、不问一长串问题、"
            "不@任何人，控制在 %d 字以内。只输出这一句话。"
            % (when, int(wc.get("max_chars") or 40))
        )
        messages = [{"role": "user",
                     "content": "【我不在时的对话】\n%s\n\n【现在】我回来了。" % ctx_text}]
        try:
            reply = backend.reply_to(system, messages) if hasattr(backend, "reply_to") \
                else backend.reply(ctx_text, {"kind": "group" if is_group else "friend",
                                              "system": system, "who": name})
        except Exception as e:
            log("唤醒插话生成失败（%s）：%s" % (name, e))
            state[wxid] = latest
            continue

        reply = strip_emoji_tag(reply or "").strip()
        if not reply:
            state[wxid] = latest
            continue
        reply = reply[:int(wc.get("max_chars") or 40)]

        if dry_run:
            log("[dry-run][唤醒] %s 积压 %d 条（%.1f 小时）→ 插一句「%s」"
                % (name, len(rows), gap / 3600.0, reply))
        else:
            ok = dispatch_send(db, wxid, name, reply)
            log("[唤醒] %s 积压 %d 条（%.1f 小时）→ 插一句「%s」%s"
                % (name, len(rows), gap / 3600.0, reply, "" if ok else "（发送失败）"))
        if hist is not None and hist.enabled:
            hist.add(wxid, "assistant", reply)
        state[wxid] = latest          # 积压一律跳过，不再逐条回
        done += 1

    if done:
        save_state(state)
    return done


def mem_nick(db):
    """昵称映射（带缓存）。"""
    global _NICK_CACHE
    if _NICK_CACHE is None:
        try:
            _NICK_CACHE = db.nickname_map() or {}
        except Exception:
            _NICK_CACHE = {}
    return _NICK_CACHE


_NICK_CACHE = None


def _run(args: argparse.Namespace) -> int:
    global SEND_MODE
    cfg = load_config()
    SEND_MODE = (cfg.get("send_mode") or "foreground").strip().lower()
    if SEND_MODE in ("background", "auto"):
        try:
            bg = get_bg()
            if SEND_MODE == "background":
                # 嵌入模式下窗口常驻可见（UIA 要求可见），不再挪出屏幕
                log("发送模式：background（窗口保持可见，不挪走）")
            else:
                log("发送模式：auto（微信在前台就原地发，不在前台就挪走发、发完还回）")
        except Exception as e:
            log("后台发送初始化失败（退回前台模式）：%s" % e)
            SEND_MODE = "foreground"
    if SEND_MODE == "foreground":
        log("发送模式：foreground（微信窗口需保持可见）")
    if args.list_groups:
        return list_groups()
    if args.send_image:
        return send_image_test(cfg, args.send_image, args.to)
    try:
        uia_reset_watch()      # 每次启动都从头开始，别继承上次的"已放弃"
    except Exception:
        pass

    # UIA 健康看门狗（守护线程，90 秒一探，坏了自动尝试自愈）
    try:
        import threading as _th
        def _watch():
            _memdb = None
            try:
                mem_backfill_vectors(load_config())   # 启动兜底：补缺失向量
            except Exception:
                pass
            while True:
                try:
                    uia_healthy()
                except Exception:
                    pass
                try:
                    if _memdb is None:
                        _memdb = _shared_db()
                    mem_maintain(_memdb, load_config())
                except Exception:
                    pass
                time.sleep(60)
        _th.Thread(target=_watch, daemon=True).start()
        log("UIA 看门狗已启动（每 90 秒探测一次）")
    except Exception as _e:
        log("UIA 看门狗启动失败：%s" % _e)

    backend = build_backend(cfg)
    # 缺 key 就早点说，别等第一条消息进来才发现
    if isinstance(backend, OpenAICompatBackend) and not os.environ.get(backend.key_env):
        log("缺少 API Key：环境里没有 %s" % backend.key_env)
        log("做法：把 .env.example 复制成 .env，填上 key；或 setx %s \"sk-...\"" % backend.key_env)
        return 1
    guard = Guard(cfg.get("limits") or {})
    dry_run = (not args.live) or args.dry_run

    if not (cfg.get("allow_friends") or cfg.get("allow_groups")):
        log("白名单为空 —— 不会回复任何人。先填 config.json。")
        return 1

    db, err = connect_db()
    if db is None:
        log("打开数据库失败：%s" % err)
        log("确认微信已登录并在运行（主密钥要从进程内存读）")
        return 1

    # 只取一次：见 one_round 的注释，反复调用会泄漏 contact.db 连接
    me: Dict[str, Any] = {}
    for attempt in range(3):
        try:
            me = db.get_self_info() or {}
            break
        except Exception as e:
            log("读取账号信息失败（第 %d 次）：%s" % (attempt + 1, e))
            try:
                db._invalidate_cache()
            except Exception:
                pass
            time.sleep(1.5)
    log("当前账号：%s / %s" % (me.get("nick_name"), short(me.get("username") or "")))
    my_wxid = me.get("username") or ""
    global _MY_WXID
    _MY_WXID = my_wxid
    if not wechat_running():
        log("警告：没检测到微信进程。现在只能读数据库，任何发送都会失败。")
        log("     ↳ 请先打开微信并登录小号，然后再重启本程序。")
    log("后端：%s ｜ 模式：%s ｜ 白名单好友 %d 个"
        % (backend.name, "LIVE（会真发）" if not dry_run else "dry-run（不发）",
           len(cfg.get("allow_friends") or [])))

    groups = resolve_groups(db, cfg)
    hist = History(cfg)
    member_cache: Dict[str, Dict[str, str]] = {}
    pending: Dict[str, Dict[str, Any]] = {}
    dcfg = cfg.get("debounce") or {}
    log("群聊节奏：%s ｜ 等对方补充 %.1fs（最多 %.1fs）｜ 回看最近 %.0fs / %d 条"
        % ("debounce 开" if dcfg.get("enabled", True) else "debounce 关",
           float(dcfg.get("wait_s") or 2.0), float(dcfg.get("max_wait_s") or 6.0),
           float(dcfg.get("lookback_s") or 300), int(dcfg.get("max_messages") or 40)))
    mem = load_memory(os.path.join(HERE, (cfg.get("memory") or {}).get("path") or "memory.md")) \
        if (cfg.get("memory") or {}).get("enabled", True) else {}
    n_items = sum(len(v) for v in mem.values())
    log("上下文：%s ｜ 私聊 %d 轮 ｜ 群 %d 条 ｜ 每会话上限 %d 字"
        % ("开" if hist.enabled else "关", hist.friend_turns, hist.group_msgs, hist.max_chars))
    log("记忆库：%s ｜ %d 节 / %d 条" % ("开" if n_items else "空（去填 memory.md）",
                                       len(mem), n_items))
    log("群白名单：%d 个%s" % (len(groups), "" if groups else "（config 里没配或没找到）"))
    startup_sync_stickers(db)
    for g in groups:
        log("  · %s  [%s]  @才回=%s  触发词=%s  间隔=%.0fs"
            % (g["name"], short(g["wxid"]),
               g["entry"].get("require_at", True),
               ",".join(g["entry"].get("triggers") or []) or "（任意@）",
               float(g["entry"].get("min_interval_s") or 60)))

    chats = [e["wxid"] for e in (cfg.get("allow_friends") or [])
             if isinstance(e, dict) and e.get("wxid")]
    chats += [g["wxid"] for g in groups]
    state = load_state()
    if not args.from_beginning:
        state = init_watermarks(db, chats, state)
        save_state(state)
    else:
        log("--from-beginning：忽略水位，回放历史（仅测试用，别配 --live）")
        if not dry_run:
            log("拒绝：--from-beginning 不能和 --live 同用")
            return 1

    # 休眠唤醒：积压跨了较长时间就只插一句，不逐条补回
    try:
        catch_up_wakeup(db, cfg, backend, guard, state, dry_run, my_wxid,
                        groups, hist, mem)
    except Exception as e:
        log("唤醒插话失败（忽略，继续正常循环）：%s" % e)

    interval = float(cfg.get("poll_interval_s", 2.0))
    fails = 0
    try:
        while True:
            try:
                n = one_round(db, cfg, backend, guard, state, dry_run,
                              args.from_beginning, my_wxid, hist, mem)
                _try_steal(db, cfg, groups, dry_run)
                n += one_round_groups(db, cfg, backend, guard, state, dry_run,
                                      args.from_beginning, my_wxid, groups, hist, mem,
                                      member_cache, pending)
                fails = 0
            except Exception as e:
                # 常驻进程不能因为一次锁冲突就死掉。
                # 典型：库自己 os.replace 临时解密文件时被残留句柄挡住（WinError 5）。
                fails += 1
                log("本轮异常（连续第 %d 次）：%s" % (fails, e))
                try:
                    db._invalidate_cache()      # 丢掉库内缓存的 sqlite 连接，释放文件句柄
                except Exception:
                    pass
                if args.once:
                    traceback.print_exc()
                    return 1
                n = 0
            if not args.from_beginning:
                save_state(state)
                hist.save()
            if args.once:
                log("本轮处理 %d 条，退出（--once）" % n)
                return 0
            # 连续失败时退避，别把微信数据库怼坏
            time.sleep(min(interval * (2 ** min(fails, 4)), 30) if fails else interval)
    except KeyboardInterrupt:
        log("已停止")
        save_state(state)
        return 130


if __name__ == "__main__":
    sys.exit(main())

# ---------------- P0：UIA 串行锁包装 ----------------
from uia_guard import uia_call  # noqa: E402
try:
    import uia_guard as _ug
    _ug.set_logger(log)
except Exception:
    pass

def uia_current_chat(ensure: bool = True) -> str:
    with uia_call("uia_current_chat"):
        return _impl_uia_current_chat(ensure)

def uia_session_items():
    with uia_call("uia_session_items"):
        return _impl_uia_session_items()

def uia_session_items_quiet():
    with uia_call("uia_session_items_quiet"):
        return _impl_uia_session_items_quiet()

def uia_heal(cfg=None) -> bool:
    with uia_call("uia_heal"):
        return _impl_uia_heal(cfg)

def uia_find_session(name: str):
    with uia_call("uia_find_session"):
        return _impl_uia_find_session(name)

def uia_click_session(name: str) -> bool:
    with uia_call("uia_click_session"):
        return _impl_uia_click_session(name)

def uia_open_chat(name: str, tries: int = 2, wxid: Optional[str] = None) -> bool:
    with uia_call("uia_open_chat"):
        return _impl_uia_open_chat(name, tries, wxid)

