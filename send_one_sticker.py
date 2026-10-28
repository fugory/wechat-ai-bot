# -*- coding: utf-8 -*-
"""一次性的"试发一个表情"脚本（由控制台以子进程调用）。

为什么不是直接在控制台进程里发：GUI 进程里 UIA 不可用（校准也是走子进程）。
输出：SEND_RESULT {"ok": true/false, "md5": ..., "msg": ...}
"""
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "gui")

import bridge as b
import sticker_match as sm

b.load_dotenv(r"D:\dsh\projects\ai-chat-bot\.env")


def main() -> int:
    md5 = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if len(md5) < 32:
        print("SEND_RESULT " + json.dumps({"ok": False, "msg": "md5 参数不对"}))
        return 1
    db = b._shared_db()
    ok = False
    msg = ""
    try:
        b.set_focus_mode(True)
        ok = sm.send_sticker_by_md5(db, "filehelper", "文件传输助手", md5)
        msg = "已发出并校验" if ok else "没发出去（面板里认不出或面板打不开）"
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            b.set_focus_mode(False)
        except Exception:
            pass
    print("SEND_RESULT " + json.dumps({"ok": bool(ok), "md5": md5, "msg": msg},
                                      ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
