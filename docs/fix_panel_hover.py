# -*- coding: utf-8 -*-
"""修表情发送失败：鼠标悬停在面板上会破坏切格与哈希匹配。

实测（2026-10-26 18:14）：
    鼠标停在面板中央 → panel_cells 只切出 24 格
    鼠标移到屏幕角落 → 切出 25 格
原因：微信给悬停的那格加了高亮/描边，亮度掩码把相邻两格粘在一起，
      同时那一格的缩略图哈希也变了（匹配不上 → "面板里没有这张"）。

修法：
  1. 抓图前把鼠标挪开（只在指针确实落在面板/微信窗口内时才挪，并记住原位）
  2. 抓完再挪回去，不打扰用户
  3. 坐标表加"指纹"：收藏集合变了就判定缓存失效，避免拿旧坐标乱点
"""
import io

SM = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"

HELPER = '''def park_cursor(hwnd=None):
    """把鼠标挪开，避免悬停高亮破坏切格/哈希。返回原位置（可直接传回 restore_cursor）。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    try:
        pt = wt.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        orig = (pt.x, pt.y)
        inside = False
        if hwnd:
            r = wt.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            if r.left <= pt.x <= r.right and r.top <= pt.y <= r.bottom:
                inside = True
        if inside:
            u.SetCursorPos(5, 5)
            time.sleep(0.35)
            b.log("抓图前挪开鼠标（避免悬停高亮）：(%d,%d) -> (5,5)" % (orig[0], orig[1]))
        return orig
    except Exception:
        return None


def restore_cursor(pos):
    if not pos:
        return
    try:
        import ctypes
        ctypes.windll.user32.SetCursorPos(int(pos[0]), int(pos[1]))
    except Exception:
        pass


'''

with io.open(SM, "r", encoding="utf-8") as f:
    s = f.read()

if "def park_cursor(" not in s:
    anchor = "def panel_cells("
    assert anchor in s, "panel_cells 没找到"
    s = s.replace(anchor, HELPER + anchor, 1)

# panel_cells 内部：抓图前挪鼠标，抓完还原
OLD = '''    img = _grab(hw)
    a = np.asarray(img).astype(np.int16)'''
NEW = '''    _cur = park_cursor(hw)
    try:
        img = _grab(hw)
    finally:
        restore_cursor(_cur)
    a = np.asarray(img).astype(np.int16)'''
assert OLD in s, "panel_cells 抓图处没匹配上"
s = s.replace(OLD, NEW, 1)

with io.open(SM, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: panel_cells 已加'挪开鼠标'")

# ---------- 坐标表指纹 ----------
with io.open(SM, "r", encoding="utf-8") as f:
    t = f.read()

FP = '''

def map_fingerprint(db) -> str:
    """收藏集合的指纹（md5 排序后拼接再取 md5），用来判断坐标表是否过期。"""
    try:
        import hashlib
        order = b.sticker_order_md5s(db)
        return hashlib.md5(("|".join(sorted(order))).encode()).hexdigest()[:16]
    except Exception:
        return ""


def map_is_stale(db) -> bool:
    m = load_map()
    if not m:
        return True
    want = map_fingerprint(db)
    got = m.get("_fingerprint")
    if not got:
        return False        # 老格式没有指纹，先不判失效
    return want != got


'''
if "def map_fingerprint(" not in t:
    t = t.rstrip("\n") + FP
    with io.open(SM, "w", encoding="utf-8", newline="\n") as f:
        f.write(t)
    print("sticker_match: 已加坐标表指纹")
