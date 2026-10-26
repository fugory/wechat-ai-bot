# -*- coding: utf-8 -*-
"""记忆系统 L2/L3（M1：先做画像层）。

设计要点
--------
* 独立 memory.db，不碰 memory.md（手写补充继续有效）
* 画像按 scope 分：group（群卡）/ person（人卡）/ private（私聊卡）
* 触发用"从数据库算"，不靠事件计数 → 不会重复、重启也不丢
* 每条画像记录它覆盖到的最新消息时间，避免重复抽取
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "memory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    scope      TEXT NOT NULL,
    owner      TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT 0,
    msg_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, owner)
);
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    owner      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'fact',
    text       TEXT NOT NULL,
    src_msg_id TEXT,
    created_at INTEGER NOT NULL DEFAULT 0,
    used_count INTEGER NOT NULL DEFAULT 0,
    last_used  INTEGER NOT NULL DEFAULT 0,
    weight     REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(scope, owner);
CREATE TABLE IF NOT EXISTS tasks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner    TEXT NOT NULL,
    text     TEXT NOT NULL,
    due_at   INTEGER,
    state    TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS vec (
    fact_id INTEGER PRIMARY KEY,
    dim     INTEGER NOT NULL,
    blob    BLOB NOT NULL
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False：主循环、看门狗线程都会用它（写入有 _DB_LOCK 保护）
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------- 状态（每个频道的"抽到哪了"） ----------------

def get_mark(conn: sqlite3.Connection, scope: str, owner: str) -> int:
    row = conn.execute("SELECT value FROM state WHERE key=?",
                       ("mark:%s:%s" % (scope, owner),)).fetchone()
    try:
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def set_mark(conn: sqlite3.Connection, scope: str, owner: str, ts: int) -> None:
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?,?)",
                 ("mark:%s:%s" % (scope, owner), str(int(ts))))
    conn.commit()


# ---------------- 画像 ----------------

def get_profile(conn: sqlite3.Connection, scope: str, owner: str) -> str:
    row = conn.execute("SELECT text FROM profiles WHERE scope=? AND owner=?",
                       (scope, owner)).fetchone()
    return (row["text"] if row else "") or ""


def set_profile(conn: sqlite3.Connection, scope: str, owner: str,
                text: str, msg_count: int = 0) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO profiles(scope, owner, text, updated_at, msg_count)"
        " VALUES(?,?,?,?,?)",
        (scope, owner, text.strip(), int(time.time()), int(msg_count)))
    conn.commit()


def all_profiles(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM profiles ORDER BY updated_at DESC").fetchall()


# ---------------- 注入用的渲染 ----------------

def render_block(conn: sqlite3.Connection, scope: str, owner: str,
                 extra: Optional[List[Tuple[str, str]]] = None,
                 max_chars: int = 420) -> str:
    """渲染注入文本。extra = [(标签, 另一张卡的 owner)]，用于私聊带群记忆。"""
    parts = []
    main = get_profile(conn, scope, owner)
    if main:
        label = {"group": "关于这个群", "person": "关于这个人",
                 "private": "关于这个私聊"}.get(scope, "记忆")
        parts.append("【%s】\n%s" % (label, main.strip()))
    for tag, (s2, o2) in [("来自群聊", x) for x in (extra or [])]:
        t = get_profile(conn, s2, o2)
        if t:
            parts.append("【%s的记忆 · 转述时注意分寸】\n%s" % (tag, t.strip()))
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


# ---------------- 画像 prompt ----------------

PROFILE_PROMPT = """你在帮一个群聊机器人整理"记忆卡"。

下面是%s，请输出一张**不超过 300 字**的记忆卡，供它以后接话时参考。

要求：
1. 只写有长期价值的信息：谁是谁、脾气、习惯、反复出现的梗、正在进行的约定。
2. 不要复述具体某一句对话，不要写"刚刚说了什么"这类会过期的内容。
3. 不确定的不要写；宁可少写。
4. 用短句、分行，不要 markdown 标题。
5. 直接输出记忆卡正文，不要任何解释或前缀。
6. **一律用对话里出现的昵称称呼人，绝对不要写 wxid_ 开头的字符串**；
   如果实在不知道某人的昵称，就写"某位群友"，不要编。
7. 总长度不超过 300 字。

%s

