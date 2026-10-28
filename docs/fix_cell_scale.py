# -*- coding: utf-8 -*-
"""面板尺寸不再是 643：把写死的格子坐标改成按实际尺寸缩放。

实测：面板现在是 771×771（以前 643×643）。
COLS = [101,211,321,431,541] / ROWS = [100,210,319,430,523] 是 643 时代的像素，
直接用在 771 面板上，每一格都偏 20%，点到的就是隔壁格/空白。

改法：
  cell_xy(row, col, panel=None)  —— 传了 panel 就按它实际尺寸缩放；
                                    不传则沿用旧常量（兼容老调用）
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''def cell_xy(row: int, col: int):
    try:
        return COLS[int(col)], ROWS[int(row)]
    except Exception:
        return None'''

NEW = '''PANEL_REF_SIZE = 643.0     # COLS/ROWS 是按这个尺寸量的


def cell_xy(row: int, col: int, panel: int = 0):
    """格子中心坐标。传 panel 时按面板实际尺寸等比缩放（面板已不是 643）。"""
    try:
        cx, cy = COLS[int(col)], ROWS[int(row)]
    except Exception:
        return None
    if panel:
        try:
            import ctypes
            import ctypes.wintypes as wt
            r = wt.RECT()
            ctypes.windll.user32.GetClientRect(panel, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 100 and h > 100:
                return int(cx * w / PANEL_REF_SIZE), int(cy * h / PANEL_REF_SIZE)
        except Exception:
            pass
    return cx, cy'''

assert OLD in s, "cell_xy 没匹配上"
s = s.replace(OLD, NEW, 1)

# send_by_map 里调用处传 panel
OLD2 = 'xy = cell_xy(m.get("row", -1), m.get("col", -1))'
if OLD2 in s:
    # 这时还没有 panel 句柄，改成"拿到面板后再算"
    s = s.replace(OLD2, 'row_, col_ = m.get("row", -1), m.get("col", -1)', 1)
    OLD3 = '''    bg._click_panel_at(hw, _tp[0], _tp[1])
    time.sleep(0.9)'''
    NEW3 = '''    bg._click_panel_at(hw, _tp[0], _tp[1])
    time.sleep(0.9)
    xy = cell_xy(row_, col_, hw)     # 按面板实际尺寸算格子坐标'''
    assert OLD3 in s, "tab 后锚点没匹配上"
    s = s.replace(OLD3, NEW3, 1)
    print("send_by_map: 格子坐标改为按面板缩放")

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("cell_xy: 支持按面板尺寸缩放")
