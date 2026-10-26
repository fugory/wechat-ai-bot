# -*- coding: utf-8 -*-
"""把实测坐标写进 coords.json。

基准：窗口 1674x1151，客户区 1652x1140，客户区原点相对窗口 (11, 0)
实测像素（窗口坐标）：
  搜索框   (240,  84)
  输入框   (700, 950)
  表情按钮 (365, 1064)
  发送按钮 (1540, 1065)
"""
import io
import json
import os

P = r"D:\dsh\projects\ai-chat-bot\coords.json"
CLIENT_W, CLIENT_H = 1652, 1140
OX, OY = 11, 0

pts = {
    "search": (240, 84),
    "input": (700, 950),
    "emoji": (365, 1064),
    "send": (1540, 1065),
}

data = {}
for k, (x, y) in pts.items():
    data[k] = [round((x - OX) / CLIENT_W, 4), round((y - OY) / CLIENT_H, 4)]
data["_note"] = "相对客户区的比例；窗口大小变化会自动换算"
data["_measured"] = {"client": [CLIENT_W, CLIENT_H], "at": "2026-10-26 17:15"}

with io.open(P, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print("coords.json 已写入：")
for k in pts:
    print("  %-7s %s" % (k, data[k]))
