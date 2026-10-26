# -*- coding: utf-8 -*-
"""自愈止损改造：
  1. 真退避（60s → 5min → 15min → 停手），且**未到点连探测都不做**
  2. 卡死识别：IsHungAppWindow → 立即停手，不再碰 UIA
  3. uia_should_block 不再触发探测（它原本每调一次就 force 自愈一次，是主要的锤击来源）
  4. 撑开一个手工 reset（用户重启微信后点一下）
"""
import io

BR = r"D:\dsh\projects\ai-chat-bot\bridge.py"

with io.open(BR, "r", encoding="utf-8") as f:
    s = f.read()

# ---------- 1. 状态字段 ----------
OLD_STATE = '''BACKOFF = [60, 300, 900]       # 自愈间隔：1 分钟 → 5 分钟 → 15 分钟'''
NEW_STATE = '''BACKOFF = [60, 300, 900]       # 自愈间隔：1 分钟 → 5 分钟 → 15 分钟
MAX_ATTEMPTS = 4                # 连续失败这么多次就停手，等用户手工重启微信'''
assert OLD_STATE in s
s = s.replace(OLD_STATE, NEW_STATE, 1)

# ---------- 2. uia_healthy 前置门控 ----------
OLD_HEAD = '''    now = time.time()
    if not force and now - _UIA_WATCH["last_check"] < interval:
        return _UIA_WATCH["ok"]'''
NEW_HEAD = '''    now = time.time()

    # 卡死：什么都不做（对着卡死的进程调 UIA 只会更糟）
    if _UIA_WATCH.get("hung"):
        return False

    # 未到退避时间点：连探测都不做
    if not force and now < float(_UIA_WATCH.get("next_try") or 0):
        return _UIA_WATCH["ok"]

    if not force and now - _UIA_WATCH["last_check"] < interval:
        return _UIA_WATCH["ok"]'''
assert OLD_HEAD in s
s = s.replace(OLD_HEAD, NEW_HEAD, 1)

# ---------- 3. 探测前先判卡死 ----------
OLD_PROBE = '''    try:
        # 用库自己的扫描：只读探针在树上不可靠（恒返回 0，会误判坏了）
        # 抢前台的问题已由按需激活守卫解决，这里读是安全的
        names = uia_session_items()'''
NEW_PROBE = '''    # 先看进程是不是卡死（卡死时 probe 会长时间阻塞且毫无意义）
    if _wechat_hung():
        _UIA_WATCH["hung"] = True
        _UIA_WATCH["gave_up"] = True
        _UIA_WATCH["ok"] = False
        log("微信进程无响应（不是 UIA 问题）：已停止所有自动尝试，请手动重启微信")
        _write_uia_state()
        return False

    try:
        # 用库自己的扫描：只读探针在树上不可靠（恒返回 0，会误判坏了）
        # 抢前台的问题已由按需激活守卫解决，这里读是安全的
        names = uia_session_items()'''
assert OLD_PROBE in s
s = s.replace(OLD_PROBE, NEW_PROBE, 1)

# ---------- 4. 失败时真退避 ----------
OLD_FAIL = '''    wait = BACKOFF[attempts] if attempts < len(BACKOFF) else None
    if wait is None:
        # 退避用尽：停止自动重试，只留提示
        _UIA_WATCH["gave_up"] = True
        _UIA_WATCH["ok"] = False
        log("UIA 自愈已放弃（连续 %d 次失败）：请手动重启微信客户端，"
            "或重启 bot 后再试" % _UIA_WATCH["fails"])'''
NEW_FAIL = '''    wait = BACKOFF[attempts] if attempts < len(BACKOFF) else None
    if attempts >= MAX_ATTEMPTS or wait is None:
        # 退避用尽：停止自动重试，只留提示
        _UIA_WATCH["gave_up"] = True
        _UIA_WATCH["ok"] = False
        _UIA_WATCH["next_try"] = now + 86400      # 明天再说，别自己复活
        log("UIA 自愈已放弃（连续 %d 次失败）：请手动重启微信客户端，"
            "重启后到状态页点「我重启好了」" % _UIA_WATCH["fails"])'''
assert OLD_FAIL in s
s = s.replace(OLD_FAIL, NEW_FAIL, 1)

# 失败后写 next_try

# ---------- 5. uia_should_block 只读状态 ----------
OLD_BLOCK = '''    return not uia_healthy(force=True, interval=30.0)'''
NEW_BLOCK = '''    # 只读当前状态，绝不在这里触发探测/自愈
    # （原来这里 force 探一次，等于每次发送都锤一遍微信，是卡死的主要嫌疑）
    return (not _UIA_WATCH["ok"]) or bool(_UIA_WATCH.get("hung")) or bool(_UIA_WATCH.get("gave_up"))'''
assert OLD_BLOCK in s
s = s.replace(OLD_BLOCK, NEW_BLOCK, 1)

# ---------- 6. 卡死检测 + reset ----------
HELPERS = '''def _wechat_hung() -> bool:
    """微信主窗口是否无响应（用户拖不动那种）。"""
    try:
        import wxwin
        h = wxwin.find_wechat()
        if not h:
            return False
        return bool(ctypes.windll.user32.IsHungAppWindow(h))
    except Exception:
        return False


def uia_reset_after_restart() -> bool:
    """用户重启微信后调用：清空卡死/放弃状态，允许重新探测。"""
    _UIA_WATCH["hung"] = False
    _UIA_WATCH["gave_up"] = False
    _UIA_WATCH["fails"] = 0
    _UIA_WATCH["attempts"] = 0
    _UIA_WATCH["last_check"] = 0
    _UIA_WATCH["last_heal"] = 0
    _UIA_WATCH["next_try"] = 0
    reset_uia()
    ok = uia_healthy(force=True)
    log("已重置 UIA 状态（重启后重试）：现在 %s" % ("正常" if ok else "仍不可用"))
    return ok


'''
anchor = "def uia_healthy("
assert anchor in s
s = s.replace(anchor, HELPERS + anchor, 1)

# ---------- 7. 写状态时带上 hung / next_try ----------
OLD_W = '''            payload = {"ok": _UIA_WATCH["ok"],
                       "fails": _UIA_WATCH["fails"],'''
NEW_W = '''            payload = {"ok": _UIA_WATCH["ok"],
                       "hung": bool(_UIA_WATCH.get("hung")),
                       "next_try_in": max(0, int(float(_UIA_WATCH.get("next_try") or 0) - time.time())),
                       "fails": _UIA_WATCH["fails"],'''
if OLD_W in s:
    s = s.replace(OLD_W, NEW_W, 1)

# 每次失败后设置 next_try：插在 attempts+1 之后
OLD_INC = '''        _UIA_WATCH["attempts"] = attempts + 1'''
NEW_INC = '''        _UIA_WATCH["attempts"] = attempts + 1
        _UIA_WATCH["next_try"] = now + (BACKOFF[min(attempts, len(BACKOFF) - 1)] if attempts < MAX_ATTEMPTS else 86400)'''
if OLD_INC in s:
    s = s.replace(OLD_INC, NEW_INC, 1)

with io.open(BR, "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("bridge: 止损 + 卡死识别 + 只读 should_block 已改")
