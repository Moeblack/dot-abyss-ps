param(
    [string]$DummyDirectory = (Join-Path $PSScriptRoot '..\..\dotabyss_pc\toolchain\dummy-annotated'),
    [string]$Cecil = (Join-Path $PSScriptRoot '..\..\dotabyss_pc\toolchain\runtime\BepInEx\core\Mono.Cecil.dll'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\artifacts\ida\pc-1.15.0')
)

$ErrorActionPreference = 'Stop'
Add-Type -Path $Cecil
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$apiTypes = [System.Collections.Generic.List[object]]::new()
$nativeMethods = [System.Collections.Generic.List[object]]::new()
$eventEnums = [System.Collections.Generic.List[object]]::new()
$battleTypes = [System.Collections.Generic.List[object]]::new()
$sources = [System.Collections.Generic.List[object]]::new()
$typeShapes = @{}

function Read-AttributeFields($Owner, [string]$AttributeName) {
    $values = @{}
    foreach ($attribute in $Owner.CustomAttributes) {
        if ($attribute.AttributeType.Name -ne $AttributeName) { continue }
        foreach ($field in $attribute.Fields) { $values[$field.Name] = $field.Argument.Value }
    }
    return $values
}

foreach ($assemblyName in @('Project.dll', 'Absf.dll', 'mscorlib.dll', 'UnityEngine.CoreModule.dll')) {
    $assemblyPath = Join-Path $DummyDirectory $assemblyName
    $sources.Add(@{path = [IO.Path]::GetFullPath($assemblyPath); sha256 = (Get-FileHash -LiteralPath $assemblyPath -Algorithm SHA256).Hash.ToLowerInvariant()})
    $assembly = [Mono.Cecil.AssemblyDefinition]::ReadAssembly($assemblyPath)
    try {
        $queue = [System.Collections.Generic.Queue[object]]::new()
        foreach ($type in $assembly.MainModule.Types) { $queue.Enqueue($type) }
        while ($queue.Count -gt 0) {
            $type = $queue.Dequeue()
            foreach ($nested in $type.NestedTypes) { $queue.Enqueue($nested) }
            $typeShapes[$type.FullName] = @{kind = $(if ($type.IsEnum) { 'enum' } elseif ($type.IsValueType) { 'value' } else { 'reference' }); underlying = $(if ($type.IsEnum) { ($type.Fields | Where-Object Name -eq 'value__').FieldType.FullName } else { $null })}
            if ($assemblyName -eq 'mscorlib.dll' -and $type.FullName -notin @('System.String', 'System.Math')) { continue }
            if ($assemblyName -eq 'UnityEngine.CoreModule.dll' -and $type.FullName -notin @('UnityEngine.Random', 'UnityEngine.Mathf')) { continue }
            $methods = @($type.Methods | ForEach-Object {
                $method = $_
                $address = Read-AttributeFields $method 'AddressAttribute'
                $record = @{
                    name = $method.Name
                    signature = $method.FullName
                    return_type = $method.ReturnType.FullName
                    static = $method.IsStatic
                    parameters = @($method.Parameters | ForEach-Object { @{name = $_.Name; type = $_.ParameterType.FullName} })
                    address = $address
                }
                if ($address.ContainsKey('RVA') -and $address.RVA -ne '0x0') {
                    $nativeMethods.Add(@{type = $type.FullName; name = $method.Name; signature = $method.FullName; static = $method.IsStatic; return_type = $method.ReturnType.FullName; parameters = $record.parameters; address = $address})
                }
                $record
            })
            if ($type.IsEnum -and $type.FullName -match 'EventType|BattleQuestType|BattleStageType|FeatureTutorialType|ApiMethod') {
                $eventEnums.Add(@{type = $type.FullName; values = @($type.Fields | Where-Object HasConstant | ForEach-Object { @{name = $_.Name; value = $_.Constant} })})
            }
            $includeApi = $type.FullName -match '^(Project|Absf)\.Api\.'
            $includeBattle = $type.FullName -match 'StageDetail|PartyDetail|BattleSession|SkillEffectDamageCalculator'
            if (-not $includeApi -and -not $includeBattle) { continue }
            $fields = @($type.Fields | ForEach-Object {
                $offset = Read-AttributeFields $_ 'FieldOffsetAttribute'
                @{name = $_.Name; type = $_.FieldType.FullName; public = $_.IsPublic; static = $_.IsStatic; offset = $offset.Offset}
            })
            $typeRecord = @{type = $type.FullName; parent = $type.BaseType.FullName; interfaces = @($type.Interfaces | ForEach-Object { $_.InterfaceType.FullName }); fields = $fields; methods = $methods}
            if ($includeApi) { $apiTypes.Add($typeRecord) }
            if ($includeBattle) { $battleTypes.Add($typeRecord) }
        }
    } finally { $assembly.Dispose() }
}

@{sources = $sources.ToArray(); types = $apiTypes.ToArray(); enums = $eventEnums.ToArray()} |
    ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'api-type-index.json') -Encoding utf8
@{sources = $sources.ToArray(); methods = $nativeMethods.ToArray(); type_shapes = $typeShapes} |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'native-method-index.json') -Encoding utf8
@($nativeMethods | Where-Object { $_.type -eq 'System.String' -and $_.name -in @('Format','Concat','IsNullOrEmpty','Trim','TrimStart') }) |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'system-string-methods.json') -Encoding utf8
$requestTypes = @($apiTypes | Where-Object { $_.type.EndsWith('RequestEntity') } | ForEach-Object {
    $request = $_
    $getters = @($request.methods | Where-Object { $_.name -in @('get_ApiName','get_Method') } | ForEach-Object { @{method = $_.name; return_type = $_.return_type; address = $_.address} })
    if ($getters.Count -gt 0) { @{type = $request.type; getters = $getters; route_binding_verified = $false} }
})
$apiMethodEnum = ($eventEnums | Where-Object { $_.type -eq 'Absf.Api.ApiMethod' }).values
@{sources = $sources.ToArray(); client = 'dotabyss_x_cl 1.15.0'; api_method_enum = $apiMethodEnum; request_types = $requestTypes; note = 'Native metadata declarations require getter analysis to confirm routes.'} |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'request-getter-index.json') -Encoding utf8
@{sources = $sources.ToArray(); types = $battleTypes.ToArray(); native_implementation_verified = $false} |
    ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'battle-contract-index.json') -Encoding utf8
@{api_types = $apiTypes.Count; native_methods = $nativeMethods.Count; enums = $eventEnums.Count} | ConvertTo-Json
