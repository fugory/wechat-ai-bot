# -*- coding: utf-8 -*-
"""P0：给 UIA 入口加串行锁 + COM 错误记录（修正版）。

bridge：函数改名 _impl_ + 文件末尾补同名包装（模块级，安全）
wxbg：**不改名**，类定义后用 monkey-patch 包一层（避免缩进/类边界问题）
"""
import io
import re

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"
WX = r"D:\dsh\projects\ai-chat-bot\wxbg.py"

BR_FUNCS = {
    "uia_current_chat": ("uia_current_chat(ensure: bool = True) -> str",
                         "uia_current_chat(ensure)"),
    "uia_session_items": ("uia_session_items()", "uia_session_items()"),
    "uia_session_items_quiet": ("uia_session_items_quiet()", "uia_session_items_quiet()"),
    "uia_heal": ("uia_heal(cfg=None) -> bool", "uia_heal(cfg)"),
    "uia_find_session": ("uia_find_session(name: str)", "uia_find_session(name)"),
    "uia_click_session": ("uia_click_session(name: str) -> bool", "uia_click_session(name)"),
    "uia_open_chat": ("uia_open_chat(name: str, tries: int = 2, wxid: Optional[str] = None) -> bool",
                      "uia_open_chat(name, tries, wxid)"),
}


def patch_bridge():
    with io.open(BR, "r", encoding="utf-8") as f:
        s = f.read()
    if "_impl_uia_session_items" in s:
        print("bridge 已打过补丁，跳过")
        return
    ok = 0
    for name in BR_FUNCS:
        pat = re.compile(r"^def %s\(" % re.escape(name), re.M)
        if not pat.search(s):
            print("  ⚠ 没找到 def %s(" % name)
            continue
        s = pat.sub("def _impl_%s(" % name, s, count=1)
        ok += 1

    wrappers = []
    for name, (sig, call) in BR_FUNCS.items():
        wrappers.append(
            "def %s:\n"
            "    with uia_call(\"%s\"):\n"
            "        return %s\n\n" % (sig, name, call))

    tail = (
        "\n\n# ---------------- P0：UIA 串行锁包装 ----------------\n"
        "from uia_guard import uia_call  # noqa: E402\n"
        "try:\n"
        "    import uia_guard as _ug\n"
        "    _ug.set_logger(log)\n"
        "except Exception:\n"
        "    pass\n\n"
        + "".join(wrappers))
    s = s.rstrip("\n") + tail
    with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print("bridge: %d 个入口已包装" % ok)


WX_METHODS = ["uia_center", "uia_chat_title", "uia_screen_center",
              "click_screen", "_click_panel_at"]

WX_TAIL = '''

# ---------------- P0：UIA 串行锁（monkey-patch，不改原方法） ----------------
try:
    from uia_guard import uia_call as _uia_call

    def _wrap_method(cls, name):
        orig = getattr(cls, name)

        def wrapped(self, *a, **kw):
            with _uia_call("bg." + name):
                return orig(self, *a, **kw)

        wrapped.__name__ = name
        wrapped.__doc__ = getattr(orig, "__doc__", None)
        setattr(cls, name, wrapped)

    for _m in (%s):
        if hasattr(BgWeChat, _m):
            _wrap_method(BgWeChat, _m)
except Exception as _e:  # pragma: no cover
    print("[wxbg] UIA 锁包装失败（忽略）：%%s" %% _e)
''' % ", ".join('"%s"' % m for m in WX_METHODS)


def patch_wxbg():
    with io.open(WX, "r", encoding="utf-8") as f:
        s = f.read()
    if "_wrap_method(cls, name)" in s:
        print("wxbg 已打过补丁，跳过")
        return
    with io.open(WX, "w", encoding="utf-8", newline="\n") as f:
        f.write(s.rstrip("\n") + WX_TAIL)
    print("wxbg: 5 个方法已 monkey-patch 包装")


if __name__ == "__main__":
    patch_bridge()
    patch_wxbg()
