param(
    [Parameter(Mandatory = $true)]
    [string]$CertificatePath,
    [Parameter(Mandatory = $true)]
    [string]$CertificateChainPath,
    [Parameter(Mandatory = $true)]
    [string]$Pkcs11KeyUri,
    [string]$OpenSslConfigPath,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

foreach ($required in @($CertificatePath, $CertificateChainPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required signing material is missing: $required"
    }
}
if ($OpenSslConfigPath) {
    if (-not (Test-Path -LiteralPath $OpenSslConfigPath -PathType Leaf)) {
        throw "OpenSSL configuration is missing: $OpenSslConfigPath"
    }
    $env:OPENSSL_CONF = (Resolve-Path -LiteralPath $OpenSslConfigPath).Path
}

if (-not $SkipBuild) {
    & (Join-Path $root "scripts/build_mcpb.ps1")
    if ($LASTEXITCODE -ne 0) { throw "MCPB build failed: $LASTEXITCODE" }
}

$manifest = Get-Content -LiteralPath (Join-Path $root "manifest.json") -Raw | ConvertFrom-Json
$artifact = Join-Path $root "dist/$($manifest.display_name.Replace(' ', '-'))-$($manifest.version).mcpb"
$unsigned = "$artifact.unsigned"
$signature = "$artifact.p7s"
$verifiedContent = "$artifact.verified"
Copy-Item -LiteralPath $artifact -Destination $unsigned -Force

& openssl cms -sign -binary -in $unsigned -signer $CertificatePath `
    -certfile $CertificateChainPath -engine pkcs11 -keyform engine `
    -inkey $Pkcs11KeyUri -outform DER -out $signature -md sha256 `
    -nosmimecap
if ($LASTEXITCODE -ne 0) { throw "Cloud-HSM CMS signing failed: $LASTEXITCODE" }

& uv run --project $root --frozen --no-dev python `
    (Join-Path $root "scripts/append_mcpb_signature.py") `
    --mcpb $artifact --signature $signature
if ($LASTEXITCODE -ne 0) { throw "MCPB signature append failed: $LASTEXITCODE" }

& openssl cms -verify -binary -inform DER -in $signature -content $unsigned `
    -CAfile $CertificateChainPath -purpose any -out $verifiedContent
if ($LASTEXITCODE -ne 0) { throw "OpenSSL CMS verification failed: $LASTEXITCODE" }

& npx -y "@anthropic-ai/mcpb@2.1.2" verify $artifact
if ($LASTEXITCODE -ne 0) { throw "MCPB signature verification failed: $LASTEXITCODE" }

$requirements = Join-Path $root "dist/requirements-$($manifest.version).txt"
$sbom = Join-Path $root "dist/$($manifest.name)-$($manifest.version).cdx.json"
& uv export --project $root --frozen --no-dev --format requirements-txt --output-file $requirements
if ($LASTEXITCODE -ne 0) { throw "Dependency export failed: $LASTEXITCODE" }
& uvx --from cyclonedx-bom cyclonedx-py requirements $requirements `
    --pyproject (Join-Path $root "pyproject.toml") --output-reproducible --output-file $sbom
if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed: $LASTEXITCODE" }

$checksum = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$artifact.sha256"
"$checksum  $([IO.Path]::GetFileName($artifact))" | Set-Content -LiteralPath $checksumPath -Encoding ascii

Remove-Item -LiteralPath $unsigned, $signature, $verifiedContent -Force
Get-Item -LiteralPath $artifact, $checksumPath, $sbom | Select-Object Name, Length, FullName
