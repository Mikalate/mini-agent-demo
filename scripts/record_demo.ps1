$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$demoScript = Join-Path $PSScriptRoot "run_demo.ps1"
$artifactsDir = Join-Path $projectRoot "artifacts"
$outputFile = Join-Path $artifactsDir "mini-agent-terminal-demo.mp4"
$windowTitle = "MiniAgentDemo"

New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null

$terminalArguments = @(
    "-w", "new",
    "new-tab",
    "--title", $windowTitle,
    "powershell.exe",
    "-NoLogo",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $demoScript
)
Start-Process -FilePath "wt.exe" -ArgumentList $terminalArguments | Out-Null

$terminal = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $terminal = Get-Process -Name "WindowsTerminal" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -like "*$windowTitle*" } |
        Sort-Object StartTime -Descending |
        Select-Object -First 1
    if ($null -ne $terminal) {
        break
    }
}
if ($null -eq $terminal) {
    throw "Windows Terminal window was not found: $windowTitle"
}

$captureTitle = $terminal.MainWindowTitle
$ffmpeg = (Get-Command "ffmpeg" -ErrorAction Stop).Source
$ffmpegArguments = @(
    "-y",
    "-f", "gdigrab",
    "-framerate", "15",
    "-draw_mouse", "0",
    "-i", "title=$captureTitle",
    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "24",
    "-pix_fmt", "yuv420p",
    "-t", "420",
    $outputFile
)

& $ffmpeg @ffmpegArguments
$ffmpegExit = $LASTEXITCODE
if (-not (Test-Path -LiteralPath $outputFile)) {
    throw "FFmpeg did not create the recording (exit=$ffmpegExit)."
}

$file = Get-Item -LiteralPath $outputFile
if ($file.Length -lt 102400) {
    throw "The recording is too small and may not contain the terminal."
}
$durationText = & ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $outputFile
$duration = [double]::Parse($durationText.Trim(), [Globalization.CultureInfo]::InvariantCulture)
if ($duration -lt 30) {
    throw "The recording is shorter than 30 seconds and the demo likely failed."
}
Write-Host "Recording created: $($file.FullName)"
