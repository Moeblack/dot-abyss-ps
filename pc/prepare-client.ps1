param(
    [string]$Source = (Join-Path $PSScriptRoot '..\..\dotabyss_x_cl'),
    [string]$Destination = (Join-Path $PSScriptRoot '..\..\dotabyss_pc\local-client'),
    [string]$Release = (Join-Path $PSScriptRoot 'releases\pc-1.15.0.json'),
    [switch]$Verify
)

$ErrorActionPreference = 'Stop'
$arguments = @((Join-Path $PSScriptRoot 'prepare_client.py'), '--release', $Release, '--destination', $Destination)
if ($Verify) { $arguments += '--verify' } else { $arguments += @('--source', $Source) }
& python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Local PC client preparation failed.' }