【对话片段】
%s
"""

OLD_HINT = """
【已有的记忆卡】（可更新、可补充，不要整段推翻）
%s
"""


def build_profile(conn: sqlite3.Connection, scope: str, owner: str,
                  channel_desc: str, transcript: str,
                  llm_call) -> str:
    """调用模型生成画像。llm_call(prompt) -> str"""
    old = get_profile(conn, scope, owner)
    prompt = PROFILE_PROMPT % (channel_desc, (OLD_HINT % old) if old else "",
                               transcript[:6000])
    try:
        out = (llm_call(prompt) or "").strip()
    except Exception:
        return ""
    out = out.strip().strip('"').strip()
    return out


# ---------------- 触发判定 ----------------

def due(n_new: int, idle_s: float, cfg: Dict) -> bool:
    """三条件触发：爆发批量 / 静默补齐 / 每日兜底。"""
    tg = (cfg.get("triggers") or {})
    batch = int(tg.get("batch") or 30)
    idle_min = int(tg.get("idle_min") or 10)
    idle_msgs = int(tg.get("idle_msgs") or 5)
    if n_new >= batch:
        return True
    if n_new >= idle_msgs and idle_s >= idle_min * 60:
        return True
    return False


# ==================== M2 事实层 ====================

def add_fact(conn, scope: str, owner: str, text: str, kind: str = "fact",
             src_msg_id: str = "") -> int:
    text = (text or "").strip()
    if len(text) < 4:
        return 0
    # 去重：同一 owner 下文本相同就不重复插
    row = conn.execute(
        "SELECT id FROM facts WHERE scope=? AND owner=? AND text=?",
        (scope, owner, text)).fetchone()
    if row:
        return 0
    cur = conn.execute(
        "INSERT INTO facts(scope, owner, kind, text, src_msg_id, created_at,"
        " used_count, last_used, weight) VALUES(?,?,?,?,?,?,0,0,1.0)",
        (scope, owner, kind, text[:120], src_msg_id, int(time.time())))
    conn.commit()
    return cur.lastrowid


def list_facts(conn, scope: str, owner: str, limit: int = 200):
    return conn.execute(
        "SELECT * FROM facts WHERE scope=? AND owner=? ORDER BY id DESC LIMIT ?",
        (scope, owner, limit)).fetchall()


def delete_fact(conn, fid: int) -> bool:
    cur = conn.execute("DELETE FROM facts WHERE id=?", (int(fid),))
    conn.execute("DELETE FROM vec WHERE fact_id=?", (int(fid),))
    conn.commit()
    return cur.rowcount > 0


def touch_facts(conn, ids) -> None:
    """被引用过的事实：计数 +1、刷新 last_used（用进废退的'进'）。"""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return
    q = ",".join("?" * len(ids))
    conn.execute(
        "UPDATE facts SET used_count=used_count+1, last_used=? WHERE id IN (%s)" % q,
        [int(time.time())] + ids)
    conn.commit()


def _bigrams(text: str):
    t = "".join(ch for ch in (text or "") if ch.strip())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def search_facts(conn, scope: str, owner: str, query: str, top_k: int = 5,
                 vec_score=None):
    """关键词 + 新鲜度 + 使用次数；给了 vec_score 就混进向量分。

    vec_score: dict(fact_id -> 0..1)
    返回 [(fact_row, score)]
    """
    rows = list_facts(conn, scope, owner, 400)
    if not rows:
        return []
    qb = _bigrams(query)
    now = time.time()
    scored = []
    for r in rows:
        text = r["text"] or ""
        fb = _bigrams(text)
        kw = 0.0
        inter = 0
        if qb and fb:
            inter = len(qb & fb)
            # 覆盖查询的程度更重要，其次才看事实被覆盖多少
            kw = 0.75 * (inter / len(qb)) + 0.25 * (inter / len(fb))
        age_d = max(0.0, (now - (r["created_at"] or now)) / 86400.0)
        fresh = 0.5 ** (age_d / 30.0)          # 30 天半衰
        used = min(1.0, (r["used_count"] or 0) / 5.0)
        vec = float((vec_score or {}).get(r["id"], 0.0))
        # 必须有证据才参与：关键词重叠或向量相似，否则新鲜度会把无关事实顶上来
        if vec_score:
            if vec < 0.05 and (inter < 2 or kw < 0.02):
                continue
            score = 0.60 * vec + 0.30 * kw + 0.10 * fresh
        else:
            # 至少 2 个二元组重叠才算命中（单个重叠太容易误伤）
            if inter < 2 or kw < 0.05:
                continue
            score = 0.70 * kw + 0.20 * fresh + 0.10 * used
        scored.append((r, score))
    if vec_score and scored:
        # 向量尺度上：正确命中 ~0.65+，噪声 <=0.45
        # 用「绝对下限 + 相对领先」双门槛，避免弱相关被塞进来
        best_vec = max(float(vec_score.get(r["id"], 0.0)) for r, _s in scored)
        floor = max(0.50, 0.75 * best_vec)
        scored = [(r, s) for r, s in scored
                  if float(vec_score.get(r["id"], 0.0)) >= floor]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


def render_facts(conn, scope: str, owner: str, query: str, top_k: int = 5,
                 max_chars: int = 320, vec_score=None) -> str:
    """渲染检索到的事实，供注入。会把用到的事实计数 +1。"""
    hits = search_facts(conn, scope, owner, query, top_k, vec_score)
    if not hits:
        return ""
    lines, ids, total = [], [], 0
    for r, _sc in hits:
        line = "· " + (r["text"] or "").strip()
        if total + len(line) > max_chars:
            break
        lines.append(line)
        ids.append(r["id"])
        total += len(line) + 1
    if not lines:
        return ""
    touch_facts(conn, ids)
    return "【相关记忆】\n" + "\n".join(lines)


# ==================== M2 任务/约定 ====================

def add_task(conn, owner: str, text: str, due_at: int = None) -> int:
    cur = conn.execute(
        "INSERT INTO tasks(owner, text, due_at, state, created_at) VALUES(?,?,?,?,?)",
        (owner, (text or "")[:200], due_at, "pending", int(time.time())))
    conn.commit()
    return cur.lastrowid


def due_tasks(conn, owner: str, now: int = None):
    now = int(now or time.time())
    return conn.execute(
        "SELECT * FROM tasks WHERE owner=? AND state='pending'"
        " AND due_at IS NOT NULL AND due_at<=? ORDER BY due_at", (owner, now)).fetchall()


def close_task(conn, tid: int) -> None:
    conn.execute("UPDATE tasks SET state='done' WHERE id=?", (int(tid),))
    conn.commit()


# ==================== M3 向量 ====================
# 说明：向量只做"额外加分"，拿不到就退化到纯关键词，不影响主流程。

def set_vec(conn, fact_id: int, vec) -> None:
    import array
    arr = array.array("f", [float(x) for x in vec])
    conn.execute("INSERT OR REPLACE INTO vec(fact_id, dim, blob) VALUES(?,?,?)",
                 (int(fact_id), len(arr), arr.tobytes()))
    conn.commit()


def get_vec(conn, fact_id: int):
    import array
    row = conn.execute("SELECT dim, blob FROM vec WHERE fact_id=?",
                       (int(fact_id),)).fetchone()
    if not row:
        return None
    arr = array.array("f")
    arr.frombytes(row["blob"])
    return list(arr)


def all_vecs(conn, scope: str, owner: str):
    rows = conn.execute(
        "SELECT f.id AS id, v.dim AS dim, v.blob AS blob FROM facts f"
        " JOIN vec v ON v.fact_id=f.id WHERE f.scope=? AND f.owner=?",
        (scope, owner)).fetchall()
    import array
    out = {}
    for r in rows:
        arr = array.array("f")
        arr.frombytes(r["blob"])
        out[r["id"]] = list(arr)
    return out


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return max(0.0, min(1.0, s / (na * nb)))


FACT_PROMPT = """从下面的对话里，抽出**值得长期记住的事实**，每行一条。

只抽这几类：
- 某个人的偏好/习惯/身体状况（如"不吃香菜""喝西梅汁会闹肚子"）
- 约定和计划（如"周三交材料"）
- 反复出现的梗或称呼
- 明确的身份信息（谁是谁、做什么的）

不要抽：日常寒暄、情绪、一次性的玩笑、聊天里没有确认的猜测。

格式：每行 `对象 | 事实`，对象是对话里出现的昵称（不要写 wxid）。
最多 8 行。没有可抽的就输出一个空行。

【对话片段】
%s
"""


def parse_facts(text: str):
    """把模型输出解析成 [(subject, fact)]。"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-·* ").strip()
        if not line or "|" not in line:
            continue
        subj, fact = line.split("|", 1)
        subj, fact = subj.strip()[:16], fact.strip()[:100]
        if len(fact) >= 4:
            out.append((subj, fact))
    return out[:8]
