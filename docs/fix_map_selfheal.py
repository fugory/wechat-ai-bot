# -*- coding: utf-8 -*-
"""坐标表自愈补全：匹配成功时把位置记进 map。

为什么不重建整表：25 格连点是今天两次卡死的嫌疑源。
改成"用到哪个补哪个"——第一次靠哈希找到，之后就有缓存坐标了。

注意：只有"第一页"匹配成功才记录（翻页后的行列号和首页不一致）。
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''        if got == want:
            bg.close_emoji_panel()
            b.log("表情发送确认：md5=%s（第 %d 格，距离 %d）" % (got[:8], idx, dist))
            return True'''

NEW = '''        if got == want:
            bg.close_emoji_panel()
            b.log("表情发送确认：md5=%s（第 %d 格，距离 %d）" % (got[:8], idx, dist))
            # 自愈：第一页命中的话，把位置记进坐标表，下次就不用再匹配
            try:
                from_first_page = bool(pages and scored is pages[0])
                if from_first_page and 0 <= idx < 25:
                    mp = load_map()
                    mp[want] = {"row": idx // 5, "col": idx % 5}
                    try:
                        mp["_fingerprint"] = map_fingerprint(db)
                    except Exception:
                        pass
                    save_map(mp)
                    b.log("坐标表已补全：%s → 第%d行第%d列"
                          % (want[:8], idx // 5, idx % 5))
            except Exception as _e:
                b.log("坐标表补全失败（忽略）：%s" % _e)
            return True'''

assert OLD in s, "确认分支没匹配上"
s = s.replace(OLD, NEW, 1)

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: 已加坐标自愈补全")
