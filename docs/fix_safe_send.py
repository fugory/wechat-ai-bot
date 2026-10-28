# -*- coding: utf-8 -*-
"""安全改造：不再"哈希不像也硬点"，也不再用坐标表发消息。

原因（2026-10-28 实测）：
  · 面板排序 ≠ 数据库收藏顺序（面板按"最近使用"排）
    → 坐标表里的行列和实际位置对不上（表说第2行第1列，实际在第4行第5列）
    → 按坐标点会发错表情
  · "哈希不像也硬点一次再靠数据库校验"同样有风险：
    点下去的那一刻表情就已经发出去了，校验只能事后发现

原则：**宁可发不出去，也不要发错。**
  · send_by_map 默认关闭（保留函数与坐标表，只做诊断用）
  · 哈希不像 → 不点
  · 只有匹配明确（距离小、且明显优于第二名）才发
"""
import io

P = r"D:\dsh\projects\ai-chat-bot\sticker_match.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

# 1) send_by_map 开头直接返回 False（保留代码便于将来恢复）
OLD = '''def send_by_map(db, wxid: str, name: str, md5: str) -> bool:
    """按缓存坐标发一个表情。返回 True=已发出且校验一致。"""
'''
NEW = '''def send_by_map(db, wxid: str, name: str, md5: str) -> bool:
    """按缓存坐标发一个表情 —— **当前默认禁用**。

    实测面板按"最近使用"排序，与数据库收藏顺序不一致，
    按坐标点会点到别的表情。安全问题优先，暂时关闭。
    """
    if not _ALLOW_MAP_SEND:
        return False
'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

# 加开关
if "_ALLOW_MAP_SEND" not in s.split("def send_by_map")[0]:
    anchor = "PANEL_REF_SIZE = 643.0"
    assert anchor in s
    s = s.replace(anchor, "_ALLOW_MAP_SEND = False   # 坐标表发送开关（面板排序不可靠，默认关）\n" + anchor, 1)

# 2) 去掉"仍然不像 → 硬点"的整段
start = s.find("                if dist > 20:\n                    # 仍然不像：按坐标点一次")
end = s.find("                    return False", start)
if start > 0 and end > 0:
    end = s.find("\n", s.find("return False", start)) + 1
    block = s[start:end]
    s = s[:start] + """                if dist > 20:
                    b.log("目标格始终不像（%d）：放弃，避免发错表情" % dist)
                    try:
                        bg.close_emoji_panel()
                    except Exception:
                        pass
                    return False
""" + s[end:]
    print("已移除'硬点'分支，改为放弃")
else:
    print("⚠ 没找到硬点分支")

with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("完成")
