# -*- coding: utf-8 -*-
"""修修复阶梯里的两个 bug：

1) `No module named 'wxwin'` —— wxwin 在 gui/ 下，bridge 的 sys.path 里没有
2) 顺带把阶梯里的探测改成"局部 import 顶层函数"，避免模块加载顺序导致的 NameError
"""
import io

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"

with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

# 1) 阶梯第一步：用 locals 拿函数，避免名字解析问题
OLD1 = '''        time.sleep(1.2)
        if uia_session_items():
            log("UIA 修复成功：热激活（第 1 步）")
            return True'''
NEW1 = '''        time.sleep(1.2)
        _probe = globals().get("uia_session_items") or _impl_uia_session_items
        if _probe():
            log("UIA 修复成功：热激活（第 1 步）")
            return True'''
if OLD1 in s:
    s = s.replace(OLD1, NEW1, 1)

# 2) 其余几处 uia_session_items() 同样兜底
s = s.replace('''        time.sleep(1.0)
        if uia_session_items():
            log("UIA 修复成功：切换 tab 重绘（第 2 步）")''',
              '''        time.sleep(1.0)
        if (globals().get("uia_session_items") or _impl_uia_session_items)():
            log("UIA 修复成功：切换 tab 重绘（第 2 步）")''', 1)

s = s.replace('''        if uia_session_items():
            log("UIA 修复成功：最小化还原（第 3 步）")''',
              '''        if (globals().get("uia_session_items") or _impl_uia_session_items)():
            log("UIA 修复成功：最小化还原（第 3 步）")''', 1)

s = s.replace('''            if uia_session_items():
                log("UIA 修复成功：重启微信（第 4 步）")''',
              '''            if (globals().get("uia_session_items") or _impl_uia_session_items)():
                log("UIA 修复成功：重启微信（第 4 步）")''', 1)

# 3) wxwin 导入兜底：把 gui 加进 sys.path
OLD3 = '''        import wxwin
        h = wxwin.find_wechat()'''
NEW3 = '''        try:
            import wxwin
        except Exception:
            import sys as _sys
            _g = os.path.join(HERE, "gui")
            if _g not in _sys.path:
                _sys.path.insert(0, _g)
            import wxwin
        h = wxwin.find_wechat()'''
if OLD3 in s:
    s = s.replace(OLD3, NEW3, 1)

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("bridge: 阶梯的两个 bug 已修")
