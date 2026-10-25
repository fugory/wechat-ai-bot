# -*- coding: utf-8 -*-
"""测试台：给定"用户说的一句话"，干跑一遍完整链路，返回"它看到什么 + 会回什么"。

关键设计：**原样构造 ctx 交给 backend.reply()**，走的就是线上那条代码路径，
所以测试台的结论对生产有效（而不是另写一套拼装逻辑再对不上）。

严格只读：
  * 不推进水位、不写 history、不发送任何消息
  * 唯一副作用：新图会调一次视觉并写缓存（等于预热）
"""
import os
import sys
import time
from typing import Any, Dict

ROOT = r"D:\dsh\projects\ai-chat-bot"
sys.path.insert(0, ROOT)

import bridge as b  # noqa: E402


def _pick_target(kind: str) -> Dict[str, Any]:
    cfg = b.load_config()
    if kind == "group":
        groups = b.resolve_groups(b._shared_db(), cfg)
        if not groups:
            return {}
        g = groups[0]
        return {"wxid": g["wxid"], "name": g["name"], "entry": g["entry"]}
    friends = [e for e in (cfg.get("allow_friends") or [])
               if isinstance(e, dict) and e.get("wxid")]
    if not friends:
        return {}
    f = friends[0]
    return {"wxid": f["wxid"], "name": f.get("name") or f["wxid"][:8], "entry": {}}


def simulate(text: str, kind: str = "group", asker: str = "") -> Dict[str, Any]:
    b.load_dotenv(os.path.join(ROOT, ".env"))
    cfg = b.load_config()
    backend = b.build_backend(cfg)
    db = b._shared_db()
    tgt = _pick_target(kind)
    if not tgt:
        return {"ok": False, "error": "没有可用会话（检查 config 白名单）"}
    wxid, disp = tgt["wxid"], tgt["name"]
    nm = b.mem_nick(db)
    nameof = lambda w: nm.get(w) or (w[:8] if w else "")
    my_wxid = ""
    try:
        from wechatauto import WeChatDB
        my_wxid = (WeChatDB().get_self_info() or {}).get("username") or ""
    except Exception:
        pass
    try:
        my_wxid = (db.get_self_info() or {}).get("username") or my_wxid
    except Exception:
        pass
    if not asker:
        asker = "我自己"

    mem = b.load_memory(os.path.join(ROOT, "memory.md")) \
        if (cfg.get("memory") or {}).get("enabled", True) else {}
    memo = b.memory_for(mem, asker) if hasattr(b, "memory_for") else ""

    detail: Dict[str, Any] = {"kind": kind, "chat": disp}
    ctx: Dict[str, Any] = {"kind": kind, "who": disp, "asker": asker, "memory": memo}
    ctx["stickers"] = b.sticker_menu()

    if kind == "group":
        ctx["system"] = (cfg.get("backend") or {}).get("group_system") or ""
        ctx_chars = int((cfg.get("context") or {}).get("group_max_chars") or 6000)
        st: Dict[str, Any] = {}
        scene = b.build_group_scene(db, wxid, my_wxid, ctx_chars, 400,
                                    nameof, ["大肥鲸-aiBot", "大肥鲸"], st)
        try:
            import rolling_summary as rs
            line = rs.summary_line(wxid)
            if line:
                scene = scene.replace("（更早的对话已省略）\n", "", 1)
                scene = line + "\n" + scene
                detail["summary"] = True
        except Exception:
            pass
        q = text
        # 附件：时间归一化 + 发送者约束（与线上同顺序）
        try:
            name2wx: Dict[str, str] = {}
            for k, v in b.group_member_index(db, wxid, {}).items():
                name2wx[k] = v
            for wx, n in nm.items():
                name2wx.setdefault(n, wx)
            # 提问者 wxid 按名字解析（不能用"最后一条消息的发送者"，那可能是机器人自己）
            asker_wxid = name2wx.get(asker) or ""
            only, why = b.parse_sender_constraint(text, asker, name2wx, asker_wxid)
            idx = b.attachment_index(db, wxid, nameof, my_wxid, 12, None, only)
            if idx:
                ctx["attachments"] = idx
                detail["attach_index_lines"] = idx.count("\n") + 1
            target_dt, note = b.parse_relative_time(text)
            if target_dt:
                q = text + note
                detail["time_note"] = note
                hit = b.find_attachment_near(db, wxid, target_dt, nameof, my_wxid,
                                             asker, only_sender=only)
                if hit:
                    ctx["attach_hint"] = hit
                    detail["attach_hint"] = hit
            elif only:
                hit = b.recent_own_attachments(db, wxid, only, nameof, my_wxid, 2)
                if hit:
                    ctx["attach_hint"] = hit
                    detail["attach_hint"] = hit
            detail["sender_constraint"] = why
        except Exception as e:
            detail["attach_error"] = str(e)[:120]

        ctx["messages"] = [{"role": "user",
                            "content": "【群里刚才的对话】\n%s\n\n【现在 @ 你的是 %s】\n%s"
                                       % (scene, asker, q)}]
        detail["scene_chars"] = len(scene)
        detail["scene_lines"] = scene.count("\n") + 1 if scene else 0
        detail["scene_head"] = "\n".join(scene.split("\n")[:8])
    else:
        ctx["system"] = (cfg.get("backend") or {}).get("system") or ""
        hist = b.History(cfg)
        window = hist.window(wxid, hist.friend_turns * 2) if hist.enabled else []
        ctx["messages"] = list(window) + [{"role": "user", "content": text}]
        detail["scene_chars"] = sum(len(str(m.get("content") or "")) for m in window)
        detail["scene_lines"] = len(window)

    # 生成（不发送）：走的就是生产同一函数
    t0 = time.time()
    try:
        reply = backend.reply(text, ctx)
    except Exception as e:
        return {"ok": False, "error": "生成失败：%s" % e, "detail": detail}
    detail["reply_seconds"] = round(time.time() - t0, 2)
    detail["system_chars"] = len(ctx.get("system") or "")
    detail["memory_chars"] = len(memo)
    detail["menu_chars"] = len(ctx.get("stickers") or "")
    total = detail["system_chars"] + detail["scene_chars"] + \
        int(detail.get("attach_index_lines") or 0) + \
        len(ctx.get("attachments") or "") + len(ctx.get("attach_hint") or "")
    detail["prompt_chars"] = total
    detail["est_tokens"] = int(total * 0.72)
    return {"ok": True, "reply": reply, "detail": detail}
