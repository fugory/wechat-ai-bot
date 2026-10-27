# -*- coding: utf-8 -*-
"""map_by_send 保存映射时写入收藏指纹。"""
import io

P = r"D:\dsh\projects\ai-chat-bot\map_by_send.py"
with io.open(P, "r", encoding="utf-8") as f:
    s = f.read()

if "_fingerprint" in s:
    print("已存在，跳过")
else:
    OLD = "    sm.save_map(mapping)"
    NEW = (
        "    try:\n"
        "        mapping[\"_fingerprint\"] = sm.map_fingerprint(db)\n"
        "    except Exception as _e:\n"
        "        print(\"指纹写入失败（不影响映射）：%s\" % _e)\n"
        "    sm.save_map(mapping)")
    assert OLD in s, "save_map 调用没找到"
    s = s.replace(OLD, NEW, 1)
    with io.open(P, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print("map_by_send: 已写入指纹")
