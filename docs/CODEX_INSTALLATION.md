# Soriono Prelude mit Codex verwenden

Codex importiert `.mcpb`-Dateien nicht direkt. Das MCPB enthält jedoch den
vollständigen, getesteten STDIO-MCP-Server und kann lokal entpackt und bei
Codex registriert werden.

Die `.cdx.json`-Datei im GitHub-Release ist nur die Software-Stückliste (SBOM)
und keine Installationsdatei.

## Voraussetzungen

- Windows 10 oder 11
- [Codex CLI](https://developers.openai.com/codex/cli)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

Prüfe in PowerShell:

```powershell
codex --version
uv --version
```

## Installation

Öffne PowerShell und führe den gesamten Block aus:

```powershell
$version = "0.3.0-rc.1"
$releaseBase = "https://github.com/FabianBartschDatenanalyse/soriono-prelude/releases/download/soriono-prelude-v$version"
$bundle = Join-Path $HOME "Downloads\Soriono-Prelude-$version.mcpb"
$installDir = Join-Path $HOME "Soriono\Prelude-$version"
$stateDir = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Soriono Prelude"
$zip = Join-Path $env:TEMP "Soriono-Prelude-$version.zip"
$expectedSha256 = "821e4a8795d0ff1eb52f66b07531c0e6194af2584af781557bf7af96da7ade9c"

Invoke-WebRequest `
  -Uri "$releaseBase/Soriono-Prelude-$version.mcpb" `
  -OutFile $bundle

$actualSha256 = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "Ungültige MCPB-Prüfsumme: $actualSha256"
}

New-Item -ItemType Directory -Path $installDir, $stateDir -Force | Out-Null
Copy-Item -LiteralPath $bundle -Destination $zip -Force
Expand-Archive -LiteralPath $zip -DestinationPath $installDir -Force
Remove-Item -LiteralPath $zip -Force

codex mcp remove soriono-prelude 2>$null
codex mcp add soriono-prelude `
  --env "SORIONO_PRELUDE_ROOT=$installDir" `
  --env "SORIONO_PRELUDE_STATE_DIR=$stateDir" `
  -- uv run --frozen --no-dev --project "$installDir" "$installDir\server.py"
```

Die Installation benötigt beim ersten Start Internetzugriff, damit `uv` die
gelockten Python-Abhängigkeiten laden kann. Danach liegen diese im lokalen
Cache.

## Installation prüfen

```powershell
codex mcp list
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
%USERPROFILE%\Soriono\Prelude-0.3.0-rc.1
```

## Deinstallation

```powershell
codex mcp remove soriono-prelude
Remove-Item -LiteralPath "$HOME\Soriono\Prelude-0.3.0-rc.1" -Recurse -Force
```

Der Arbeitsordner unter `Dokumente\Soriono Prelude` wird bewusst nicht
automatisch gelöscht.
