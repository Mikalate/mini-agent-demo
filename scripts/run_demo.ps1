$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$setTemporaryKey = $false

if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    $keyFile = Join-Path $projectRoot "APIkey.txt"
    if (-not (Test-Path -LiteralPath $keyFile)) {
        throw "DEEPSEEK_API_KEY is missing and local APIkey.txt was not found."
    }
    $rawKey = (Get-Content -LiteralPath $keyFile -Raw -Encoding UTF8).Trim()
    if ($rawKey -match '^\s*DEEPSEEK_API_KEY\s*=\s*(.+)\s*$') {
        $agentApiKey = $Matches[1].Trim()
    } else {
        $agentApiKey = $rawKey
    }
    if ([string]::IsNullOrWhiteSpace($agentApiKey)) {
        throw "APIkey.txt does not contain a usable key."
    }
    $env:DEEPSEEK_API_KEY = $agentApiKey
    $setTemporaryKey = $true
}

try {
    Set-Location -LiteralPath $projectRoot
    Start-Sleep -Seconds 4
    python scripts\demo_recording.py
    exit $LASTEXITCODE
} finally {
    if ($setTemporaryKey) {
        Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    }
    $agentApiKey = $null
    $rawKey = $null
}
