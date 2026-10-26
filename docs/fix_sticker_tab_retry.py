# -*- coding: utf-8 -*-
"""修表情发送：坐标表是对的，但"发送前校验"经常误判 → 回退到不稳定的全格匹配。

根因：打开面板后点"收藏贴纸" tab 有时没生效（面板还在动画/还在表情 tab），
      于是抓到的格子是表情 tab 的内容，与目标哈希差 30+ → 判定"缓存失效"
      → 回退到 panel_cells（切格本身在 24/25/26 之间跳）→ 放弃发送。

修法：
  1. 点 tab 后**重试一次**（间隔加长），再校验
  2. 校验失败时不直接回退，先"按坐标点一下 + 数据库回读"确认
     （数据库是权威的：点对了就是对的）
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''    bg._click_panel_at(hw, _TAB[0], _TAB[1])
    time.sleep(0.6)
    panel_scroll_top(hw)
    time.sleep(0.4)'''
NEW = '''    bg._click_panel_at(hw, _TAB[0], _TAB[1])
    time.sleep(0.9)
    panel_scroll_top(hw)
    time.sleep(0.5)'''
assert OLD in s, "tab 点击处没匹配上"
s = s.replace(OLD, NEW, 1)

# 校验失败时的处理：先按坐标点一次并回读，不要立刻回退
OLD2 = '''            if dist > 12:
                b.log("坐标缓存已失效：%s 目标格距离 %d，改走全格匹配" % (want[:8], dist))
                mm = load_map()
                mm.pop(want, None)
                save_map(mm)
                try:
                    bg.close_emoji_panel()
                except Exception:
                    pass
                return False'''
NEW2 = '''            if dist > 12:
                b.log("目标格不像：%s 距离 %d —— 可能是 tab 没切过去，重点一次 tab 再校验"
                      % (want[:8], dist))
                bg._click_panel_at(hw, _TAB[0], _TAB[1])
                time.sleep(1.2)
                panel_scroll_top(hw)
                time.sleep(0.6)
                try:
                    img2 = _grab(hw)
                    cell2 = img2.crop(box)
                    dist = min(hamming(th, dhash_trim(cell2)) for th in target_hashes)
                    b.log("重试后距离：%d" % dist)
                except Exception:
                    dist = 99
                if dist > 20:
                    # 仍然不像：按坐标点一次，用数据库回读做最终判断
                    b.log("仍然不像（%d）：按坐标点一次，用数据库回读判定" % dist)
                    bg._click_panel_at(hw, xy[0], xy[1])
                    time.sleep(1.0)
                    try:
                        rows2 = db.get_messages(wxid, limit=2) or []
                        got2 = ""
                        if rows2:
                            mm2 = re.search(r'md5\\s*=\\s*"?([0-9a-fA-F]{32})',
                                            str(rows2[0].get("content") or ""))
                            got2 = (mm2.group(1) or "").lower() if mm2 else ""
                        if got2 == want:
                            b.log("按坐标发送成功（数据库确认）：%s" % want[:8])
                            b.PANEL_OPEN = True
                            return True
                        b.log("按坐标发送对不上：期望 %s 实发 %s"
                              % (want[:8], (got2[:8] or "非表情")))
                    except Exception as e:
                        b.log("回读失败：%s" % e)
                    mm = load_map()
                    mm.pop(want, None)
                    save_map(mm)
                    try:
                        bg.close_emoji_panel()
                    except Exception:
                        pass
                    return False'''
assert OLD2 in s, "校验失败分支没匹配上"
s = s.replace(OLD2, NEW2, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: tab 重试 + 数据库兜底判定")
