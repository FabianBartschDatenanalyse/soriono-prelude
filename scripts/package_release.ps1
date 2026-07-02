param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $SkipBuild) {
    & (Join-Path $root "scripts/build_mcpb.ps1")
    if ($LASTEXITCODE -ne 0) { throw "MCPB build failed: $LASTEXITCODE" }
}

$manifest = Get-Content -LiteralPath (Join-Path $root "manifest.json") -Raw | ConvertFrom-Json
$artifact = Join-Path $root "dist/$($manifest.display_name.Replace(' ', '-'))-$($manifest.version).mcpb"
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "Tested MCPB artifact is missing: $artifact"
}

& npx -y "@anthropic-ai/mcpb@2.1.2" info $artifact
if ($LASTEXITCODE -ne 0) { throw "MCPB inspection failed: $LASTEXITCODE" }

$requirements = Join-Path $root "dist/requirements-$($manifest.version).txt"
$sbom = Join-Path $root "dist/$($manifest.name)-$($manifest.version).cdx.json"
& uv export --project $root --frozen --no-dev --format requirements-txt --output-file $requirements
if ($LASTEXITCODE -ne 0) { throw "Dependency export failed: $LASTEXITCODE" }
& uvx --from cyclonedx-bom cyclonedx-py requirements $requirements `
    --pyproject (Join-Path $root "pyproject.toml") --output-reproducible --output-file $sbom
if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed: $LASTEXITCODE" }

$checksum = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$artifact.sha256"
"$checksum  $([IO.Path]::GetFileName($artifact))" |
    Set-Content -LiteralPath $checksumPath -Encoding ascii

Get-Item -LiteralPath $artifact, $checksumPath, $sbom |
    Select-Object Name, Length, FullName
