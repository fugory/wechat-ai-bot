# -*- coding: utf-8 -*-
"""表情发送（A 方案）：面板缩略图匹配。

流程：
  开面板 → 截图 → 切 25 格 → 每格算 dHash → 和本地图哈希比对
  → 点最像的那格 → 用数据库回读的 md5 验证 → 不对就换下一个候选

为什么这么做：面板顺序会随使用漂移、只显示 25 格，靠序号必错；
但"图像长什么样"是稳定的，dHash 对缩放/压缩也够鲁棒。

自校验：发出去的「动画表情」消息里带 md5，能直接和期望值比对。
"""
import json
import os
import sys
import time

sys.path.insert(0, r"D:\dsh\projects\ai-chat-bot")
sys.path.insert(0, r"D:\dsh\projects\ai-chat-bot\gui")

import numpy as np  # noqa: E402
from PIL import Image, ImageGrab  # noqa: E402

import bridge as b  # noqa: E402
from wxbg import PANEL_TAB_STICKER as _TAB  # noqa: E402

IMG_DIR = r"D:\dsh\projects\ai-chat-bot\imgs\stickers"
MD5_RE = None


def dhash(img, size=8):
    """差值哈希：抗缩放、抗轻微压缩。"""
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    a = np.asarray(g, dtype=np.int16)
    diff = a[:, 1:] > a[:, :-1]
    bits = 0
    for v in diff.flatten():
        bits = (bits << 1) | int(v)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


_LOCAL_CACHE = {}


def trim_content(img, bg_tol: int = 18):
    """裁掉四周的背景留白，只保留内容区域（缩略图常见 letterbox）。"""
    try:
        rgb = img.convert("RGB")
        w, h = rgb.size
        if w < 8 or h < 8:
            return rgb
        a = np.asarray(rgb).astype(np.int16)
        # 取四角像素的中位数当背景色
        corners = np.array([a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]])
        bg = np.median(corners, axis=0)
        diff = np.abs(a - bg).sum(axis=2)
        mask = diff > bg_tol * 3
        if not mask.any():
            return rgb
        ys, xs = np.where(mask)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        if x1 - x0 < 8 or y1 - y0 < 8:
            return rgb
        return rgb.crop((x0, y0, x1, y1))
    except Exception:
        return img


def dhash_trim(img, size=8):
    """先裁留白再算 dHash（对 letterbox 更鲁棒）。"""
    return dhash(trim_content(img), size)


def local_hashes(md5: str, max_frames: int = 6):
    """本地素材的多个 dHash（动图取多帧）。返回 list（空=没有素材）。"""
    md5 = (md5 or "").lower()
    if md5 in _LOCAL_CACHE:
        return _LOCAL_CACHE[md5]
    out = []
    for ext in (".gif", ".jpg", ".png", ".jpeg", ".webp"):
        p = os.path.join(IMG_DIR, md5 + ext)
        if not os.path.isfile(p):
            continue
        try:
            img = Image.open(p)
            n = getattr(img, "n_frames", 1) or 1
            if n <= 1:
                out = [dhash_trim(img.convert("RGB"))]
            else:
                step = max(1, n // max_frames)
                for i in range(0, n, step):
                    try:
                        img.seek(i)
                        out.append(dhash_trim(img.convert("RGB")))
                    except Exception:
                        continue
                    if len(out) >= max_frames:
                        break
            break
        except Exception:
            return []
    _LOCAL_CACHE[md5] = out
    return out


def local_hash(md5: str):
    """兼容旧调用：返回第一帧哈希。"""
    hs = local_hashes(md5)
    return hs[0] if hs else None


def _panel_window(bg):
    hw = bg.find_emoji_panel()
    if hw:
        return hw
    bg.open_emoji_panel()
    time.sleep(1.2)
    return bg.find_emoji_panel()


def _grab(hw):
    """优先用 PrintWindow 抓窗口自身内容（不受遮挡影响），失败再退回屏幕截图。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    gdi = ctypes.windll.gdi32
    # 64 位下句柄是指针，必须声明签名，否则 OverflowError
    u.GetWindowDC.restype = ctypes.c_void_p
    u.GetWindowDC.argtypes = [ctypes.c_void_p]
    u.PrintWindow.restype = ctypes.c_int
    u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    u.ReleaseDC.restype = ctypes.c_int
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi.SelectObject.restype = ctypes.c_void_p
    gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.DeleteObject.restype = ctypes.c_int
    gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi.DeleteDC.restype = ctypes.c_int
    gdi.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi.GetDIBits.restype = ctypes.c_int
    gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_uint]
    r = wt.RECT()
    u.GetWindowRect(hw, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        # 面板没打开 / 句柄失效：返回一张空图，由调用方按"没格子"处理
        b.log("抓图失败：窗口尺寸为 %dx%d（面板可能没打开）" % (w, h))
        return Image.new("RGB", (1, 1))
    hdc = u.GetWindowDC(hw)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    PW_RENDERFULLCONTENT = 2
    ok = u.PrintWindow(hw, mem, PW_RENDERFULLCONTENT)
    try:
        if not ok:
            raise RuntimeError('PrintWindow 失败')
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                        ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                        ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                        ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                        ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                        ('biClrImportant', ctypes.c_uint32)]
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1)
        return img.convert('RGB')
    finally:
        gdi.DeleteObject(bmp); gdi.DeleteDC(mem); u.ReleaseDC(hw, hdc)


def panel_scroll(hw, up: bool = True, times: int = 1) -> None:
    """滚动表情面板。WM_MOUSEWHEEL 的 lParam 是**屏幕坐标**。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    r = wt.RECT()
    u.GetWindowRect(hw, ctypes.byref(r))
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    delta = 120 if up else -120
    wparam = (delta << 16) & 0xFFFFFFFF
    lparam = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_void_p, ctypes.c_void_p]
    for _ in range(times):
        u.PostMessageW(hw, 0x020A, wparam, lparam)
        import time as _t
        _t.sleep(0.35)


