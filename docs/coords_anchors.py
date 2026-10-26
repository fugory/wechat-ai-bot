# -*- coding: utf-8 -*-
"""坐标改用"窗口边缘锚点 + 绝对偏移"，不再用比例。

原因：输入区左边是固定的会话列表宽度（约 285px），不是按窗口百分比缩放；
      窗口一变，比例坐标就点空（实测 1373x948 下点 😊 完全没反应）。

锚点定义（以 1391x957 窗口实测）：
  search  left +205 , top    +70
  input   left +700 , bottom -127
  emoji   left +322 , bottom -51
  send    right -71 , bottom -51
"""
import io
import json

P = r"D:\dsh\projects\ai-chat-bot\coords_send.py"

NEW_DEFAULT = '''DEFAULT = {
    "search": {"ax": "left",  "dx": 205, "ay": "top",    "dy": 70},
    "input":  {"ax": "left",  "dx": 700, "ay": "bottom", "dy": -127},
    "emoji":  {"ax": "left",  "dx": 322, "ay": "bottom", "dy": -51},
    "send":   {"ax": "right", "dx": -71, "ay": "bottom", "dy": -51},
    "_note": "以窗口边缘为锚点的绝对偏移；会话列表宽度固定，不能用比例",
}
'''

NEW_POINT = '''def point_of(hwnd, key: str):
    """按"边缘锚点 + 绝对偏移"算屏幕坐标。"""
    d = load()
    spec = d.get(key) or DEFAULT[key]
    if isinstance(spec, (list, tuple)):      # 兼容旧格式（比例），直接换算
        fx, fy = spec[0], spec[1]
        x, y, w, h = client_box(hwnd)
        return int(x + fx * w), int(y + fy * h)
    x, y, w, h = client_box(hwnd)
    px = (x + spec["dx"]) if spec["ax"] == "left" else (x + w + spec["dx"])
    py = (y + spec["dy"]) if spec["ay"] == "top" else (y + h + spec["dy"])
    return int(px), int(py)
'''

with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) 替换 DEFAULT
import re
m = re.search(r"DEFAULT = \{.*?\n\}\n", s, re.S)
assert m, "DEFAULT 没找到"
s = s[:m.start()] + NEW_DEFAULT + s[m.end():]

# 2) 替换 point_of
m2 = re.search(r"def point_of\(hwnd, key: str\):.*?\n(?=\n\n)", s, re.S)
assert m2, "point_of 没找到"
s = s[:m2.start()] + NEW_POINT + s[m2.end():]

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("coords_send: 已改为边缘锚点 + 绝对偏移")

# 3) 重写 coords.json（清掉旧的比例格式）
with io.open(r"D:\dsh\projects\ai-chat-bot\coords.json", "w", encoding="utf-8") as f:
    json.dump({"_cleared": "改用 DEFAULT 里的锚点偏移；如需自定义请直接改 coords.json 里同结构"},
              f, ensure_ascii=False, indent=1)
print("coords.json: 已清空旧比例值（回落到 DEFAULT 锚点）")
