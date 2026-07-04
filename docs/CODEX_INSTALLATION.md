# Soriono Prelude mit Codex verwenden

Codex importiert `.mcpb`-Dateien nicht direkt. Das MCPB enthält jedoch den
vollständigen, getesteten STDIO-MCP-Server und kann lokal entpackt und bei
Codex registriert werden.

Die `.cdx.json`-Datei im GitHub-Release ist nur die Software-Stückliste (SBOM)
und keine Installationsdatei.

## Voraussetzungen

- Windows 10 oder 11
- [Codex-App](https://developers.openai.com/codex/app) oder
  [Codex CLI](https://developers.openai.com/codex/cli)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Prüfe `uv` in PowerShell:

```powershell
uv --version
```

Wenn `codex --version` nicht funktioniert, verwendet der Installationsblock
automatisch die in der Codex-App enthaltene CLI.

## Installation

Öffne PowerShell und führe den gesamten Block aus:

```powershell
$version = "0.3.0-rc.2"
$releaseBase = "https://github.com/FabianBartschDatenanalyse/soriono-prelude/releases/download/soriono-prelude-v$version"
$bundle = Join-Path $HOME "Downloads\Soriono-Prelude-$version.mcpb"
$checksumFile = "$bundle.sha256"
$installDir = Join-Path $HOME "Soriono\Prelude-$version"
$stateDir = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Soriono Prelude"
$zip = Join-Path $env:TEMP "Soriono-Prelude-$version.zip"

$codexCommand = Get-Command codex -ErrorAction SilentlyContinue
if ($codexCommand) {
    $codexCli = $codexCommand.Source
} else {
    $codexConfig = Join-Path $HOME ".codex\config.toml"
    $codexConfigText = Get-Content -LiteralPath $codexConfig -Raw
    $codexCliMatch = [regex]::Match(
        $codexConfigText,
        '(?m)^CODEX_CLI_PATH\s*=\s*[''"]([^''"]+)[''"]\s*$'
    )
    if (-not $codexCliMatch.Success) {
        throw "Codex CLI wurde nicht gefunden. Installiere zuerst Codex CLI."
    }
    $codexCli = $codexCliMatch.Groups[1].Value
}
$uvExe = (Get-Command uv -ErrorAction Stop).Source

Invoke-WebRequest `
  -Uri "$releaseBase/Soriono-Prelude-$version.mcpb" `
  -OutFile $bundle
Invoke-WebRequest `
  -Uri "$releaseBase/Soriono-Prelude-$version.mcpb.sha256" `
  -OutFile $checksumFile

$expectedSha256 = ((Get-Content -LiteralPath $checksumFile -Raw) -split "\s+")[0].ToLowerInvariant()
$actualSha256 = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "Ungültige MCPB-Prüfsumme: $actualSha256"
}

New-Item -ItemType Directory -Path $installDir, $stateDir -Force | Out-Null
Copy-Item -LiteralPath $bundle -Destination $zip -Force
Expand-Archive -LiteralPath $zip -DestinationPath $installDir -Force
Remove-Item -LiteralPath $zip -Force

& $codexCli mcp remove soriono-prelude 2>$null
& $codexCli mcp add soriono-prelude `
  --env "SORIONO_PRELUDE_ROOT=$installDir" `
  --env "SORIONO_PRELUDE_STATE_DIR=$stateDir" `
  -- $uvExe run --frozen --no-dev --project "$installDir" "$installDir\server.py"
```

Die Installation benötigt beim ersten Start Internetzugriff, damit `uv` die
gelockten Python-Abhängigkeiten laden kann. Danach liegen diese im lokalen
Cache.

## Installation prüfen

```powershell
& $codexCli mcp list
```

In der Ausgabe muss `soriono-prelude` als aktiver STDIO-Server erscheinen.
Starte Codex anschließend neu beziehungsweise öffne einen neuen Thread und
frage beispielsweise:

> Suche Schweizer Daten zur Bevölkerungsentwicklung nach Gemeinden.

Codex sollte dafür die Werkzeuge von Soriono Prelude verwenden.

## Lokale Daten

Prelude speichert materialisierte Daten, Abfrageergebnisse und
Reproduktionsinformationen standardmäßig hier:

```text
Dokumente\Soriono Prelude
```

Der Katalog und der Server liegen versionsbezogen unter:

```text
%USERPROFILE%\Soriono\Prelude-0.3.0-rc.2
```

## Deinstallation

```powershell
& $codexCli mcp remove soriono-prelude
Remove-Item -LiteralPath "$HOME\Soriono\Prelude-0.3.0-rc.2" -Recurse -Force
```

Der Arbeitsordner unter `Dokumente\Soriono Prelude` wird bewusst nicht
automatisch gelöscht.