def panel_scroll_top(hw) -> None:
    """尽力滚到顶部（多滚几次，避免惯性）。"""
    panel_scroll(hw, up=True, times=8)


def park_cursor(hwnd=None):
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


def panel_cells(bg, hw):
    """返回 [(index, cx, cy, img_crop)]，index 按行优先。

    逐行找列：不同表情宽高比不同，用全局列坐标会串行偏移。
    """
    _cur = park_cursor(hw)
    try:
        img = _grab(hw)
    finally:
        restore_cursor(_cur)
    a = np.asarray(img).astype(np.int16)
    chroma = a.max(axis=2) - a.min(axis=2)
    bright = a.mean(axis=2)
    mask = (chroma > 18) | (bright > 120)
    row_prof = mask.mean(axis=1)

    def runs(prof, th=0.18, min_len=30):
        out, start = [], None
        for i, v in enumerate(prof):
            if v >= th and start is None:
                start = i
            elif v < th and start is not None:
                if i - start >= min_len:
                    out.append((start, i))
                start = None
        if start is not None and len(prof) - start >= min_len:
            out.append((start, len(prof)))
        return out

    rows = runs(row_prof)
    cells = []
    idx = 0
    for (y0, y1) in rows:
        band = mask[y0:y1, :]
        col_prof = band.mean(axis=0)
        cols = runs(col_prof, th=0.15, min_len=30)
        if not cols:
            continue
        for (x0, x1) in cols:
            if x1 - x0 < 30 or y1 - y0 < 20:
                continue
            crop = img.crop((x0, y0, x1, y1))
            cells.append((idx, (x0 + x1) // 2, (y0 + y1) // 2, crop))
            idx += 1
    return cells, (rows, rows)


MARK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "sticker_map.json")
COLS = [101, 211, 321, 431, 541]
ROWS = [100, 210, 319, 430, 523]


def load_map() -> dict:
    try:
        with open(MARK_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_map(m: dict) -> None:
    try:
        tmp = MARK_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MARK_PATH)
    except Exception:
        pass


def cell_xy(row: int, col: int):
    try:
        return COLS[int(col)], ROWS[int(row)]
    except Exception:
        return None


def send_by_map(db, wxid: str, name: str, md5: str) -> bool:
    """按缓存坐标发一个表情。返回 True=已发出且校验一致。"""

    # 坐标表必须和"当前收藏集合"匹配，否则行列会错、还会点错格子
    try:
        if map_is_stale(db):
            b.log("坐标表已过期（收藏变了）：不用缓存坐标，改走面板匹配")
            return False
    except Exception:
        pass

    import re
    want = (md5 or "").lower()
    m = load_map().get(want)
    if not m:
        return False
    xy = cell_xy(m.get("row", -1), m.get("col", -1))
    if not xy:
        return False
    if not b.uia_open_chat(name, wxid=wxid):
        return False
    bg = b.get_bg()
    bg.refresh_window()
    bg.activate()
    time.sleep(0.3)
    hw = bg.find_emoji_panel()
    if not hw:
        hw = _panel_window(bg)
    if not hw:
        return False
    bg._click_panel_at(hw, _TAB[0], _TAB[1])
    time.sleep(0.9)
    panel_scroll_top(hw)
    time.sleep(0.5)

    # ④ 点之前先确认这一格就是目标（防止增删导致整体位移后发错图）
    try:
        target_hashes = local_hashes(want)
        if target_hashes:
            img = _grab(hw)
            half = 45
            box = (max(0, xy[0] - half), max(0, xy[1] - half),
                   min(img.size[0], xy[0] + half), min(img.size[1], xy[1] + half))
            cell = img.crop(box)
            dist = min(hamming(th, dhash_trim(cell)) for th in target_hashes)
            if dist > 12:
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
                            mm2 = re.search(r'md5\s*=\s*"?([0-9a-fA-F]{32})',
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
                    return False
    except Exception as e:
        b.log("发送前校验异常（继续按坐标发）：%s" % e)

    bg._click_panel_at(hw, xy[0], xy[1])
    time.sleep(0.9)
    try:
        rows = db.get_messages(wxid, limit=2) or []
        got = ""
        if rows:
            mm = re.search(r'md5="?([0-9a-fA-F]{32})', str(rows[0].get("content") or ""))
            got = (mm.group(1) or "").lower() if mm else ""
        if got == want:
            # 成功后**不关面板**：下次直接复用，减少一次开/关
            try:
                b.PANEL_OPEN = True
            except Exception:
                pass
            b.log("表情发送（缓存坐标）：%s 第%s行%s列（面板保留）"
                  % (want[:8], m.get("row"), m.get("col")))
            return True
        try:
            bg.close_emoji_panel()
        except Exception:
            pass
        b.log("缓存坐标失效：期望 %s 实发 %s" % (want[:8], got[:8] or "非表情"))
        mm2 = load_map()
        mm2.pop(want, None)
        save_map(mm2)
        return False
    except Exception as e:
        b.log("按缓存发送异常：%s" % e)
        return False


def calibrate_stickers(db, threshold: int = 12):
    """返回 (matched, unmatched)：能匹配的 [(md5,dist)]，不能的 [(md5,dist)]。"""
    matched, unmatched = [], []
    try:
        order = b.sticker_order_md5s(db)
        if not order:
            return matched, unmatched
        # 先打开一个会话：没有会话就没有输入栏，也就找不到「发送表情」按钮
        try:
            if not b.uia_current_chat():
                b.uia_open_chat("文件传输助手", wxid="filehelper")
                time.sleep(1.0)
        except Exception:
            pass
        bg = b.get_bg()
        bg.refresh_window()
        bg.activate()
        time.sleep(0.5)
        if bg.find_emoji_panel():
            bg.close_emoji_panel()
            time.sleep(0.4)
        c = bg.uia_screen_center(lambda e: "发送表情" in (e.Name or ""))
        if not c:
            b.log("校准：UIA 不可用")
            return matched, unmatched
        bg.click_screen(*c)
        time.sleep(1.6)
        hw = _panel_window(bg)
        if not hw:
            b.log("校准：面板打不开")
            return matched, unmatched
        bg._click_panel_at(hw, _TAB[0], _TAB[1])
        time.sleep(0.9)

        # 收集"所有页"的格子，覆盖面更广
        # 只采集第一页：收藏上限就是 25（一页的量），翻页会把不同偏移的格子混进来
        all_hashes = []
        page_cells = []
        panel_scroll_top(hw)
        time.sleep(0.6)
        cells, _g = panel_cells(bg, hw)
        page_cells = cells
        all_hashes = [dhash_trim(crop) for _i, _x, _y, crop in cells]
        if not cells:
            b.log("校准失败：表情面板没打开或没画出格子（先手动点一次表情按钮再试）")
        panel_scroll_top(hw)
        b.log("校准：采集到 %d 个面板格子" % len(all_hashes))
        if not all_hashes:
            bg.close_emoji_panel()
            return matched, unmatched

        mapping = {}
        for md5 in order:
            hs = local_hashes(md5)
            if not hs:
                unmatched.append((md5, 99))
                continue
            best = (999, -1, -1)
            for _pi, (_i, cx, cy, crop) in enumerate(page_cells):
                d = min(hamming(th, dhash_trim(crop)) for th in hs)
                if d < best[0]:
                    best = (d, cx, cy)
            dist = best[0]
            if dist <= threshold:
                matched.append((md5, dist))
                # 反推行列（面板是固定网格）
                col = min(range(len(COLS)), key=lambda k: abs(COLS[k] - best[1]))
                row = min(range(len(ROWS)), key=lambda k: abs(ROWS[k] - best[2]))
                mapping[md5] = {"row": row, "col": col, "dist": dist}
            else:
                unmatched.append((md5, dist))
        save_map(mapping)
        b.log("表情坐标已缓存：%d 条 → sticker_map.json" % len(mapping))
        try:
            bg.close_emoji_panel()
        except Exception:
            pass
        b.log("校准完成：可发 %d 个，不可发 %d 个"
              % (len(matched), len(unmatched)))
    except Exception as e:
        b.log("校准异常：%s" % e)
    return matched, unmatched


def send_sticker_by_md5(db, wxid: str, name: str, md5: str,
                        max_try: int = 3) -> bool:
    """按 md5 发一个表情：缩略图匹配 + 发送后 md5 校验 + 失败换候选。"""
    global MD5_RE
    import re
    if MD5_RE is None:
        MD5_RE = re.compile(r'md5="?([0-9a-fA-F]{32})')

    want = (md5 or "").lower()
    target_hashes = local_hashes(want)
    if not target_hashes:
        b.log("发表情失败：本地没有 %s 的图" % want[:8])
        return False

    # ① 先试坐标缓存（不截图、不算哈希，面板操作最少）
    b.set_focus_mode(True)        # 下面要开面板/点格子
    try:
        if send_by_map(db, wxid, name, want):
            return True
    except Exception as _e:
        b.log("按缓存发送异常（转哈希匹配）：%s" % _e)
    finally:
        b.set_focus_mode(False)

    if not b.uia_open_chat(name, wxid=wxid):
        b.log("发表情失败：切会话失败")
        return False
    bg = b.get_bg()
    bg.refresh_window()
    bg.activate()
    time.sleep(0.3)

    # 面板已经开着就先关掉，避免"切换"导致误判
    if bg.find_emoji_panel():
        bg.close_emoji_panel()
        time.sleep(0.4)
    hw = _panel_window(bg)
    if not hw:
        b.log("发表情失败：面板打不开")
        return False

    # 必须先切到「收藏贴纸」tab，再截图：否则截到的是 emoji 面板
    try:
        bg._click_panel_at(hw, _TAB[0], _TAB[1])
        time.sleep(0.8)
    except Exception as e:
        b.log('切收藏tab失败：%s' % e)

    panel_scroll_top(hw)
    cells, _grid = panel_cells(bg, hw)
    if not cells:
        b.log("发表情失败：面板里没找到格子")
        return False

    scored = sorted(((min(hamming(th, dhash_trim(crop)) for th in target_hashes),
                      i, cx, cy, crop)
                     for i, cx, cy, crop in cells), key=lambda x: x[0])
    best = scored[0][0]
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

    for dist, idx, cx, cy, _crop in scored[:max_try]:
        # 点击必须发给**面板窗口**（主窗口收不到），坐标是面板内坐标
        bg._click_panel_at(hw, cx, cy)
        time.sleep(0.9)
        rows = db.get_messages(wxid, limit=2) or []
        got = ""
        if rows:
            m = MD5_RE.search(str(rows[0].get("content") or ""))
            got = (m.group(1) or "").lower() if m else ""
        if got == want:
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
            return True
        b.log("第 %d 格不是目标（发的是 %s），换下一个" % (idx, got[:8] or "非表情"))
    bg.close_emoji_panel()
    b.log("表情发送失败：%d 个候选都对不上" % max_try)
    return False


if __name__ == "__main__":
    import json
    b.load_dotenv(r"D:\dsh\projects\ai-chat-bot\.env")
    db = b._shared_db()
    items = json.load(open(r"D:\dsh\projects\ai-chat-bot\stickers.json", encoding="utf-8-sig"))
    ok = 0
    for it in items[:3]:
        r = send_sticker_by_md5(db, "filehelper", "文件传输助手", it["md5"])
        print("%-16s %s" % ((it.get("tag") or "")[:14], "OK" if r else "FAIL"))
        ok += r
    print("成功 %d/3" % ok)

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


