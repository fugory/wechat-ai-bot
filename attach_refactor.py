# -*- coding: utf-8 -*-
"""重构附件块：先算「时间 + 发送者约束」，再按约束生成索引。

实测：索引里留着程 12:30 那张，模型就会推翻点名（1/3）。
索引也按发送者过滤后 → 3/3。

新顺序：
  1. parse_relative_time  → 目标时间
  2. parse_sender_constraint → 指认的发送者（可能为空）
  3. attachment_index(only_sender=...) → 索引按约束过滤
  4. 有目标时间 → 点名最相关；没有 → 兜底给最近两条
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) attachment_index 支持 only_sender
OLD_SIG = '''def attachment_index(db, chat_wxid: str, nameof, my_wxid: str,
                     limit: int = 12, cache: Optional[Dict] = None) -> str:'''
NEW_SIG = '''def attachment_index(db, chat_wxid: str, nameof, my_wxid: str,
                     limit: int = 12, cache: Optional[Dict] = None,
                     only_sender: str = "") -> str:'''
assert OLD_SIG in s
s = s.replace(OLD_SIG, NEW_SIG, 1)

OLD_FILTER = '''        ts = int(r.get("create_time") or 0)
        sender = r.get("sender_username") or ""
        who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        if rtype == "图片":'''
NEW_FILTER = '''        ts = int(r.get("create_time") or 0)
        sender = r.get("sender_username") or ""
        if only_sender and sender != only_sender:
            continue                      # 用户指认了是谁，索引里也只留他的
        who = "我" if (my_wxid and sender == my_wxid) else (nameof(sender) or "有人")
        if rtype == "图片":'''
assert OLD_FILTER in s
s = s.replace(OLD_FILTER, NEW_FILTER, 1)

# 2) 重构调用块
START = s.find('        # 问题里提到图/照片/几点之类，就给附件索引 + 点名最相关的一条')
END = s.find('        else:\n            q_for_prompt = merged', START)
assert START > 0 and END > START
END = END + len('        else:\n            q_for_prompt = merged')

NEW_BLOCK = '''        # 附件：先算「时间 + 指认的发送者」，再按约束生成索引 + 点名最相关的一条
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
            log("附件处理异常（忽略）：%s" % e)'''

s = s[:START] + NEW_BLOCK + s[END:]

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "only_sender=only" in t and "attachment_index(db, wxid, nameof, my_wxid, 12, None, only)" in t
print("附件块已重构")
