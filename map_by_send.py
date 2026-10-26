# -*- coding: utf-8 -*-
"""用「点一遍 + 回读数据库」建立**精确**的表情坐标映射。

为什么这么做：
  缩略图哈希在真实环境里只能认出约一半（留白、取帧、缩放都会影响），
  但"点一下 → 数据库里出现哪个 md5"是**确定的事实**。

做法：
  打开「文件传输助手」→ 开面板 → 滚到顶 → 逐个点 25 格
  每点一次，等 1 秒，读数据库最新消息的 md5，记下 格位 → md5
  最后写成 sticker_map.json（md5 → row/col）

代价：往文件传输助手发 25 个表情（无害），耗时约 1~2 分钟。
"""
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "gui"))

import bridge as b  # noqa: E402
import sticker_match as sm  # noqa: E402
from wxbg import PANEL_TAB_STICKER  # noqa: E402

MD5_RE = re.compile(r'md5="?([0-9a-fA-F]{32})')


def main() -> int:
    b.load_dotenv(os.path.join(HERE, ".env"))
    db = b._shared_db()
    bg = b.get_bg()
    bg.refresh_window()
    bg.activate()
    time.sleep(0.5)

    if not b.uia_open_chat("文件传输助手", wxid="filehelper"):
        print("切会话失败")
        return 1
    time.sleep(0.8)

    if bg.find_emoji_panel():
        bg.close_emoji_panel()
        time.sleep(0.4)
    c = bg.uia_screen_center(lambda e: "发送表情" in (e.Name or ""))
    if not c:
        print("找不到表情按钮（UIA 不可用？）")
        return 1
    bg.click_screen(*c)
    time.sleep(1.6)
    hw = bg.find_emoji_panel()
    if not hw:
        print("面板没打开")
        return 1
    bg._click_panel_at(hw, *PANEL_TAB_STICKER)
    time.sleep(0.9)
    sm.panel_scroll_top(hw)
    time.sleep(0.6)

    mapping = {}
    seen = {}
    total = 25
    for idx in range(total):
        row, col = idx // 5, idx % 5
        xy = sm.cell_xy(row, col)
        if not xy:
            continue
        rows = db.get_messages("filehelper", limit=1) or []
        seq0 = int(rows[0]["sort_seq"]) if rows else 0

        if not bg.find_emoji_panel():
            c2 = bg.uia_screen_center(lambda e: "发送表情" in (e.Name or ""))
            if not c2:
                print("面板掉了，停止")
                break
            bg.click_screen(*c2)
            time.sleep(1.4)
            hw = bg.find_emoji_panel()
            if not hw:
                continue
            bg._click_panel_at(hw, *PANEL_TAB_STICKER)
            time.sleep(0.7)
            sm.panel_scroll_top(hw)
            time.sleep(0.4)

        bg._click_panel_at(hw, xy[0], xy[1])
        md5 = ""
        for _ in range(6):
            time.sleep(0.55)
            rows = db.get_messages("filehelper", limit=3) or []
            for r in rows:
                if int(r.get("sort_seq") or 0) > seq0:
                    m = MD5_RE.search(str(r.get("content") or ""))
                    if m:
                        md5 = m.group(1).lower()
                        break
            if md5:
                break
        if md5:
            if md5 in seen:
                print("  (%d,%d) 与 (%d,%d) 重复：%s" % (row, col, *seen[md5], md5[:8]))
            else:
                seen[md5] = (row, col)
                mapping[md5] = {"row": row, "col": col}
            print("  (%d,%d) → %s" % (row, col, md5[:8]))
        else:
            print("  (%d,%d) → 没读到 md5" % (row, col))

    sm.save_map(mapping)
    print("已写入 sticker_map.json：%d 条" % len(mapping))
    try:
        bg.close_emoji_panel()
    except Exception:
        pass
    print("MAP_RESULT " + json.dumps({"ok": True, "mapped": len(mapping)},
                                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    _guard_no_bot()
    sys.exit(main())


def _guard_no_bot():
    """bot 在跑时禁止做校准/连点，避免把微信点卡死。"""
    import glob
    import os
    if os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.lock")):
        raise SystemExit("检测到 bot 正在运行（bridge.lock 存在）："
                         "请先停止 bot 再校准，避免高频点击把微信点卡死。")
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "bridge.py" in cl and p.info.get("pid") != os.getpid():
                raise SystemExit("检测到 bot 进程（pid=%s）：请先停止 bot 再校准。" % p.info.get("pid"))
    except SystemExit:
        raise
    except Exception:
        pass


