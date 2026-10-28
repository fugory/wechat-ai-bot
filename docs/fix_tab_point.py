# -*- coding: utf-8 -*-
"""修"切收藏 tab 点到表情格，直接把黄色 emoji 发出去"。

现象：该发贴纸，结果群里收到一个黄色表情（用户截图确认面板停在 emoji tab）。

根因：PANEL_TAB_STICKER = (201, 575) 是按 643×643 面板**写死**的像素。
      心形实际位置在面板高度的 91.4%（截图实测），575/643 = 89.4%，
      差 13px —— 正好落进最后一排 emoji 格。
      而点 emoji 格 = 立即发送，没有二次确认，所以直接发出去了。

修法：
  1. tab 位置改成**按面板实际尺寸的比例**算（x 31.0%、y 91.4%）
  2. 加硬保护：算出来的 y 如果不在底部 12% 的条带内，直接放弃（绝不点进内容区）
  3. 点击前打印算出来的坐标，便于日志核对
"""
import io

WX = r"D:\dsh\projects\ai-chat-bot\wxbg.py"
SM = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"

with io.open(WX, "r", encoding="utf-8") as f:
    w = f.read()

OLD = "PANEL_TAB_STICKER = (201, 575)   # 面板底部心形 tab（收藏贴纸）"
NEW = '''PANEL_TAB_STICKER = (201, 575)   # 旧值：643×643 面板下的近似像素（已废弃，保留兼容）
# 心形 tab 在面板内的相对位置（2026-10-28 用户截图实测）
PANEL_TAB_STICKER_FRAC = (0.310, 0.914)
PANEL_TOOLBAR_BAND = 0.86        # y 必须大于面板高度的这个比例，否则不许点'''
assert OLD in w, "常量没匹配上"
w = w.replace(OLD, NEW, 1)

# 在类里加一个"算 tab 坐标"的方法（插在 _click_panel_at 之前）
HELPER = '''    def panel_tab_point(self, panel: int):
        """算出「收藏贴纸」tab 在面板客户区里的坐标；不在底部条带就返回 None。"""
        import ctypes
        import ctypes.wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.GetClientRect(panel, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 200 or h < 200:
            self._log("面板尺寸异常：%dx%d" % (w, h))
            return None
        fx, fy = PANEL_TAB_STICKER_FRAC
        x, y = int(w * fx), int(h * fy)
        if y < h * PANEL_TOOLBAR_BAND:
            self._log("拒绝点 tab：算出来 y=%d 不在底部条带（h=%d）" % (y, h))
            return None
        self._log("收藏 tab 坐标：(%d,%d) / 面板 %dx%d" % (x, y, w, h))
        return x, y

'''
if "def panel_tab_point" not in w:
    anchor = "    def _click_panel_at(self, panel: int, x: int, y: int) -> None:"
    assert anchor in w
    w = w.replace(anchor, HELPER + anchor, 1)

with io.open(WX, "w", encoding="utf-8", newline="\n") as f:
    f.write(w)
print("wxbg: 已加 panel_tab_point（比例 + 条带保护）")

# ---------- sticker_match：4 处调用改用新方法 ----------
with io.open(SM, "r", encoding="utf-8") as f:
    s = f.read()

OLD_CALL = "bg._click_panel_at(hw, _TAB[0], _TAB[1])"
NEW_CALL = '''_tp = bg.panel_tab_point(hw)
        if not _tp:
            bg.close_emoji_panel()
            b.log("发表情失败：定位不到收藏 tab（拒绝乱点，避免发出表情）")
            return False
        bg._click_panel_at(hw, _tp[0], _tp[1])'''

n = s.count(OLD_CALL)
s = s.replace(OLD_CALL, NEW_CALL)
print("sticker_match: 替换了 %d 处 tab 点击" % n)

with io.open(SM, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
