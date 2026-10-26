# -*- coding: utf-8 -*-
"""图标定位（模板匹配）：不靠绝对坐标，直接从截图里找控件。

用途：点 😊、点发送 —— 这两个按钮小、位置随窗口动，固定坐标容易点空。
做法：把一次截下来的图标存成模板，之后在当前截图里多尺度匹配，
      找到就点它中心；找不到就明确失败（绝不乱点）。
"""
import os

import numpy as np
from PIL import Image, ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, "imgs", "ui")


def _gray(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("L")).astype(np.float32)
    return a


def _norm(a: np.ndarray) -> np.ndarray:
    a = a - a.mean()
    n = np.sqrt((a * a).sum()) + 1e-6
    return a / n


def _match_at_scale(hay: np.ndarray, tpl: np.ndarray, down: int):
    """在降采样图上做归一化互相关，返回 (score, cx, cy)，坐标是降采样坐标。"""
    H, W = hay.shape
    th, tw = tpl.shape
    if th >= H or tw >= W:
        return -1.0, 0, 0
    # 用 stride 视图做快速滑动窗口
    from numpy.lib.stride_tricks import sliding_window_view
    win = sliding_window_view(hay, (th, tw))          # (H-th+1, W-tw+1, th, tw)
    win = win.reshape(win.shape[0], win.shape[1], -1)
    win = win - win.mean(axis=2, keepdims=True)
    denom = np.sqrt((win * win).sum(axis=2)) + 1e-6
    t = tpl.reshape(-1)
    t = t - t.mean()
    t = t / (np.sqrt((t * t).sum()) + 1e-6)
    score = (win @ t) / denom
    idx = int(np.argmax(score))
    y, x = divmod(idx, score.shape[1])
    return float(score[y, x]), x, y


def locate(hwnd, name: str, region: tuple = None, min_score: float = 0.72,
           down: int = 3, scales=(0.85, 0.92, 1.0, 1.08, 1.15)):
    """在窗口截图里找图标，返回屏幕坐标 (x, y) 或 None。

    region: (left, top, right, bottom) 相对窗口的位置，限定搜索区域以提速
    """
    path = os.path.join(TPL_DIR, "%s.png" % name)
    if not os.path.isfile(path):
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        box = (r.left, r.top, r.right, r.bottom)
        shot = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    except Exception:
        return None

    ox, oy = 0, 0
    if region:
        left, top, right, bottom = region
        left = max(0, left)
        top = max(0, top)
        right = min(shot.width, right)
        bottom = min(shot.height, bottom)
        if right - left < 20 or bottom - top < 20:
            return None
        shot = shot.crop((left, top, right, bottom))
        ox, oy = left, top

    hay_full = _gray(shot)
    best = (-1.0, 0, 0, 1.0)
    tpl_img = Image.open(path).convert("L")
    for sc in scales:
        tw = max(8, int(tpl_img.width * sc))
        th = max(8, int(tpl_img.height * sc))
        tpl = _gray(tpl_img.resize((tw, th), Image.BILINEAR))
        # 粗查：降采样
        hay_small = _gray(Image.fromarray(hay_full.astype(np.uint8)).resize(
            (max(1, hay_full.shape[1] // down), max(1, hay_full.shape[0] // down)),
            Image.BILINEAR))
        tpl_small = _gray(Image.fromarray(tpl.astype(np.uint8)).resize(
            (max(4, tw // down), max(4, th // down)), Image.BILINEAR))
        sc_score, sx, sy = _match_at_scale(_norm(hay_small), _norm(tpl_small), down)
        if sc_score > best[0]:
            best = (sc_score, int(sx * down + tw / 2), int(sy * down + th / 2), sc)

    score, cx, cy, sc = best
    if score < min_score:
        return None
    # 精修：在粗定位附近 ±12px 再匹配一次（全分辨率、同尺度）
    tw = max(8, int(tpl_img.width * sc))
    th = max(8, int(tpl_img.height * sc))
    tpl = _norm(_gray(tpl_img.resize((tw, th), Image.BILINEAR)))
    y0, y1 = max(0, cy - 16), min(hay_full.shape[0], cy + 16)
    x0, x1 = max(0, cx - 16), min(hay_full.shape[1], cx + 16)
    patch = hay_full[y0:y1 + th, x0:x1 + tw]
    if patch.shape[0] > th and patch.shape[1] > tw:
        s2, dx, dy = _match_at_scale(_norm(patch), tpl, 1)
        if s2 > score:
            score = s2
            cx, cy = x0 + dx + tw // 2, y0 + dy + th // 2
    # 换算回屏幕坐标
    r = None
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        rr = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rr))
        r = rr
    except Exception:
        return None
    return r.left + ox + cx, r.top + oy + cy


def score_only(hwnd, name: str, region=None):
    """调试用：返回 (score, x, y) 或 None。"""
    p = locate(hwnd, name, region)
    return p
