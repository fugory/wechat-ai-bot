# -*- coding: utf-8 -*-
"""修表情发送的两处硬伤：

1) 「翻页再找」是死代码
   原来 best > 12 就 `return False` 了，后面那段翻页逻辑永远执行不到。
   所以只要目标不在第一页，就一定失败。
   改：先翻页（最多 3 页）取全局最优，仍不合格才放弃。

2) 坐标表没有和收藏绑定
   收藏一变，map 里的行列就全错，还会按错的坐标"发出去"。
   改：map 里写指纹；发送前校验指纹，不一致就不用 map（转匹配），
       绝不拿过期坐标乱点。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# ---------- 1. 去掉"第一页不合格就放弃"，改成翻页 ----------
OLD = '''    best = scored[0][0]
    b.log("面板匹配：共 %d 格，最佳距离 %d" % (len(cells), best))
    if best > 12:
        # 不够像就宁可放弃：错发比不发更糟
        bg.close_emoji_panel()
        b.log("面板里没有这张（最佳距离 %d 过大），放弃发送" % best)
        return False

    pages = []
    pages.append(scored)
    # 第一页不够像 → 往后翻页再试
    for _page in range(2):
        if pages[-1] and pages[-1][0][0] <= 12:
            break
        panel_scroll(hw, up=False, times=3)
        time.sleep(0.5)
        more, _g2 = panel_cells(bg, hw)
        if not more:
            break
        scored2 = sorted(((min(hamming(th, dhash_trim(crop)) for th in target_hashes),
                           i, cx, cy, crop)
                          for i, cx, cy, crop in more), key=lambda x: x[0])
        # 只有在比上一页更像时才用新页
        if scored2 and (not pages[-1] or scored2[0][0] < pages[-1][0][0]):
            pages.append(scored2)
        else:
            break

    best_page = min(pages, key=lambda pg: pg[0][0] if pg else 99)
    scored = best_page
'''

NEW = '''    best = scored[0][0]
    b.log("面板匹配：第 1 页共 %d 格，最佳距离 %d" % (len(cells), best))

    # 第一页不够像 → 真的翻页再找（之前这里提前 return，翻页是死代码）
    pages = [scored]
    for _page in range(2):
        if best <= 12:
            break
        panel_scroll(hw, up=False, times=3)
        time.sleep(0.6)
        more, _g2 = panel_cells(bg, hw)
        if not more:
            break
        scored2 = sorted(((min(hamming(th, dhash_trim(crop)) for th in target_hashes),
                           i, cx, cy, crop)
                          for i, cx, cy, crop in more), key=lambda x: x[0])
        b.log("  翻到第 %d 页：共 %d 格，最佳距离 %d"
              % (len(pages) + 1, len(more), scored2[0][0] if scored2 else 99))
        if scored2 and scored2[0][0] < best:
            pages.append(scored2)
            best = scored2[0][0]
        else:
            break

    if best > 12:
        bg.close_emoji_panel()
        b.log("面板里没有这张（翻完 %d 页，最佳距离 %d 过大），放弃发送"
              % (len(pages), best))
        return False

    # 翻过页就回到顶部，避免点错行
    panel_scroll_top(hw)
    time.sleep(0.4)
    cells2, _g3 = panel_cells(bg, hw)
    if cells2 and len(cells2) >= 20:
        scored = sorted(((min(hamming(th, dhash_trim(crop)) for th in target_hashes),
                          i, cx, cy, crop)
                         for i, cx, cy, crop in cells2), key=lambda x: x[0])
    else:
        scored = min(pages, key=lambda pg: pg[0][0] if pg else 99)
'''

assert OLD in s, "翻页段没匹配上"
s = s.replace(OLD, NEW, 1)

# ---------- 2. 发送前校验指纹 ----------
OLD2 = '''def send_by_map(db, wxid: str, name: str, md5: str) -> bool:'''
idx = s.find(OLD2)
assert idx > 0
# 在函数体开头插入指纹校验：找到函数的第一行 docstring 之后
head_end = s.find('"""', idx)
head_end = s.find('"""', head_end + 3) + 3
CHECK = '''

    # 坐标表必须和"当前收藏集合"匹配，否则行列会错、还会点错格子
    try:
        if map_is_stale(db):
            b.log("坐标表已过期（收藏变了）：不用缓存坐标，改走面板匹配")
            return False
    except Exception:
        pass
'''
s = s[:head_end] + CHECK + s[head_end:]

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: 翻页修复 + 指纹校验")

# ---------- 3. map_by_send 写指纹 ----------
M = r"D:\dsh\projects\ai-chat-bot\map_by_send.py"
with io.open(M, "r", encoding="utf-8") as f:
    m = f.read()

if "_fingerprint" not in m:
    OLD3 = '''    sm.save_map(mp)'''
    if OLD3 in m:
        NEW3 = '''    try:
        import sticker_match as _sm2
        mp["_fingerprint"] = _sm2.map_fingerprint(db)
    except Exception:
        pass
    sm.save_map(mp)'''
        m = m.replace(OLD3, NEW3, 1)
        with io.open(M, "w", encoding="utf-8", newline="\n") as f:
            f.write(m)
        print("map_by_send: 已写入指纹")
    else:
        print("⚠ map_by_send 里没找到 save_map 调用")
