param(
    [string]$Manifest = "catalog/manifest.json"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = [IO.Path]::GetFullPath((Join-Path $repo $Manifest))
$catalogManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$artifacts = @(
    @{
        Name = "resources.sqlite"
        ExpectedHash = [string]$catalogManifest.sha256
        Target = Join-Path $repo "catalog/resources.sqlite"
    },
    @{
        Name = "documents.sqlite"
        ExpectedHash = [string]$catalogManifest.documents_sha256
        Target = Join-Path $repo "catalog/documents.sqlite"
    }
)

if (
    -not ($artifacts | Where-Object {
        -not (Test-Path -LiteralPath $_.Target -PathType Leaf) -or
        (Get-FileHash -LiteralPath $_.Target -Algorithm SHA256).Hash -ne $_.ExpectedHash
    })
) {
    Write-Output "Catalog artifacts already present and valid."
    exit 0
}

$downloadUrl = [string]$catalogManifest.download_url
$archiveHash = [string]$catalogManifest.archive_sha256
if (-not $downloadUrl -or -not $archiveHash) {
    throw "Catalog manifest has no download_url/archive_sha256."
}
$work = Join-Path ([IO.Path]::GetTempPath()) ("soriono-catalog-" + [guid]::NewGuid())
$archive = Join-Path $work "resources.sqlite.zip"
$expanded = Join-Path $work "expanded"
New-Item -ItemType Directory -Path $expanded -Force | Out-Null
try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -UseBasicParsing
    $actualArchiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actualArchiveHash -ne $archiveHash) {
        throw "Catalog archive hash mismatch: expected $archiveHash, got $actualArchiveHash"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    foreach ($artifact in $artifacts) {
        $database = Join-Path $expanded $artifact.Name
        if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
            throw "Catalog archive does not contain $($artifact.Name)."
        }
        $actualHash = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
        if ($actualHash -ne $artifact.ExpectedHash) {
            throw "$($artifact.Name) hash mismatch: expected $($artifact.ExpectedHash), got $actualHash"
        }
        $resolvedTarget = [IO.Path]::GetFullPath($artifact.Target)
        if (-not $resolvedTarget.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe catalog target: $resolvedTarget"
        }
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($resolvedTarget)) -Force | Out-Null
        Copy-Item -LiteralPath $database -Destination $resolvedTarget -Force
        Write-Output "$($artifact.Name) installed and verified: $actualHash"
    }
}
finally {
    if (Test-Path -LiteralPath $work) {
        $resolved = [IO.Path]::GetFullPath($work)
        $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to delete unsafe temporary path: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
