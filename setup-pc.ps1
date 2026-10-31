param(
    [string]$Source,
    [string]$CacheDirectory,
    [string]$SharedDirectory,
    [string]$DataDirectory,
    [switch]$AllowPartial,
    [switch]$Check,
    [switch]$PluginOnly
)

$ErrorActionPreference = 'Stop'
$setupPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $setupPython -PathType Leaf)) {
    & python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Python environment. Install Python 3.11 or newer.' }
}
& $setupPython -c 'import sys; assert sys.version_info >= (3, 11); import dotabyss_ps, fastapi, uvicorn, msgpack' *> $null
if ($LASTEXITCODE -ne 0) {
    & $setupPython -m pip install -e $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install the server dependencies.' }
}
$setupArguments = @('-X', 'utf8', '-m', 'pc.setup')
foreach ($entry in @(@('--source', $Source), @('--cache-dir', $CacheDirectory), @('--shared-dir', $SharedDirectory), @('--data-dir', $DataDirectory))) {
    if ($entry[1]) { $setupArguments += @($entry[0], $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($entry[1])) }
}
if ($AllowPartial) { $setupArguments += '--allow-partial' }
if ($Check) { $setupArguments += '--check' }
if ($PluginOnly) { $setupArguments += '--plugin-only' }
Push-Location -LiteralPath $PSScriptRoot
try {
    & $setupPython @setupArguments
    if ($LASTEXITCODE -ne 0) { throw 'PC setup did not complete. Resolve the error above and run the command again.' }
} finally {
    Pop-Location
}
