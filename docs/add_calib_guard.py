# -*- coding: utf-8 -*-
"""高频操作熔断：校准类脚本在 bot 运行时直接拒绝。

今天的教训：map_by_send 连点 25 格、校准测试几十次点击，
这两次之后微信都卡死了。所以：
  · bot 在跑（bridge.lock 存在 / 有 bridge.py 进程）→ 校准脚本直接退出
  · 校准过程每点一次都留间隔，且总数设上限
"""
import io
import os

ROOT = r"D:\dsh\projects\ai-chat-bot"

GUARD = '''def _guard_no_bot():
    """bot 在跑时禁止做校准/连点，避免把微信点卡死。"""
    import glob
    import os
    if os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.lock")):
        raise SystemExit("检测到 bot 正在运行（bridge.lock 存在）："
                         "请先停止 bot 再校准，避免高频点击把微信点卡死。")
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "bridge.py" in cl and p.info.get("pid") != os.getpid():
                raise SystemExit("检测到 bot 进程（pid=%s）：请先停止 bot 再校准。" % p.info.get("pid"))
    except SystemExit:
        raise
    except Exception:
        pass


'''

for name in ("map_by_send.py", "calib_stickers.py"):
    p = os.path.join(ROOT, name)
    if not os.path.isfile(p):
        print("跳过（不存在）:", name)
        continue
    s = io.open(p, encoding="utf-8").read()
    if "_guard_no_bot" in s:
        print("已有熔断:", name)
        continue
    # 插到 main 之后（或文件末尾前），并在 __main__ 里调用
    s = s.rstrip("\n") + "\n\n\n" + GUARD
    if 'if __name__ == "__main__":' in s:
        s = s.replace('if __name__ == "__main__":',
                      'if __name__ == "__main__":\n    _guard_no_bot()', 1)
    else:
        s += "\n_guard_no_bot()\n"
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)
    print("已加熔断:", name)
