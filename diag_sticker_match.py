# -*- coding: utf-8 -*-
"""诊断：某个表情为什么在面板里匹配不上。

步骤：
1. 打开面板 → 切收藏 tab → 滚动到顶
2. 切格 → 每格与目标哈希比距离
3. 打印前 5 名 + 面板里实际有多少格

用法：python diag_sticker_match.py <md5前8位或全量>
"""
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "gui")

import bridge as b
import sticker_match as sm
from wxbg import PANEL_TAB_STICKER

b.load_dotenv(r"D:\dsh\projects\ai-chat-bot\.env")
db = b._shared_db()

want = (sys.argv[1] if len(sys.argv) > 1 else "2c8e167f").lower()
if len(want) < 32:
    order = b.sticker_order_md5s(db)
    hit = [m for m in order if m.startswith(want)]
    if not hit:
        print("收藏里没有这个 md5 前缀")
        raise SystemExit(1)
    want = hit[0]
print("目标:", want)

hs = sm.local_hashes(want)
print("本地哈希:", len(hs), hs[:3])

bg = b.get_bg()
bg.refresh_window()
bg.activate()
time.sleep(0.6)
if bg.find_emoji_panel():
    bg.close_emoji_panel()
    time.sleep(0.5)
if not b.uia_open_chat("文件传输助手", wxid="filehelper"):
    print("切会话失败")
    raise SystemExit(1)
time.sleep(0.8)
bg.refresh_window()
bg.activate()
time.sleep(0.4)

hw = sm._panel_window(bg)
print("面板:", hw)
if not hw:
    raise SystemExit(1)
try:
    bg._click_panel_at(hw, *PANEL_TAB_STICKER)
    time.sleep(1.0)
    sm.panel_scroll_top(hw)
    time.sleep(0.6)
    sm.park_cursor(hw)
    cells, _grid = sm.panel_cells(bg, hw)
    print("格子数:", len(cells))
    if not cells:
        raise SystemExit(1)
    rows = []
    for i, cx, cy, crop in cells:
        d = min(sm.hamming(th, sm.dhash_trim(crop)) for th in hs)
        rows.append((d, i, cx, cy))
    rows.sort()
    print("距离最近的 6 格：")
    for d, i, cx, cy in rows[:6]:
        print("   距离 %2d  第%d行%d列  (面板内 %d,%d)" % (d, i // 5, i % 5, cx, cy))
    # 把最近那格存下来，肉眼看看是什么
    top = rows[0]
    for i, cx, cy, crop in cells:
        if i == top[1]:
            crop.save(r"D:\dsh\scratch\closest_cell.png")
            print("最近一格已存 scratch\\closest_cell.png")
            break
finally:
    bg.close_emoji_panel()
