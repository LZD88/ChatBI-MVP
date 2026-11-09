$ErrorActionPreference = "Stop"

function ConvertFrom-SecureValue {
    param([Security.SecureString]$Value)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$stdout = Join-Path $projectRoot ".runtime\chatbi-api.log"
$stderr = Join-Path $projectRoot ".runtime\chatbi-api-error.log"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python"
}

$deepSeekSecure = Read-Host "DeepSeek API Key" -AsSecureString
$qwenSecure = Read-Host "Aliyun Bailian API Key" -AsSecureString
$readerPassword = [Convert]::ToHexString(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(24)
)

try {
    $env:CHATBI_RUNTIME_READER_PASSWORD = $readerPassword
    & $python -c 'import os,pymysql; c=pymysql.connect(host="127.0.0.1",port=33307,user="root",password="",autocommit=True); x=c.cursor(); p=os.environ["CHATBI_RUNTIME_READER_PASSWORD"]; x.execute("ALTER USER chatbi_reader@127.0.0.1 IDENTIFIED BY %s",(p,)); x.execute("ALTER USER chatbi_reader@localhost IDENTIFIED BY %s",(p,)); c.close()'
    if ($LASTEXITCODE -ne 0) {
        throw "Could not configure the MySQL read-only runtime account."
    }

    $listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
    if ($listener) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($owner.CommandLine -notmatch "uvicorn chatbi\.api\.app:app.*--port 8000") {
            throw "Port 8000 is owned by a process other than ChatBI."
        }
        Stop-Process -Id $listener.OwningProcess
        Start-Sleep -Milliseconds 500
    }

    $env:OPENAI_API_KEY = ConvertFrom-SecureValue $deepSeekSecure
    $env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
    $env:LLM_MODEL = "deepseek-chat"
    $env:INTENT_API_KEY = ConvertFrom-SecureValue $qwenSecure
    $env:INTENT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    $env:INTENT_MODEL = "qwen-plus"
    $env:DB_HOST = "127.0.0.1"
    $env:DB_PORT = "33307"
    $env:DB_USER = "chatbi_reader"
    $env:DB_PASSWORD = $readerPassword
    $env:DB_NAME = "chatbi_mvp"
    $env:FEATURE_SIMPLE_CHROMA = "true"

    $process = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "chatbi.api.app:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -WindowStyle Hidden `
        -PassThru
    Write-Output "ChatBI started (PID $($process.Id))"
}
finally {
    Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:INTENT_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:DB_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:CHATBI_RUNTIME_READER_PASSWORD -ErrorAction SilentlyContinue
    $readerPassword = $null
}
