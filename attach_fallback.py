# -*- coding: utf-8 -*-
"""两件事（用户 2026-10-23 确认）：
  1. 没说日期时，取「最近一次已经过去的那个时刻」，而不是直接放弃
     —— 例如现在 14:32 说"十二点半" → 今天 12:30；上午 10 点说 → 昨天 12:30
  2. 兜底：说了"我发的"但完全没时间时，给「你最近发过的两张图」
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# ---------- 1) 无日期词时取最近一次 ----------
OLD = '''    if day_off is None:
        return None, ""'''
NEW = '''    no_day = day_off is None          # 没说日期：后面按"最近一次"处理'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

OLD2 = '''    now = datetime.now()
    day = (now + timedelta(days=day_off)).replace(hour=hour, minute=minute,
                                                  second=0, microsecond=0)
    if day > now + timedelta(hours=1):        # 解析出未来时间，多半错了
        return None, ""
    note = "（今天是 %s，%s = %s）" % (now.strftime("%m-%d"), t.strip()[:12],
                                      day.strftime("%m-%d %H:%M"))
    return day, note'''
NEW2 = '''    now = datetime.now()
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
    return day, note'''
assert OLD2 in s
s = s.replace(OLD2, NEW2, 1)

# ---------- 2) 兜底：最近两张自己发的附件 ----------
FALLBACK = '''def recent_own_attachments(db, chat_wxid: str, wxid: str, nameof,
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
    return "【他最近发的附件（时间没对上，可作参考）】\\n" + "\\n".join(out)


'''
anchor = "def find_attachment_near("
assert anchor in s
s = s.replace(anchor, FALLBACK + anchor, 1)

# 调用处：目标时间为空时走兜底
OLD3 = '''                else:
                    q_for_prompt = merged'''
NEW3 = '''                else:
                    q_for_prompt = merged
                    try:
                        only2, why2 = parse_sender_constraint(
                            merged, asker, name2wx, asker_wxid_early)
                        if only2:
                            fb = recent_own_attachments(db, wxid, only2, nameof, my_wxid, 2)
                            if fb:
                                ctx["attach_hint"] = fb
                                log("附件兜底：没时间，按「%s」给最近两张" % why2)
                    except Exception as e:
                        log("附件兜底失败（忽略）：%s" % e)'''
assert OLD3 in s
s = s.replace(OLD3, NEW3, 1)

# name2wx / asker_wxid 需要在这条分支也能用：提前到 try 外面算好
OLD4 = '''            try:
                target, note = parse_relative_time(merged or "")'''
NEW4 = '''            name2wx: Dict[str, str] = {}
            try:
                for nm_, wx_ in group_member_index(db, wxid, {}).items():
                    name2wx[nm_] = wx_
                for wx_, nm_ in mem_nick(db).items():
                    name2wx.setdefault(nm_, wx_)
            except Exception:
                pass
            asker_wxid_early = ent.get("asker_wxid") or ""
            try:
                target, note = parse_relative_time(merged or "")'''
assert OLD4 in s
s = s.replace(OLD4, NEW4, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "def recent_own_attachments" in t and "asker_wxid_early" in t
print("最近一次解析 + 兜底 已实现")
