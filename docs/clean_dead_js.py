# -*- coding: utf-8 -*-
"""删掉残留的「试发」事件监听（元素已不存在，留着是死代码）。"""
import io
import re

P = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"
with io.open(P, "r", encoding="utf-8") as f:
    h = f.read()

# 从"面板视图：悬停显示「试发」"到该点击处理结束（最后一行 })）； 为止
start = h.find("// 面板视图：悬停显示「试发」")
if start < 0:
    start = h.find('document.addEventListener("mouseover", e => {')
if start >= 0:
    end = h.find("// ---------------- 坐标校准 ----------------", start)
    if end < 0:
        end = start + 2000
    removed = h[start:end]
    h = h[:start] + h[end:]
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(h)
    print("已删除 %d 字符的残留监听" % len(removed))
else:
    print("没找到残留监听")

print("剩余 stSend:", h.count("stSend"), "| stPanelGrid:", h.count("stPanelGrid"))
