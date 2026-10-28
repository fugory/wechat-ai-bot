# -*- coding: utf-8 -*-
"""坐标表自动重建：收藏一变就按数据库顺序重算，不再靠点格子。

背景：map_by_send 逐格点过 25 次，结果与数据库收藏顺序**完全一致**，
说明"面板第 i 格 = 数据库第 i 个"。既然如此，重建不需要点击。

安全性：send_by_map 点之前会校验那一格的图像（距离 >12 就不点），
所以即使顺序假设错了，最坏情况是"发不出"，不会"发错"。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

SYNC = '''def sync_map_from_db(db) -> dict:
    """按数据库收藏顺序重算坐标表（不点格子），并写入指纹。"""
    try:
        order = b.sticker_order_md5s(db)
    except Exception:
        return {}
    if not order:
        return {}
    mp = {}
    for i, md5 in enumerate(order):
        mp[(md5 or "").lower()] = {"row": i // 5, "col": i % 5}
    try:
        mp["_fingerprint"] = map_fingerprint(db)
    except Exception:
        pass
    save_map(mp)
    b.log("坐标表已按收藏顺序重建：%d 条" % len(order))
    return mp


'''

if "def sync_map_from_db" not in s:
    anchor = "def map_fingerprint(db) -> str:"
    assert anchor in s
    s = s.replace(anchor, SYNC + anchor, 1)

OLD = '''    try:
        if map_is_stale(db):
            b.log("坐标表已过期（收藏变了）：不用缓存坐标，改走面板匹配")
            return False
    except Exception:
        pass'''
NEW = '''    try:
        if map_is_stale(db):
            # 收藏变了：坐标整体位移→按数据库顺序重算（不点击），然后继续用缓存
            old_n = len([k for k in load_map() if not k.startswith("_")])
            new_map = sync_map_from_db(db)
            new_n = len([k for k in new_map if not k.startswith("_")])
            b.log("坐标表已过期（收藏变了）：按数据库顺序重建 %d→%d 条" % (old_n, new_n))
    except Exception as _e:
        b.log("坐标表重建失败（转匹配）：%s" % _e)
        return False'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: 坐标表可自动重建")
