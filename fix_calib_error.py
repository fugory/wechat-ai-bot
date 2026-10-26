# -*- coding: utf-8 -*-
"""校准失败时给明确原因，而不是静默返回 0。

现象：点「校准表情」返回"可发 0 个"，没有原因。
根因：面板没打开时 _grab 抛 '窗口尺寸异常'，被外层 except 吞掉。
"""
import io

SM = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
CAL = r"D:\dsh\projects\ai-chat-bot\calib_stickers.py"
APP = r"D:\dsh\projects\ai-chat-bot\gui\app.py"

# ---------- 1. _grab 尺寸非法时返回空图，不再抛 ----------
with io.open(SM, "r", encoding="utf-8") as f:
    s = f.read()

OLD = '''    if w <= 0 or h <= 0:
        raise RuntimeError('窗口尺寸异常')'''
NEW = '''    if w <= 0 or h <= 0:
        # 面板没打开 / 句柄失效：返回一张空图，由调用方按"没格子"处理
        b.log("抓图失败：窗口尺寸为 %dx%d（面板可能没打开）" % (w, h))
        return Image.new("RGB", (1, 1))'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

# ---------- 2. 校准里 cells 为空时明确记录 ----------
OLD2 = '''        cells, _g = panel_cells(bg, hw)
        page_cells = cells
        all_hashes = [dhash_trim(crop) for _i, _x, _y, crop in cells]'''
NEW2 = '''        cells, _g = panel_cells(bg, hw)
        page_cells = cells
        all_hashes = [dhash_trim(crop) for _i, _x, _y, crop in cells]
        if not cells:
            b.log("校准失败：表情面板没打开或没画出格子（先手动点一次表情按钮再试）")'''
assert OLD2 in s
s = s.replace(OLD2, NEW2, 1)

with io.open(SM, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("sticker_match: 抓图失败不再抛异常")

# ---------- 3. CLI 把"为什么失败"写进结果 ----------
with io.open(CAL, "r", encoding="utf-8") as f:
    c = f.read()

OLD3 = '''        matched, unmatched = sm.calibrate_stickers(db)
        cfg = b.load_config()'''
NEW3 = '''        matched, unmatched = sm.calibrate_stickers(db)
        if not matched and not unmatched:
            out = {"ok": False,
                   "error": "校准没有结果：表情面板没打开（先手动点一次表情按钮，再点校准）"}
            print("CALIB_RESULT " + json.dumps(out, ensure_ascii=False))
            return 0
        cfg = b.load_config()'''
assert OLD3 in c
c = c.replace(OLD3, NEW3, 1)

with io.open(CAL, "w", encoding="utf-8", newline="\n") as f:
    f.write(c)
print("calib_stickers: 失败原因已写明")

# ---------- 4. 接口把它传给前端 ----------
with io.open(APP, "r", encoding="utf-8") as f:
    a = f.read()

OLD4 = '''        if not line:
            return jsonify({"ok": False,
                            "error": "校准进程没有输出结果：" + (p.stderr or "")[-200:]})'''
NEW4 = '''        if not line:
            tail = ((p.stderr or "") + (p.stdout or ""))[-300:]
            return jsonify({"ok": False, "error": "校准进程没有输出结果：" + tail})'''
assert OLD4 in a
a = a.replace(OLD4, NEW4, 1)

with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
    f.write(a)
print("app.py: 校准错误信息更完整")
