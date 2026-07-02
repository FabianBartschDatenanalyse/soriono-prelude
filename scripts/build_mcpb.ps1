$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $root "manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$dist = Join-Path $root "dist"
$output = Join-Path $dist "$($manifest.display_name.Replace(' ', '-'))-$($manifest.version).mcpb"

New-Item -ItemType Directory -Path $dist -Force | Out-Null
if (Test-Path -LiteralPath $output) {
    Remove-Item -LiteralPath $output -Force
}

& npx -y "@anthropic-ai/mcpb@2.1.2" validate $manifestPath
if ($LASTEXITCODE -ne 0) { throw "MCPB validation failed: $LASTEXITCODE" }
& npx -y "@anthropic-ai/mcpb@2.1.2" pack $root $output
if ($LASTEXITCODE -ne 0) { throw "MCPB build failed: $LASTEXITCODE" }
& npx -y "@anthropic-ai/mcpb@2.1.2" info $output
if ($LASTEXITCODE -ne 0) { throw "MCPB inspection failed: $LASTEXITCODE" }

Get-Item -LiteralPath $output | Select-Object Name, Length, FullName
