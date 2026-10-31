param(
    [int]$Port = 18083,
    [string]$DataDirectory = (Join-Path $PSScriptRoot 'data-pc-1.15.0'),
    [switch]$NoConnect,
    [switch]$Classic,
    [switch]$Demo
)

$ErrorActionPreference = 'Stop'
$consolePython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $consolePython)) {
    throw 'Python environment missing. Prepare the project environment before starting the console.'
}
$consoleEntry = Join-Path $PSScriptRoot 'console\launch.py'
$consoleArgs = @('"' + $consoleEntry + '"')
if ($PSBoundParameters.ContainsKey('Port')) { $consoleArgs += @('--origin', ('http://127.0.0.1:' + $Port)) }
if ($PSBoundParameters.ContainsKey('DataDirectory')) { $consoleArgs += @('--data-dir', ('"' + $DataDirectory + '"')) }
if ($NoConnect) { $consoleArgs += '--no-connect' }
if ($Classic) { $consoleArgs += '--classic' }
if ($Demo) { $consoleArgs += '--demo' }
Start-Process -FilePath $consolePython -ArgumentList $consoleArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
