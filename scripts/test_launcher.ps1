# Read-only launcher contract checks. All HTTP/process probes are mocked.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
Push-Location $projectRoot
try {
    foreach ($scriptName in @('chatbi.ps1','start_secure_runtime.ps1')) {
        $tokens = $null; $errors = $null
        [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $scriptName), [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors.Count) { throw "PowerShell parse failure: $scriptName" }
    }
    $env:CHATBI_TEST_EXPECTED_VERSION = & $pythonPath -m scripts.runtime_support version
    function Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction); [pscustomobject]@{ OwningProcess = 424242 } }
    function Invoke-RestMethod {
        param($Uri, $TimeoutSec)
        if ($Uri -like '*/api/v1/runtime') { return [pscustomobject]@{ source_version = $env:CHATBI_TEST_EXPECTED_VERSION } }
        if ($Uri -like '*/health/ready') { return [pscustomobject]@{ status = 'ready' } }
        return [pscustomobject]@{ 'nanosecond heartbeat' = 1 }
    }
    function Start-Process { throw 'Idempotent start must not launch another process' }
    $output = & (Join-Path $PSScriptRoot 'chatbi.ps1') status | ConvertFrom-Json
    if (-not $output.ApiReady -or $output.RestartRequired) { throw 'Ready/version status mismatch' }
    & (Join-Path $PSScriptRoot 'chatbi.ps1') start -NoBrowser
    $env:CHATBI_TEST_EXPECTED_VERSION = 'older-code'
    $output = & (Join-Path $PSScriptRoot 'chatbi.ps1') status | ConvertFrom-Json
    if (-not $output.RestartRequired) { throw 'Old server must require restart' }
    function Get-CimInstance { param($ClassName,$Filter); [pscustomobject]@{ ExecutablePath = 'C:\unrelated\python.exe'; CommandLine = 'something else' } }
    function Stop-Process { throw 'Unrelated process must never be stopped' }
    $refused = $false
    try { & (Join-Path $PSScriptRoot 'chatbi.ps1') stop } catch { $refused = $_.Exception.Message -like '*refusing to stop*' }
    if (-not $refused) { throw 'Unrelated listener guard failed' }
    function Get-CimInstance {
        param($ClassName,$Filter)
        if ($Filter -eq 'ProcessId=424242') {
            return [pscustomobject]@{ ExecutablePath = 'C:\runtime\python.exe'; ParentProcessId = 101; CommandLine = 'python -m uvicorn chatbi.api.app:app' }
        }
        return [pscustomobject]@{ ExecutablePath = $pythonPath; CommandLine = 'python -m uvicorn chatbi.api.app:app' }
    }
    $env:CHATBI_TEST_STOPPED_LISTENER = '0'
    function Stop-Process { param($Id); $env:CHATBI_TEST_STOPPED_LISTENER = [string]$Id }
    function Start-Sleep { param($Milliseconds) }
    & (Join-Path $PSScriptRoot 'chatbi.ps1') stop
    if ($env:CHATBI_TEST_STOPPED_LISTENER -ne '424242') { throw 'Verified venv child was not stopped' }
    Write-Output 'Launcher checks passed: syntax, version detection, idempotent start, unrelated-process protection, verified venv child.'
}
finally {
    Remove-Item Env:CHATBI_TEST_EXPECTED_VERSION -ErrorAction SilentlyContinue
    Remove-Item Env:CHATBI_TEST_STOPPED_LISTENER -ErrorAction SilentlyContinue
    Pop-Location
}
