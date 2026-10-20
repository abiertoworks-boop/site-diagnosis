# Windows(PowerShell)用の起動スクリプト: 初回は仮想環境と依存を用意し、サーバーを起動する
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  .\.venv\Scripts\python.exe -m playwright install chromium
}
$env:PYTHONUTF8 = "1"
Write-Host "http://127.0.0.1:8000 をブラウザで開いてください(停止は Ctrl+C)"
.\.venv\Scripts\python.exe app\server.py
