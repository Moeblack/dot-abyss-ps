param(
    [string]$GameRoot = (Join-Path $PSScriptRoot '..\..\dotabyss_unpacked')
)
$ErrorActionPreference = 'Stop'
$gameDirectory = (Resolve-Path -LiteralPath $GameRoot).Path
& dotnet build (Join-Path $PSScriptRoot 'DotAbyss.Local.csproj') -c Release "-p:GameRoot=$gameDirectory" --nologo
if ($LASTEXITCODE -ne 0) { throw 'Android adapter build failed.' }
$outputDirectory = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'bin\Release\net10.0\DotAbyss.Local.dll') -Destination $outputDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'config.example.json') -Destination $outputDirectory
Write-Output (Join-Path $outputDirectory 'DotAbyss.Local.dll')
