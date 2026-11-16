$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
if (-not (Test-Path '.\backend\.venv')) { python -m venv .\backend\.venv }
$Py = Join-Path $Root 'backend\.venv\Scripts\python.exe'
& $Py -m pip install -r .\backend\requirements.txt
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$Root\backend'; & '$Py' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$Root\backend'; & '$Py' -m app.worker_service"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$Root\backend'; `$env:CLOUDPULSE_INTERNAL_API='http://127.0.0.1:8000'; & '$Py' -m app.simulator_service"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$Root'; corepack pnpm install; corepack pnpm dev"
Write-Host 'CloudPulse API, consumer worker, simulator and frontend are starting.' -ForegroundColor Cyan
Write-Host 'Frontend: http://localhost:3000 | Backend docs: http://localhost:8000/docs' -ForegroundColor Cyan
