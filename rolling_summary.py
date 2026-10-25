# -*- coding: utf-8 -*-
"""滚动摘要：把滑出窗口的旧对话压成一行，长期留在上下文顶部。

为什么需要：群场景只有 6000 字，滑动窗口一过半，早先的话题就彻底丢了。
有了这一层，模型至少知道"之前聊过什么、结论是什么"。

格式（CHAT-FORMAT.md 摘要层）：
    10-23 群「小团体」：讨论了换显卡，结论等双十一；周末可能爬山，未定。

成本：每积攒 min_batch 条才更新一次，单次约 500 tokens ≈ 0.02 分，可以忽略。
"""
import json
import os
import sys
import time
from typing import Callable, Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "chat_summary.json")

DEFAULTS = {
    "enabled": True,
    "min_batch": 25,        # 滑出这么多条才更新一次摘要
    "max_messages": 80,     # 单次喂给模型的最多旧消息数
    "max_chars": 120,       # 摘要正文上限
}


def _load() -> Dict:
    try:
        with open(PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save(d: Dict) -> None:
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def summary_line(chat_wxid: str) -> str:
    """取这个会话的摘要行；没有就返回空串。"""
    d = _load()
    item = d.get(chat_wxid) or {}
    text = (item.get("summary") or "").strip()
    return "（更早：%s）" % text if text else ""


def maybe_update(db, cfg, backend, chat_wxid: str, chat_name: str,
                 window_start_seq: int, nameof: Callable[[str], str],
                 my_wxid: str, log: Optional[Callable[[str], None]] = None) -> bool:
    """把「窗口之前、上次摘要之后」的消息并入滚动摘要。

    window_start_seq：本次场景里最旧那条的 seq。比它更旧的就是"滑出去了"。
    """
    sc = dict(DEFAULTS)
    sc.update(cfg.get("summary") or {})
    if not sc.get("enabled", True):
        return False
    if not window_start_seq:
        return False

    d = _load()
    item = d.get(chat_wxid) or {}
    through = int(item.get("through_seq") or 0)
    if window_start_seq <= through:
        return False                 # 没滑出新内容

    try:
        rows = db.get_new_messages(chat_wxid, since_seq=through, limit=400) or []
    except Exception:
        return False
    old = [r for r in rows if int(r.get("sort_seq") or 0) < window_start_seq]
    if len(old) < int(sc.get("min_batch") or 25):
        return False

    old = old[-int(sc.get("max_messages") or 80):]
    lines = []
    last_seq = through
    for r in old:
        if r.get("type") not in ("文本", "动画表情"):
            continue
        sender, text = "", ""
        try:
            import bridge  # 延迟导入，避免循环依赖
            sender, text = bridge.split_group_content(
                r.get("content") or "", r.get("sender_username") or "")
        except Exception:
            text = str(r.get("content") or "")
        if r.get("type") == "动画表情":
            text = "[表情]"
        text = " ".join((text or "").split())[:80]
        if not text:
            last_seq = max(last_seq, int(r.get("sort_seq") or 0))
            continue
        who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        lines.append("【%s】%s" % (who, text))
        last_seq = max(last_seq, int(r.get("sort_seq") or 0))

    if len(lines) < 3:
        return False

    prev = (item.get("summary") or "").strip()
    prompt = (
        "下面是微信群的对话片段。请把它压成**一句话**，不超过 %d 字，"
        "保留：在聊什么、谁的态度/结论、有没有没定下来的事。"
        "不要逐条复述，不要客套，不要markdown。只输出这一句话。\n\n"
        "%s%s"
    ) % (int(sc.get("max_chars") or 120),
         ("【已有摘要（更早的事，可合并）】%s\n\n" % prev) if prev else "",
         "\n".join(lines))

    try:
        summary = backend.reply(prompt, {"kind": "group", "system": "你是一个只做摘要的助手。"})
    except Exception as e:
        if log:
            log("摘要更新失败：%s" % e)
        return False
    summary = " ".join((summary or "").split()).strip()
    if not summary:
        return False
    summary = summary[:int(sc.get("max_chars") or 120)]
    # 统一成 CHAT-FORMAT 摘要层格式
    day = time.strftime("%m-%d")
    d[chat_wxid] = {
        "name": chat_name,
        "summary": summary,
        "through_seq": last_seq,
        "updated_at": time.strftime("%F %T"),
        "day": day,
    }
    _save(d)
    if log:
        log("滚动摘要已更新（%d 条并入）：%s" % (len(lines), summary[:40]))
    return True
