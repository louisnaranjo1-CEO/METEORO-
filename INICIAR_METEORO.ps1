$Host.UI.RawUI.WindowTitle = "METEORO TRADER — Solana Judge Desk"
Set-Location "d:\METEORO TRADER\backend"

Write-Host "Iniciando METEORO TRADER SOLANA..." -ForegroundColor Cyan
Write-Host "Abriendo interfaz en http://localhost:8000 en 3 segundos..." -ForegroundColor DarkGray

Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "http://localhost:8000"
} | Out-Null

& ".\.venv\Scripts\python.exe" server.py
