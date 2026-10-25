# -*- mode: python ; coding: utf-8 -*-
"""ai-chat-bot 打包配置（onedir 模式）。

为什么用 onedir 而不是 onefile：
  * onefile 每次启动要解压到临时目录，启动慢（本项目依赖一大堆）
  * onedir 调试方便：缺什么文件一眼能看到
  * 数据文件（config.json 等）本来就要放在外面，onefile 也带不走

打包后目录：
  dist/AiChatBot/
    AiChatBot.exe
    _internal/...          ← 代码与依赖（只读，不用管）
    config.json 等数据      ← 首次启动自动生成/复制
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(SPECPATH)   # PyInstaller 提供，不能用 __file__

hidden = []
hidden += collect_submodules("wechatauto")
hidden += ["flask", "webview", "PIL", "psutil", "cryptography",
           "uiautomation", "numpy", "pywin32", "win32clipboard", "win32con"]

datas = [
    (os.path.join(ROOT, "gui", "static"), "gui/static"),
    (os.path.join(ROOT, "data_seed"), "data_seed"),
]

# 需要的项目模块（.py 直接带上，bridge 会 import 它们）
for mod in ("bridge.py", "wxbg.py", "sticker_vision.py", "rolling_summary.py",
            "sticker_match.py", "wininput.py"):
    p = os.path.join(ROOT, mod)
    if os.path.isfile(p):
        datas.append((p, "."))

a = Analysis(
    [os.path.join(ROOT, "gui", "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "scipy", "tkinter", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AiChatBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 无控制台窗口；排查时改成 True 能看到输出
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AiChatBot",
)
