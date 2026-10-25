# -*- coding: utf-8 -*-
"""附件时间匹配：把「昨晚七点」换算成绝对时间，点名最相关的那一条附件。

实测背景：14 张图描述全在、索引也拼进去了，模型仍答"不知道"。
三轮对照实验后确认，缺的是两样：
  ① 相对时间归一化（模型算不清"昨晚七点" = 10-22 19:00）
  ② 明确点名最相关的那一条（混在一堆附件里它挑不出来）
加上这两样后 dry-run 3/3 答对。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

HELPER = '''CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
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
    for word, off in (("前天", -2), ("大前天", -3), ("昨天", -1), ("昨晚", -1),
                      ("昨晚", -1), ("今早", 0), ("今天", 0), ("今晚", 0),
                      ("刚才", 0), ("刚刚", 0)):
        if word in t:
            day_off = off
            break
    if day_off is None:
        return None, ""

    period = ""
    for w in ("凌晨", "早上", "早晨", "上午", "中午", "下午", "傍晚", "晚上", "夜里"):
        if w in t:
            period = w
            break

    m = re.search(r"(\\d{1,2})\\s*[:：点时]\\s*(半|\\d{1,2})?", t)
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
        m2 = re.search(r"([一二两三四五六七八九十]{1,2})\\s*[点时]", t)
        if m2:
            hour = _cn_to_int(m2.group(1))
            minute = 0

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
    day = (now + timedelta(days=day_off)).replace(hour=hour, minute=minute,
                                                  second=0, microsecond=0)
    if day > now + timedelta(hours=1):        # 解析出未来时间，多半错了
        return None, ""
    note = "（今天是 %s，%s = %s）" % (now.strftime("%m-%d"), t.strip()[:12],
                                      day.strftime("%m-%d %H:%M"))
    return day, note


def find_attachment_near(db, chat_wxid: str, target: datetime, nameof,
                         my_wxid: str, asker: str = "",
                         window_min: int = 60) -> str:
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
        delta = abs((when - target).total_seconds()) / 60.0
        if delta > window_min:
            continue
        score = delta - (5 if (asker and (nameof(r.get("sender_username") or "") == asker)) else 0)
        if best is None or score < best[0]:
            best = (score, when, r)

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


'''

anchor = "def attachment_index("
assert anchor in s
s = s.replace(anchor, HELPER + anchor, 1)

# 引入 timedelta
if "from datetime import datetime" in s and "timedelta" not in s:
    s = s.replace("from datetime import datetime", "from datetime import datetime, timedelta", 1)

# 群回复：算时间提示 + 最相关附件，并把归一化说明拼进问题
OLD = '''        # 问题里提到图/照片/几点之类，就给一份附件索引（附件在长场景里太不显眼）
        if ATTACH_HINT_RE.search(merged or ""):
            try:
                idx = attachment_index(db, wxid, nameof, my_wxid, 12)
                if idx:
                    ctx["attachments"] = idx
            except Exception as e:
                log("附件索引失败（忽略）：%s" % e)'''
NEW = '''        # 问题里提到图/照片/几点之类，就给附件索引 + 点名最相关的一条
        if ATTACH_HINT_RE.search(merged or ""):
            try:
                idx = attachment_index(db, wxid, nameof, my_wxid, 12)
                if idx:
                    ctx["attachments"] = idx
            except Exception as e:
                log("附件索引失败（忽略）：%s" % e)
            try:
                target, note = parse_relative_time(merged or "")
                if target:
                    q_for_prompt = merged + note       # 把"昨晚七点=10-22 19:00"给模型
                    hit = find_attachment_near(db, wxid, target, nameof, my_wxid, asker)
                    if hit:
                        ctx["attach_hint"] = hit
                        log("附件定位：%s → %s" % (note.strip("（）"), hit[14:60]))
                else:
                    q_for_prompt = merged
            except Exception as e:
                q_for_prompt = merged
                log("附件定位失败（忽略）：%s" % e)
        else:
            q_for_prompt = merged'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

# 问题文本用 q_for_prompt
OLD2 = '''                                "【群里刚才的对话】\\n%s\\n\\n【现在 @ 你的是 %s】\\n%s"
                                % (scene, asker, merged)}]'''
NEW2 = '''                                "【群里刚才的对话】\\n%s\\n\\n【现在 @ 你的是 %s】\\n%s"
                                % (scene, asker, q_for_prompt)}]'''
assert OLD2 in s
s = s.replace(OLD2, NEW2, 1)

# reply(): 把点名提示拼进 user
OLD3 = '''        att = (ctx.get("attachments") or "").strip()
        if att:
            msgs.append({"role": "user", "content": att})'''
NEW3 = '''        hint = (ctx.get("attach_hint") or "").strip()
        if hint:
            msgs.append({"role": "user", "content": hint})
        att = (ctx.get("attachments") or "").strip()
        if att:
            msgs.append({"role": "user", "content": att})'''
assert OLD3 in s
s = s.replace(OLD3, NEW3, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "def parse_relative_time" in t and "attach_hint" in t
print("时间归一化 + 点名最相关附件 已加入")
