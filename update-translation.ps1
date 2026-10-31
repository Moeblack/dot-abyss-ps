param([switch]$Fetch)

$ErrorActionPreference = 'Stop'
$translationArguments = @((Join-Path $PSScriptRoot 'pc-translation\prepare_translation.py'), '--bootstrap', '--apply')
if ($Fetch) { $translationArguments += '--fetch' }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') @translationArguments
if ($LASTEXITCODE -ne 0) { throw 'Translation installation failed; review the preceding error.' }
