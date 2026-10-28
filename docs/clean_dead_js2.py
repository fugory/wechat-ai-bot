# -*- coding: utf-8 -*-
"""精确删除残留的 stSend 监听块（按行定位，不靠注释文字）。"""
import io

P = r"D:\dsh\projects\ai-chat-bot\gui\static\index.html"
lines = io.open(P, encoding="utf-8").read().split("\n")

# 找出包含 stSend / stPanelGrid 的行，以及它们所在 addEventListener 块的边界
bad = [i for i, l in enumerate(lines) if "stSend" in l or "stPanelGrid > div" in l]
print("涉及行:", [i + 1 for i in bad])

# 向前找 "document.addEventListener(" 起点，向后找 "\n});" 终点
def block_bounds(idx):
    s = idx
    while s > 0 and "document.addEventListener(" not in lines[s]:
        s -= 1
    e = idx
    while e < len(lines) - 1 and lines[e].strip() != "});":
        e += 1
    return s, e

spans = []
for i in bad:
    s, e = block_bounds(i)
    spans.append((s, e))
# 合并重叠
spans.sort()
merged = []
for s, e in spans:
    if merged and s <= merged[-1][1] + 1:
        merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    else:
        merged.append((s, e))

for s, e in reversed(merged):
    print("删除行 %d-%d" % (s + 1, e + 1))
    del lines[s:e + 1]

out = "\n".join(lines)
io.open(P, "w", encoding="utf-8", newline="\n").write(out)
print("剩余 stSend:", out.count("stSend"), "| stPanelGrid:", out.count("stPanelGrid"))
