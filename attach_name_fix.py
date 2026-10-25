# -*- coding: utf-8 -*-
"""修约束解析的两个盲区：
  1. 名字来源不全：group_member_index 只有 12 个标准成员名，
     而场景里实际出现的「程」来自 nickname_map。两者要合并。
  2. 单字名（如「程」）被 len>=2 过滤掉了；改成允许单字，但要求它处于
     "名字位置"（句首或标点后），且后面紧跟时间/动作词——避免「过程」这类误判。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\bridge.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) 名字匹配支持单字 + 位置保护
OLD = '''    best = ("", "")
    for name, wxid in (member_index or {}).items():
        if not name or len(name) < 2 or name not in t:
            continue
        i = t.find(name)
        window = t[max(0, i - 6): i + len(name) + 8]
        if NEAR_ATTACH_RE.search(window):
            if len(name) > len(best[1]):
                best = (wxid, name)'''
NEW = '''    best = ("", "")
    NAME_TAIL_RE = re.compile(r"^(?:昨天|今天|前天|刚才|早上|中午|下午|晚上|\\s*发|\\s*拍|\\s*的|\\s*那|\\s*在)")
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
                best = (wxid, name)'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

# 2) 调用处：合并两个名字来源
OLD_CALL = '''                    only, why = parse_sender_constraint(
                        merged, asker, group_member_index(db, wxid, {}), my_wxid)'''
NEW_CALL = '''                    name2wx: Dict[str, str] = {}
                    for nm_, wx_ in group_member_index(db, wxid, {}).items():
                        name2wx[nm_] = wx_
                    for wx_, nm_ in mem_nick(db).items():
                        name2wx.setdefault(nm_, wx_)   # 补上 nickname_map 里的名字（如「程」）
                    only, why = parse_sender_constraint(
                        merged, asker, name2wx, my_wxid)'''
assert OLD_CALL in s
s = s.replace(OLD_CALL, NEW_CALL, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)

with io.open(P, "r", encoding="utf-8") as f:
    t = f.read()
assert "NAME_TAIL_RE" in t and "name2wx" in t
print("名字来源合并 + 单字名位置保护 已完成")
