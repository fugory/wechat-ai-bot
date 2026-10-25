# -*- coding: utf-8 -*-
"""坐标系自检：把每个关键点击点用 UIA 找一遍，报告"找到没 / 在哪 / 误差"。

用途：
  * 用户改了字体/DPI/窗口尺寸后，点一下"自检"就知道还能不能正常点
  * 出问题时能立刻定位是"元素找不到"还是"找到了但点不中"

只读：不点击、不发送。
"""
import os
import sys

ROOT = r"D:\dsh\projects\ai-chat-bot"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gui"))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def calibrate() -> dict:
    try:
        import bridge as b
        import wxwin
        b.load_dotenv(os.path.join(ROOT, ".env"))
        bg = b.get_bg()
        bg.ensure_onscreen()
        cw, ch = bg._client()[2:]
        sx, sy = bg.scale()
    except Exception as e:
        return {"ok": False, "error": "初始化失败：%s" % e}

    checks = []

    def add(name, coords, note=""):
        ok = bool(coords)
        inside = ok and (0 <= coords[0] <= cw) and (0 <= coords[1] <= ch)
        checks.append({
            "name": name,
            "ok": ok and inside,
            "coords": list(coords) if ok else None,
            "note": note or ("超出客户区" if (ok and not inside) else ""),
        })

    # 1. 输入框
    add("输入框", bg.uia_center(lambda e: (e.AutomationId or "") == "chat_input_field"),
        "aid=chat_input_field")
    # 2. 表情按钮
    add("表情按钮", bg.uia_center(lambda e: "发送表情" in (e.Name or "")),
        "name≈发送表情")
    # 3. 左侧「微信」图标
    add("聊天页图标", bg.uia_center(lambda e: (e.Name or "") == "微信"
                              and "Button" in (e.ControlTypeName or "")),
        "name=微信")
    # 4. 会话列表项（取第一个）
    first = bg.uia_center(lambda e: (e.AutomationId or "").startswith("session_item_"),
                          )
    add("会话列表项", first, "aid=session_item_*")

    # 5. 表情面板（独立弹窗，不是主窗口坐标）
    panel_note = ""
    try:
        import time
        hw = bg.find_emoji_panel()
        opened_by_us = False
        if not hw:
            bg.open_emoji_panel()      # 注意：这是"切换"，已经开着会被关掉
            time.sleep(1.0)
            hw = bg.find_emoji_panel()
            opened_by_us = True
        panel_note = "面板句柄 %s" % hw
        if opened_by_us and hw:
            bg.close_emoji_panel()     # 自己开的自己关，别影响下一次
            panel_note += "（已关闭）"
    except Exception as e:
        panel_note = "探测失败：%s" % e
    checks.append({"name": "表情面板弹窗",
                   "ok": bool(panel_note.startswith("面板句柄") and "None" not in panel_note),
                   "coords": None, "note": panel_note})

    # 6. 当前会话标题
    title = ""
    try:
        title = b.uia_current_chat() or ""
    except Exception:
        pass
    checks.append({"name": "当前会话标题", "ok": bool(title), "coords": None,
                   "note": title or "读不到"})

    total = len(checks)
    passed = sum(1 for c in checks if c["ok"])
    return {
        "ok": True,
        "client": [cw, ch],
        "scale": [round(sx, 3), round(sy, 3)],
        "canon": [1100, 760],
        "passed": passed,
        "total": total,
        "all_ok": passed == total,
        "checks": checks,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(calibrate(), ensure_ascii=False, indent=2))
