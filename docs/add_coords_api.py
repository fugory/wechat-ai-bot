# -*- coding: utf-8 -*-
"""坐标校准：用户把鼠标移到目标上，点一下按钮，3 秒后记录那个位置。

为什么不用"点一下微信"来记录：控制台是另一个窗口，用户去点微信时
控制台收不到那个点击。所以用「倒计时采样」：点按钮 → 3 秒内把鼠标移过去
停住 → 时间到，服务端读 GetCursorPos 并换算成"边缘锚点 + 偏移"存起来。
"""
import io

APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"

ROUTES = '''# ---------------- 坐标校准 ----------------

COORD_NAMES = {"search": "搜索框", "input": "输入框",
               "emoji": "表情按钮 😊", "send": "发送按钮"}


@app.get("/api/coords/state")
def api_coords_state():
    try:
        sys.path.insert(0, ROOT)
        import coords_send as cs
        h = cs.find_wechat_any()
        box = cs.client_box(h) if h else (0, 0, 0, 0)
        raw = {}
        try:
            with open(os.path.join(ROOT, "coords.json"), "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        specs = {}
        for k in COORD_NAMES:
            spec = raw.get(k) or cs.DEFAULT[k]
            specs[k] = spec
        return jsonify({"ok": True, "window": bool(h), "client": box,
                        "measured_client": raw.get("_measured_client"),
                        "specs": specs, "names": COORD_NAMES})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/coords/cursor")
def api_coords_cursor():
    """读当前鼠标位置（界面用来做实时预览）。"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return jsonify({"ok": True, "x": pt.x, "y": pt.y})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/coords/capture")
def api_coords_capture():
    """倒计时采样：delay 秒后读鼠标位置，换算成锚点偏移并保存。"""
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name") or ""
    delay = float(data.get("delay") or 3.0)
    if name not in COORD_NAMES:
        return jsonify({"ok": False, "error": "未知的坐标名"})
    try:
        import ctypes
        import ctypes.wintypes as wt
        sys.path.insert(0, ROOT)
        import coords_send as cs
        time.sleep(max(0.5, min(10.0, delay)))
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        h = cs.find_wechat_any()
        if not h:
            return jsonify({"ok": False, "error": "找不到微信窗口"})
        cx, cy, cw, ch = cs.client_box(h)
        if not (cx <= pt.x <= cx + cw and cy <= pt.y <= cy + ch):
            return jsonify({"ok": False,
                            "error": "鼠标不在微信窗口内（现在 %d,%d）" % (pt.x, pt.y)})
        # 优先用"离哪条边更近"决定锚点
        use_left = (pt.x - cx) <= (cx + cw - pt.x)
        spec = {"ax": "left" if use_left else "right",
                "dx": int(pt.x - cx) if use_left else int(pt.x - (cx + cw)),
                "ay": "top" if (pt.y - cy) <= (cy + ch - pt.y) else "bottom",
                "dy": int(pt.y - cy) if (pt.y - cy) <= (cy + ch - pt.y)
                      else int(pt.y - (cy + ch))}
        path = os.path.join(ROOT, "coords.json")
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
        cur[name] = spec
        cur["_measured_client"] = [cw, ch]
        cur["_measured_at"] = time.strftime("%F %T")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return jsonify({"ok": True, "name": name, "spec": spec,
                        "client": [cw, ch],
                        "msg": "%s 已记录：锚点 %s%s，偏移 (%d,%d)"
                               % (COORD_NAMES[name], spec["ax"], "/" + spec["ay"],
                                  spec["dx"], spec["dy"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/coords/reset")
def api_coords_reset():
    try:
        p = os.path.join(ROOT, "coords.json")
        if os.path.isfile(p):
            os.remove(p)
        return jsonify({"ok": True, "msg": "已恢复默认坐标"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


'''

anchor = '@app.post("/api/uia/diagnose")'
assert anchor in s if False else True
with io.open(APP, "r", encoding="utf-8") as f:
    s = f.read()
assert anchor in s, "anchor miss"
s = s.replace(anchor, ROUTES + anchor, 1)
with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("app.py: 坐标校准接口已加")
