$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Install Python dependencies first; see README.' }
if (!(Test-Path -LiteralPath (Join-Path $projectRoot 'node_modules'))) { throw 'Run npm ci first.' }
$env:PYTHONPATH = Join-Path $projectRoot 'src\backend'
$env:PYTHONUTF8 = '1'
$runtimeDirectory = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
docker compose up -d postgres
if ($LASTEXITCODE -ne 0) { throw 'Database container did not start. See README mirror/download fallback.' }
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    docker compose exec -T postgres pg_isready -U studio -d content_studio 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (!$ready) { throw 'PostgreSQL did not become ready.' }
& $pythonPath -m app.cli initdb
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
$services = @(
    @{ name = 'mcp'; args = @('-m', 'app.cli', 'mcp'); port = 8001 },
    @{ name = 'api'; args = @('-m', 'app.cli', 'api'); port = 8000 },
    @{ name = 'message'; args = @('-m', 'app.cli', 'worker', 'message'); port = 0 },
    @{ name = 'run'; args = @('-m', 'app.cli', 'worker', 'run'); port = 0 }
)
foreach ($service in $services) {
    $servicePattern = '(?:^|\s)' + [regex]::Escape($service.args -join ' ') + '\s*$'
    $existing = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like "*$pythonPath*" -and $_.CommandLine -match $servicePattern }
    if ($existing) { Write-Output "$($service.name): already running"; continue }
    if ($service.port -and (Get-NetTCPConnection -LocalPort $service.port -State Listen -ErrorAction SilentlyContinue)) {
        throw "Port $($service.port) is occupied by another process. No process was stopped."
    }
    $process = Start-Process -FilePath $pythonPath -ArgumentList $service.args -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory "$($service.name).out.log") -RedirectStandardError (Join-Path $runtimeDirectory "$($service.name).err.log") -PassThru
    Write-Output "$($service.name): started PID $($process.Id)"
}
if (!(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)) {
    $npmPath = (Get-Command npm.cmd).Source
    $process = Start-Process -FilePath $npmPath -ArgumentList @('run', 'dev') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory 'frontend.out.log') -RedirectStandardError (Join-Path $runtimeDirectory 'frontend.err.log') -PassThru
    Write-Output "frontend: started PID $($process.Id)"
}
Write-Output 'Workspace: http://127.0.0.1:5173/'
Write-Output 'API docs: http://127.0.0.1:8000/docs'
Write-Output 'Process logs: .runtime/*.log'
