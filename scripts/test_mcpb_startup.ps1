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

    $uv = (Get-Command uv -ErrorAction Stop).Source
    $probe = Join-Path $PSScriptRoot "mcp_stdio_probe.py"
    $measurements = @()
    foreach ($phase in @("cold", "warm")) {
        $probeJson = & uv run --no-project python $probe --uv $uv --project $project
        if ($LASTEXITCODE -ne 0) {
            throw "$phase MCP probe failed: $LASTEXITCODE"
        }
        $probeResult = $probeJson | ConvertFrom-Json
        if ($probeResult.exit_code -ne 0) {
            throw "$phase MCP start failed: $($probeResult.exit_code)`n$($probeResult.stderr)"
        }
        if (-not $probeResult.initialize_response) {
            throw (
                "$phase MCP start returned no initialize response. " +
                "stdout=$($probeResult.stdout) stderr=$($probeResult.stderr)"
            )
        }
        $response = $probeResult.initialize_response
        if ($response.result.serverInfo.version -notmatch '^0\.3\.0') {
            throw "Unexpected MCP product version: $($response.result.serverInfo.version)"
        }
        if (-not $probeResult.tools_response) {
            throw (
                "$phase MCP start returned no tools/list response. " +
                "stdout=$($probeResult.stdout) stderr=$($probeResult.stderr)"
            )
        }
        $toolNames = @($probeResult.tools_response.result.tools | ForEach-Object { $_.name })
        $requiredTools = @(
            "sync_documents",
            "search_documents",
            "get_document_profile",
            "materialize_document",
            "read_document",
            "format_reproduction_bundle"
        )
        foreach ($requiredTool in $requiredTools) {
            if ($requiredTool -notin $toolNames) {
                throw "$phase MCP package is missing required tool: $requiredTool"
            }
        }
        $measurements += [pscustomobject]@{
            phase = $phase
            seconds = $probeResult.seconds
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
