# -*- coding: utf-8 -*-
"""修上一步被 shell 转义搞坏的 JS：`const m = \;` → `const m = stPanelMsgEl();`"""
import io

P = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"
with io.open(P, "r", encoding="utf-8") as f:
    h = f.read()

BAD = "    const m = \\;\n"
GOOD = "    const m = document.getElementById('stPanelMsg');\n"

n = h.count(BAD)
if n == 0:
    print("没有发现损坏片段，可能已修复")
else:
    h = h.replace(BAD, GOOD)
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(h)
    print("已修复 %d 处" % n)
