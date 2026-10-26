# -*- coding: utf-8 -*-
"""表情校准 CLI：在独立进程里跑（GUI 进程里 UIA 不可用）。

用法：python calib_stickers.py
输出：最后一行 JSON {"ok":true,"matched":N,"unmatched":[{md5,tag,dist}...]}
副作用：写 sticker_map.json（md5 → 行/列），并把不可发的写进 config.emoji.hidden
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "gui"))

import bridge as b  # noqa: E402
import sticker_match as sm  # noqa: E402


def main() -> int:
    out = {"ok": False}
    try:
        b.load_dotenv(os.path.join(HERE, ".env"))
        db = b._shared_db()
        matched, unmatched = sm.calibrate_stickers(db)
        if not matched and not unmatched:
            out = {"ok": False,
                   "error": "校准没有结果：表情面板没打开（先手动点一次表情按钮，再点校准）"}
            print("CALIB_RESULT " + json.dumps(out, ensure_ascii=False))
            return 0
        cfg = b.load_config()
        ec = cfg.setdefault("emoji", {})
        ec["hidden"] = [m for m, _d in unmatched]
        tmp = os.path.join(HERE, "config.json.tmp")
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(HERE, "config.json"))
        n = b.sync_sticker_indices(db)
        desc = {}
        try:
            with io.open(os.path.join(HERE, "sticker_desc.json"), encoding="utf-8-sig") as f:
                desc = json.load(f)
        except Exception:
            pass
        out = {
            "ok": True,
            "count": n,
            "matched": len(matched),
            "unmatched": [{"md5": m, "tag": desc.get(m, ""), "dist": d}
                          for m, d in unmatched],
        }
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    print("CALIB_RESULT " + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    _guard_no_bot()
    sys.exit(main())


def _guard_no_bot():
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


