param(
    [ValidateSet('start','status','stop','restart','doctor','evaluate')][string]$Action = 'status',
    [switch]$NoBrowser,
    [switch]$ForgetSecrets
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$secretPath = Join-Path $runtimeDir 'launcher-secrets.clixml'
$configPath = Join-Path $runtimeDir 'launcher.json'
$config = @{
    ApiPort = 8000; MySqlPort = 33307; ChromaPort = 18081; VmName = 'agent-eval-dev'
    MySqlBin = 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe'
    MySqlData = 'C:\chatbi-mysql-data'
    VBoxManage = 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe'
}
if (Test-Path -LiteralPath $configPath) {
    $overrides = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json -AsHashtable
    foreach ($key in $overrides.Keys) { if (-not $config.ContainsKey($key)) { throw "Unknown launcher setting: $key" }; $config[$key] = $overrides[$key] }
}
function PlainValue([Security.SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
function ProbeHttp([string]$Url) { try { Invoke-RestMethod -Uri $Url -TimeoutSec 3 } catch { $null } }
function PortOpen([int]$Port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) }
function WaitHttp([string]$Url, [int]$Seconds = 90) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do { $answer = ProbeHttp $Url; if ($null -ne $answer) { return $answer }; Start-Sleep -Milliseconds 600 } while ([DateTime]::UtcNow -lt $deadline)
    throw "Health check timed out: $Url. Check .runtime logs and scripts/chatbi.ps1 doctor."
}
function ReadSecrets {
    $values = @{}
    if (Test-Path -LiteralPath $secretPath) {
        try { $values = Import-Clixml -LiteralPath $secretPath }
        catch { throw 'Cannot decrypt saved credentials. Run with -ForgetSecrets to enter them again.' }
    }
    foreach ($name in @('OPENAI_API_KEY','INTENT_API_KEY','DB_PASSWORD')) {
        $existing = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($existing) { $values[$name] = ConvertTo-SecureString -String $existing -AsPlainText -Force }
    }
    foreach ($name in @('OPENAI_API_KEY','INTENT_API_KEY')) {
        if (-not $values.ContainsKey($name)) {
            $value = Read-Host "$name (hidden input, encrypted for current Windows user)" -AsSecureString
            if ($value.Length -eq 0) { throw "Missing $name" }; $values[$name] = $value
        }
    }
    if (-not $values.ContainsKey('DB_PASSWORD')) {
        $randomPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24))
        $values['DB_PASSWORD'] = ConvertTo-SecureString -String $randomPassword -AsPlainText -Force
    }
    foreach ($name in $values.Keys) { [Environment]::SetEnvironmentVariable($name, (PlainValue $values[$name]), 'Process') }
    return $values
}
function ConfigureEnvironment {
    $env:DB_HOST = '127.0.0.1'; $env:DB_PORT = [string]$config.MySqlPort
    $env:DB_NAME = 'chatbi_mvp'; $env:DB_USER = 'chatbi_reader'
    $env:SIMPLE_CHROMA_HOST = '127.0.0.1'; $env:SIMPLE_CHROMA_PORT = [string]$config.ChromaPort
    $env:APP_HOST = '127.0.0.1'; $env:APP_PORT = [string]$config.ApiPort
    if (-not $env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL = 'https://api.deepseek.com/v1' }
    if (-not $env:LLM_MODEL) { $env:LLM_MODEL = 'deepseek-chat' }
    if (-not $env:INTENT_BASE_URL) { $env:INTENT_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1' }
    if (-not $env:INTENT_MODEL) { $env:INTENT_MODEL = 'qwen-plus' }
    $env:FEATURE_SIMPLE_CHROMA = 'true'
}
function EnsureDependencies {
    if (-not (PortOpen $config.MySqlPort)) {
        if (-not (Test-Path -LiteralPath $config.MySqlBin)) { throw 'MySQL executable missing; configure .runtime/launcher.json MySqlBin.' }
        if (-not (Test-Path -LiteralPath $config.MySqlData)) { throw 'MySQL data directory missing; configure MySqlData to an initialized local instance.' }
        $mysqlBase = Split-Path -Parent (Split-Path -Parent $config.MySqlBin)
        Start-Process -FilePath $config.MySqlBin -ArgumentList @('--no-defaults', "--basedir=`"$mysqlBase`"", "--datadir=`"$($config.MySqlData)`"", "--port=$($config.MySqlPort)", '--bind-address=127.0.0.1','--mysqlx=0','--console') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'launcher-mysql.log') -RedirectStandardError (Join-Path $runtimeDir 'launcher-mysql-error.log') | Out-Null
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        while (-not (PortOpen $config.MySqlPort) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 500 }
        if (-not (PortOpen $config.MySqlPort)) { throw 'MySQL did not start. Check .runtime/launcher-mysql-error.log.' }
    }
    $dbCheck = & $pythonPath -m scripts.runtime_support db-check
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Reader/schema check failed. Administrator credentials are needed once to initialize the demo database and SELECT-only account.'
        if (-not $env:DB_ADMIN_USER) { $env:DB_ADMIN_USER = Read-Host 'MySQL administrator user (Enter=root)'; if (-not $env:DB_ADMIN_USER) { $env:DB_ADMIN_USER = 'root' } }
        if (-not [Environment]::GetEnvironmentVariables('Process').Contains('DB_ADMIN_PASSWORD')) {
            $env:DB_ADMIN_PASSWORD = PlainValue (Read-Host 'MySQL administrator password (Enter only if configured empty)' -AsSecureString)
        }
        & $pythonPath -m scripts.runtime_support bootstrap
        if ($LASTEXITCODE -ne 0) { throw 'Demo bootstrap failed; check administrator credentials. No API was stopped.' }
        & $pythonPath -m scripts.runtime_support db-check
        if ($LASTEXITCODE -ne 0) { throw 'Database is not ready after initialization.' }
    }
    if (-not (ProbeHttp "http://127.0.0.1:$($config.ChromaPort)/api/v2/heartbeat")) {
        if (-not (Test-Path -LiteralPath $config.VBoxManage)) { throw 'VirtualBox missing; configure VBoxManage in .runtime/launcher.json.' }
        $vmInfo = & $config.VBoxManage showvminfo $config.VmName --machinereadable 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Configured Chroma VM not found.' }
        if (-not ($vmInfo -match '^VMState="running"$')) {
            & $config.VBoxManage startvm $config.VmName --type headless
            if ($LASTEXITCODE -ne 0) { throw 'Could not start Chroma VM.' }
        }
        WaitHttp "http://127.0.0.1:$($config.ChromaPort)/api/v2/heartbeat" | Out-Null
    }
}
function StopApi {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $config.ApiPort -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { Write-Host 'API already stopped.'; return }
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $isProjectPython = $owner.ExecutablePath -and [IO.Path]::GetFullPath($owner.ExecutablePath) -eq [IO.Path]::GetFullPath($pythonPath)
    # Windows venv launchers can delegate to the base interpreter. Accept that
    # child only with a still-live, verified project venv parent running uvicorn.
    if (-not $isProjectPython -and $owner.ParentProcessId -and $owner.CommandLine -match 'uvicorn\s+chatbi\.api\.app:app') {
        $launcherParent = Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.ParentProcessId)"
        $isProjectPython = $launcherParent.ExecutablePath -and [IO.Path]::GetFullPath($launcherParent.ExecutablePath) -eq [IO.Path]::GetFullPath($pythonPath) -and $launcherParent.CommandLine -match 'uvicorn\s+chatbi\.api\.app:app'
    }
    if (-not $isProjectPython -or $owner.CommandLine -notmatch 'uvicorn\s+chatbi\.api\.app:app') {
        throw 'Port owner is not verified as this project API; refusing to stop it.'
    }
    Stop-Process -Id $listener.OwningProcess; Start-Sleep -Milliseconds 700
    Write-Host 'Project API stopped. MySQL and the VM remain available.'
}
function ShowStatus {
    $runtime = ProbeHttp "http://127.0.0.1:$($config.ApiPort)/api/v1/runtime"
    $localVersion = if (Test-Path -LiteralPath $pythonPath) { & $pythonPath -m scripts.runtime_support version } else { 'venv-missing' }
    $ready = ProbeHttp "http://127.0.0.1:$($config.ApiPort)/health/ready"
    [pscustomobject]@{ ApiListening = (PortOpen $config.ApiPort); ApiReady = [bool]$ready
        LoadedVersion = $runtime.source_version; LocalVersion = $localVersion
        RestartRequired = (-not $runtime -or $runtime.source_version -ne $localVersion)
        MySqlListening = (PortOpen $config.MySqlPort)
        ChromaReady = [bool](ProbeHttp "http://127.0.0.1:$($config.ChromaPort)/api/v2/heartbeat")
        EncryptedCredentials = (Test-Path -LiteralPath $secretPath)
    } | ConvertTo-Json
    if ($Action -eq 'doctor') {
        Write-Host 'Stale API: scripts/chatbi.ps1 restart. MySQL/Chroma failure: check launcher config and .runtime logs.'
        Write-Host 'Missing keys are requested at first start/evaluate and stored with Windows DPAPI for this user.'
    }
}
$savedEnvironment = @{}
foreach ($name in @('OPENAI_API_KEY','INTENT_API_KEY','DB_PASSWORD','DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_ADMIN_USER','DB_ADMIN_PASSWORD','SIMPLE_CHROMA_HOST','SIMPLE_CHROMA_PORT','APP_HOST','APP_PORT','OPENAI_BASE_URL','LLM_MODEL','INTENT_BASE_URL','INTENT_MODEL','FEATURE_SIMPLE_CHROMA')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location -LiteralPath $projectRoot
try {
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    if ($ForgetSecrets -and (Test-Path -LiteralPath $secretPath)) { Remove-Item -LiteralPath $secretPath }
    if ($Action -in @('status','doctor')) { ShowStatus; return }
    if ($Action -eq 'stop') { StopApi; return }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Virtual environment missing. Run uv sync --frozen.' }
    ConfigureEnvironment
    if ($Action -eq 'start') {
        $running = ProbeHttp "http://127.0.0.1:$($config.ApiPort)/api/v1/runtime"
        $version = & $pythonPath -m scripts.runtime_support version
        if ($running -and $running.source_version -eq $version -and (ProbeHttp "http://127.0.0.1:$($config.ApiPort)/health/ready")) {
            Write-Host "ChatBI already running: http://127.0.0.1:$($config.ApiPort)/"; return
        }
    }
    $secureValues = ReadSecrets
    EnsureDependencies
    $secureValues | Export-Clixml -LiteralPath $secretPath
    if ($Action -eq 'evaluate') {
        & $pythonPath -m scripts.evaluate_release
        if ($LASTEXITCODE -ne 0) { throw 'Real-model gate did not pass. Review reports/release-quality.json or check configuration if no report exists.' }
        return
    }
    if (PortOpen $config.ApiPort) { StopApi }
    # Bootstrap credentials must never be inherited by the API process.
    Remove-Item Env:DB_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:DB_ADMIN_USER -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','chatbi.api.app:app','--host','127.0.0.1','--port', [string]$config.ApiPort) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'chatbi-api.log') -RedirectStandardError (Join-Path $runtimeDir 'chatbi-api-error.log') -PassThru
    WaitHttp "http://127.0.0.1:$($config.ApiPort)/health/ready" | Out-Null
    $loaded = WaitHttp "http://127.0.0.1:$($config.ApiPort)/api/v1/runtime"
    if ($loaded.source_version -ne (& $pythonPath -m scripts.runtime_support version)) { throw 'Loaded API source differs from disk; check listener and logs.' }
    Write-Host "ChatBI ready: http://127.0.0.1:$($config.ApiPort)/ (PID $($process.Id), version $($loaded.source_version))"
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$($config.ApiPort)/" }
}
finally {
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    Pop-Location
}
