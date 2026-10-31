param(
    [int]$Port = 18081,
    [string]$DataDirectory = (Join-Path $PSScriptRoot 'data'),
    [string]$NewGameSchema
)

$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Python environment.' }
}
& $pythonPath -c 'import dotabyss_ps, fastapi, uvicorn, msgpack' *> $null
if ($LASTEXITCODE -ne 0) {
    & $pythonPath -m pip install -e $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install the server dependencies.' }
}
$serverArguments = @('-m', 'dotabyss_ps.app', '--data-dir', $DataDirectory, '--port', $Port)
if ($NewGameSchema) { $serverArguments += @('--new-game-schema', $NewGameSchema) }
& $pythonPath @serverArguments
exit $LASTEXITCODE
