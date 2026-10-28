# -*- coding: utf-8 -*-
"""修"坐标表越用越少"：失败时不要删条目。

现象：map 从 25 条掉到 23 条，某些表情永远没有坐标 → 永远走匹配 → 永远失败。

根因：失败分支里 `mm.pop(want)` 把条目删了，指纹却没变（仍然"不过期"），
      于是那条永远缺失，也不会触发重建。

修法：
  1. 删掉两处 pop（坐标表应当始终等于收藏集合）
  2. send_by_map 里"找不到条目"时，先按数据库顺序补一次再继续
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) 去掉两处删除
for old in ("                    mm = load_map()\n                    mm.pop(want, None)\n                    save_map(mm)\n",
            "                    mm2 = load_map()\n                    mm2.pop(want, None)\n                    save_map(mm2)\n"):
    if old in s:
        s = s.replace(old, "", 1)
        print("已移除一处 pop")

# 兜底：还有零散 pop 直接注释掉
if "pop(want" in s:
    import re
    s = re.sub(r"^(\s*)(mm2?\.pop\(want, None\))$",
               r"\1# \2   # 不要删：删了这条就永远没坐标了", s, flags=re.M)
    print("剩余 pop 已注释")

# 2) 找不到条目时补一次
OLD = '''    m = load_map().get(want)
    if not m:
        return False'''
NEW = '''    m = load_map().get(want)
    if not m:
        # 坐标表缺这条（历史遗留）：按数据库顺序补一次再试
        b.log("坐标表缺 %s，按收藏顺序补一次" % want[:8])
        sync_map_from_db(db)
        m = load_map().get(want)
    if not m:
        return False'''
if OLD in s:
    s = s.replace(OLD, NEW, 1)
    print("send_by_map: 缺条目自动补齐")
else:
    print("⚠ send_by_map 的取条目代码没匹配上")

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("完成")
