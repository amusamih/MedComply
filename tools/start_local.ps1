param([switch]$Install, [switch]$NewFixture)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
New-Item -ItemType Directory -Path .local -Force | Out-Null
if (-not (Test-Path -LiteralPath .local/database-password.txt)) {
    $taskBytes=New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($taskBytes)
    [Convert]::ToBase64String($taskBytes) | Set-Content -LiteralPath .local/database-password.txt -NoNewline
}
if (-not (Test-Path -LiteralPath .venv/Scripts/python.exe)) { py -3.12 -m venv .venv }
if ($Install) {
    & .venv/Scripts/python.exe -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & .venv/Scripts/python.exe -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw 'Editable installation failed' }
}
docker compose up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and retry' }
$taskAnvil=Get-NetTCPConnection -LocalPort 8545 -State Listen -ErrorAction SilentlyContinue
if (-not $taskAnvil) {
    $taskBinary=(Get-Command anvil -ErrorAction Stop).Source
    Start-Process -FilePath $taskBinary -ArgumentList @('--host','127.0.0.1','--port','8545','--chain-id','31337','--timestamp','1735689600','--silent') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput "$taskRoot/.local/anvil.stdout.log" -RedirectStandardError "$taskRoot/.local/anvil.stderr.log" | Out-Null
    Start-Sleep -Seconds 1
}
forge build --root contracts
if ($LASTEXITCODE -ne 0) { throw 'Contract compilation failed' }
if ($NewFixture) { & .venv/Scripts/python.exe tools/deploy.py --new-deployment }
else { & .venv/Scripts/python.exe tools/deploy.py }
if ($LASTEXITCODE -ne 0) { throw 'A prior Anvil chain has stopped or source changed. Use -NewFixture to create a fresh isolated fixture and preserve old public evidence.' }
& .venv/Scripts/python.exe tools/seed_demo.py
if ($LASTEXITCODE -ne 0) { throw 'Synthetic fixture setup failed' }
& .venv/Scripts/python.exe tools/refresh_fixture_cache.py
if ($LASTEXITCODE -ne 0) { throw 'Quota cache could not be verified' }
$taskApp=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $taskApp) {
    $taskProcess=Start-Process -FilePath "$taskRoot/.venv/Scripts/python.exe" -ArgumentList @('-m','uvicorn','medcomply.api:app','--host','127.0.0.1','--port','8765') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$taskRoot/.local/app.stdout.log" -RedirectStandardError "$taskRoot/.local/app.stderr.log"
    $taskProcess.Id | Set-Content -LiteralPath .local/app-process.txt
}
Write-Output 'MedComply is available at http://127.0.0.1:8765'
