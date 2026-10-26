# -*- coding: utf-8 -*-
"""修 P0 包装的自递归：包装函数必须调 _impl_ 版本，不是自己。"""
import io

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"
MARK = "# ---------------- P0：UIA 串行锁包装 ----------------"

BR_FUNCS = {
    "uia_current_chat": ("uia_current_chat(ensure: bool = True) -> str",
                         "_impl_uia_current_chat(ensure)"),
    "uia_session_items": ("uia_session_items()", "_impl_uia_session_items()"),
    "uia_session_items_quiet": ("uia_session_items_quiet()",
                                "_impl_uia_session_items_quiet()"),
    "uia_heal": ("uia_heal(cfg=None) -> bool", "_impl_uia_heal(cfg)"),
    "uia_find_session": ("uia_find_session(name: str)", "_impl_uia_find_session(name)"),
    "uia_click_session": ("uia_click_session(name: str) -> bool",
                          "_impl_uia_click_session(name)"),
    "uia_open_chat": ("uia_open_chat(name: str, tries: int = 2, wxid: Optional[str] = None) -> bool",
                      "_impl_uia_open_chat(name, tries, wxid)"),
}

with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

i = s.find(MARK)
if i > 0:
    s = s[:i].rstrip("\n")
    print("已截掉旧的包装块")
else:
    print("没找到旧包装块，直接追加")

wrappers = []
for name, (sig, call) in BR_FUNCS.items():
    wrappers.append(
        "def %s:\n"
        "    with uia_call(\"%s\"):\n"
        "        return %s\n\n" % (sig, name, call))

tail = ("\n\n" + MARK + "\n"
        "from uia_guard import uia_call  # noqa: E402\n"
        "try:\n"
        "    import uia_guard as _ug\n"
        "    _ug.set_logger(log)\n"
        "except Exception:\n"
        "    pass\n\n"
        + "".join(wrappers))

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s.rstrip("\n") + tail)
print("已重写 %d 个包装（都指向 _impl_）" % len(BR_FUNCS))
