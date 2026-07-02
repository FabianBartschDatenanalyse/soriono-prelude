param(
    [Parameter(Mandatory = $true)]
    [string]$Artifact,
    [int]$MaximumInstallSeconds = 180,
    [int]$MaximumColdStartSeconds = 30,
    [int]$MaximumWarmStartSeconds = 5
)

$ErrorActionPreference = "Stop"
$artifactPath = (Resolve-Path -LiteralPath $Artifact).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ("soriono-mcpb-" + [guid]::NewGuid())
$cache = Join-Path $work "uv-cache"
$project = Join-Path $work "project"
New-Item -ItemType Directory -Path $project, $cache -Force | Out-Null
$previousCache = $env:UV_CACHE_DIR
$env:UV_CACHE_DIR = $cache

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory(
        $artifactPath,
        $project
    )

    $install = [Diagnostics.Stopwatch]::StartNew()
    & uv sync --project $project --frozen --no-dev
    if ($LASTEXITCODE -ne 0) { throw "UV installation failed: $LASTEXITCODE" }
    $install.Stop()
    if ($install.Elapsed.TotalSeconds -gt $MaximumInstallSeconds) {
        throw "Installation exceeded $MaximumInstallSeconds seconds: $($install.Elapsed.TotalSeconds)"
    }

    $request = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"release-gate","version":"1.0"}}}'
    $measurements = @()
    foreach ($phase in @("cold", "warm")) {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        $output = $request | uv run --frozen --no-dev --project $project (Join-Path $project "server.py")
        $exitCode = $LASTEXITCODE
        $watch.Stop()
        if ($exitCode -ne 0) { throw "$phase MCP start failed: $exitCode" }
        $line = $output | Where-Object { $_ -match '"id":1' } | Select-Object -First 1
        if (-not $line) { throw "$phase MCP start returned no initialize response" }
        $response = $line | ConvertFrom-Json
        if ($response.result.serverInfo.version -notmatch '^0\.3\.0') {
            throw "Unexpected MCP product version: $($response.result.serverInfo.version)"
        }
        $measurements += [pscustomobject]@{
            phase = $phase
            seconds = [math]::Round($watch.Elapsed.TotalSeconds, 3)
            version = $response.result.serverInfo.version
        }
    }
    $cold = ($measurements | Where-Object phase -eq "cold").seconds
    $warm = ($measurements | Where-Object phase -eq "warm").seconds
    if ($cold -gt $MaximumColdStartSeconds) {
        throw "Cold start exceeded $MaximumColdStartSeconds seconds: $cold"
    }
    if ($warm -gt $MaximumWarmStartSeconds) {
        throw "Warm start exceeded $MaximumWarmStartSeconds seconds: $warm"
    }
    [pscustomobject]@{
        artifact = [IO.Path]::GetFileName($artifactPath)
        install_seconds = [math]::Round($install.Elapsed.TotalSeconds, 3)
        cold_start_seconds = $cold
        warm_start_seconds = $warm
    } | ConvertTo-Json
}
finally {
    $env:UV_CACHE_DIR = $previousCache
    if (Test-Path -LiteralPath $work) {
        $resolved = [IO.Path]::GetFullPath($work)
        $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if (-not $resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to delete unsafe temporary path: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
