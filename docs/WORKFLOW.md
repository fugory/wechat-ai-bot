# 前端 / 控制台改动的固定工作流

> 这份清单是从 2026-10-25/26 的连续翻车里提炼出来的。每条都对应一次真实事故，别跳步。
> 相关：`LESSONS.md` 第十五节。

## 一、改代码前（30 秒）

```powershell
cd D:\dsh\projects\ai-chat-bot
Copy-Item gui\static\index.html gui\static\index.html.bakN -Force   # 改前端先备份
Copy-Item bridge.py bridge.py.bakN -Force                           # 改主程序先备份
```

**不要**用 `pwsh -Command @"...$("x")..."@` 打补丁 —— PowerShell 会解析 `$(...)`。
**所有补丁脚本写成 `.py` 文件再执行**，文件里用 `io.open(..., "w", newline="\n")`。

## 二、改完立刻验（按顺序，缺一不可）

| 改了什么 | 必须做的验证 |
|---|---|
| HTML / JS | 抽出 `<script>` → `node --check` 通过；再重启控制台看 `gui/client.log` 为空 |
| Python | `python -m py_compile 文件` + `pyflakes` 无 undefined |
| 配置相关 | 读一次 `load_config()` 打印关键字段，确认没被写坏 |
| 表情 / 发送 | 跑一次「发送前校验」链路，确认没有错发 |

```powershell
# 前端语法检查（标准动作）
.venv\Scripts\python.exe -c "import io,re; h=io.open(r'gui\static\index.html',encoding='utf-8').read(); m=re.search(r'<script>(.*?)</script>', h, re.S); io.open(r'D:\dsh\scratch\chk.js','w',encoding='utf-8').write(m.group(1) if m else '')"
node --check D:\dsh\scratch\chk.js
```

## 三、看界面：先量，再改

**"界面坏了"先用探针量，禁止凭肉眼改 CSS。**

```js
// 插到 index.html 末尾，重启后读 gui/client.log
setTimeout(() => {
  const r = e => { const x = document.querySelector(e); if (!x) return null;
    const b = x.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.width)]; };
  fetch('/api/client-log?t=' + T, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({msg:'LAYOUT ' + JSON.stringify({
      vw: innerWidth, dw: document.documentElement.clientWidth,
      bodySW: document.body.scrollWidth, docSW: document.documentElement.scrollWidth,
      dpr: devicePixelRatio }), where:'probe'})});
}, 1500);
```

判定：`bodySW <= dw` 就是没溢出，问题在别处（多半是截图脚本）。

## 四、截图（最容易骗自己的环节）

- 开头必须声明 DPI 感知，否则拿到的是虚拟化坐标：

```python
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
```

- 只截**客户区**（`GetClientRect` + `ClientToScreen`），别截整个窗口。
- 截图前先 `SetForegroundWindow`，等 1.5 秒再抓。
- **要发出去的截图**（README / 文档）必须用一份**假的干净配置**跑独立实例：
  复制 `*.py + gui/ + data_seed/` 到 `scratch/demo-app`，造假的群/好友/记忆，截完删掉。

## 五、每次改完的回归

```powershell
.venv\Scripts\python.exe test_debounce.py    # 期望 FAILS: 0
.venv\Scripts\python.exe test_group_parse.py # 期望 FAILS: 0
```

改了「发送 / 表情 / 窗口」相关，再补：

```powershell
.venv\Scripts\python.exe diag_uia_tree.py    # 期望 HEALTHY
```

## 六、提交 / 发版前

```powershell
# 1) 敏感信息扫描（必须三项都"无"）
git grep -I -l -E "ark-[0-9a-f]{8}-" --cached
git grep -I -l -E "sk-[A-Za-z0-9]{16,}" --cached
git grep -I -l -E "wxid_[a-z0-9]{8,}|@chatroom" --cached

# 2) 确认这些不在仓库
git ls-files | Select-String "\.env$|^config\.json$|memory\.db|gui\.token|^imgs/|^dist/"

# 3) 打 exe（改了代码就必须重打，别发旧包）
.venv\Scripts\python.exe -m PyInstaller AiChatBot.spec --noconfirm --clean
# 清 dist 里的运行期数据 + 放使用说明
# 干净目录跑一次，确认 config.json 被种出来且 memory_v2 存在
```

发 Release 时**附件用英文名**（GitHub 会吞掉中文名）：

```powershell
Copy-Item "dist\大肥鲸控制台-vX.zip" "dist\aichatbot-vX-win64.zip"
gh release create vX "dist\aichatbot-vX-win64.zip" --title ... --notes ...
```

## 七、红线

1. 不碰微信窗口样式位（`WS_CAPTION` 等）——会让微信半透明、无法渲染。
2. 不让监控线程反复 `SetWindowPos` / 重新应用样式——会闪屏、抢焦点。
3. 读界面**不激活窗口**；只有真正投递点击的那几秒才允许置前。
4. 发送成功以**数据库回读**为准，不以函数返回值为准。
5. 删除 / 移动文件前，先 `Resolve-Path` 确认目标，且优先"归档"而不是"删除"。

## 八、验证必须走生产入口

冒烟测试通过 ≠ 线上能跑。**验证路径必须和真实运行路径一致**。

| 生产怎么跑 | 就必须这样验 |
|---|---|
| `python bridge.py`（脚本模式） | 真的起一次脚本，不能只 `import` |
| GUI 拉起 bot（子进程） | 从 GUI 点「启动」验证 |
| 打包成 exe | 在干净目录跑 exe |
| 普通权限运行 | 不要用管理员测完就交付 |

带 `if __name__ == "__main__":` 的文件：**任何追加到末尾的插桩/包装都要放在这个守卫之前**，
否则脚本模式下根本不会被定义（`import` 测试看不出来）。
