# -*- coding: utf-8 -*-
"""M2 群聊解析的纯函数测试（不连微信）。"""
import bridge as b

entry = {
    "triggers": ["大肥鲸"],
    "self_names": ["大肥鲸-aiBot", "大肥鲸"],
    "require_at": True,
}
cases = [
    # 真实场景：群里 @ 出来的是完整群昵称，且后面直接跟文字
    ("wxid_abc123:\n@大肥鲸-aiBot\u2005你好", True),
    ("wxid_abc123:\n@大肥鲸-aiBot 今晚吃啥", True),
    ("wxid_abc123:\n今天吃什么", False),
    ("@大肥鲸\u2005快速说", True),
    ("大家好啊", False),
    ("@别人 在吗", False),
]
bad = 0
for text, want in cases:
    got = b.group_fired(text, entry)
    if got != want:
        bad += 1
    print(("OK  " if got == want else "FAIL"), repr(text[:30]), "->", got, "want", want)

print("--- 剥 @ 后的正文 ---")
for raw in ("@大肥鲸-aiBot\u2005你好", "@大肥鲸-aiBot 今晚吃啥", "@大肥鲸\u2005在吗"):
    print(repr(raw), "->", repr(b.clean_for_model(raw, entry)))

no_at = {"triggers": ["大肥鲸"], "require_at": False}
print("require_at=False, 无@含触发词 ->", b.group_fired("大肥鲸今天好安静", no_at))
print("两者都不配 ->", b.group_fired("@任何人", {"triggers": [], "require_at": True}))
print("FAILS:", bad)
