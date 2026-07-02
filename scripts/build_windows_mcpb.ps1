$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tempRoot = [IO.Path]::GetFullPath((Join-Path $env:TEMP "soriono-prelude-windows-build"))
$build = Join-Path $tempRoot "build"
$dist = Join-Path $tempRoot "dist"
$release = Join-Path $root "release"
$catalog = Join-Path $root "catalog\resources.sqlite"

if (-not (Test-Path -LiteralPath $catalog)) {
    throw "Full catalog not found: $catalog"
}

foreach ($target in @($tempRoot, $release)) {
    $full = [IO.Path]::GetFullPath($target)
    $insideProduct = $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
    $insideDedicatedTemp = $full.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)
    if (-not ($insideProduct -or $insideDedicatedTemp)) {
        throw "Unsafe build path: $full"
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

Push-Location $root
try {
    & uv run --with pyinstaller pyinstaller `
        --noconfirm `
        --clean `
        --onedir `
        --contents-directory . `
        --name soriono-prelude `
        --console `
        --collect-submodules mcp.server `
        --collect-all duckdb `
        --collect-all sqlglot `
        --collect-all scipy `
        --collect-all statsmodels `
        --collect-all patsy `
        --add-data "$root\catalog;catalog" `
        --distpath $dist `
        --workpath $build `
        --specpath $build `
        "$root\src\soriono_prelude\__main__.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $buildRoot = Join-Path $dist "soriono-prelude"
    & (Join-Path $buildRoot "soriono-prelude.exe") doctor
    if ($LASTEXITCODE -ne 0) {
        throw "Standalone executable failed its doctor check."
    }

    & (Join-Path $root "scripts\build_mcpb.ps1") -BinaryRoot $buildRoot
    if ($LASTEXITCODE -ne 0) {
        throw "MCPB build failed with exit code $LASTEXITCODE"
    }

    Get-ChildItem $release -File | Select-Object Name, Length, FullName
} finally {
    Pop-Location
}
