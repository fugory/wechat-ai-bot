# start-bot.ps1 —— 一键以 live 模式常驻，日志同时落 bridge.log
# 用法：右键「用 PowerShell 运行」，或在终端里 .\start-bot.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "还没建虚拟环境。先执行：" -ForegroundColor Yellow
    Write-Host "  py -3.11 -m venv .venv"
    Write-Host "  .venv\Scripts\python.exe -m pip install wechatauto-replica"
    exit 1
}

if (-not (Test-Path ".\.env") -or -not (Select-String -Path .\.env -Pattern "sk-" -Quiet)) {
    Write-Host "警告：.env 里没看到 sk- 开头的 key，AI 回复会失败。" -ForegroundColor Yellow
}

Write-Host "启动中。微信窗口要保持在桌面上、别锁屏、别最小化。" -ForegroundColor Cyan
Write-Host "停止：Ctrl+C" -ForegroundColor Cyan

& .\.venv\Scripts\python.exe bridge.py --live 2>&1 | Tee-Object -FilePath bridge.log
