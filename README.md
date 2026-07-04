# Soriono Prelude

**Alle Daten der Schweiz mit einem Klick.**

Prelude enthält einen lokalen Katalog mit 22’635 profilierten Schweizer
Open-Data-Ressourcen sowie 15’859 separat indexierten Dokumenten. Es bietet
Suche, Materialisierung, Schema-Inspektion, abgesicherte lokale SQL-Abfragen,
paginierte Resultate und Reproduktionspakete.

Jede materialisierte Quelle erhält einen `sql_name`. Client-SQL darf nur diese
Aliase verwenden; Dateipfade, URLs und DuckDB-Reader bleiben serverintern.
Das vollständige Resultat wird lokal gespeichert, während MCP maximal 200
Vorschauzeilen beziehungsweise 500 Zeilen pro Seite zurückgibt.

PDF, DOC, DOCX, ODT, RTF und HTML werden nicht als SQL-Tabellen behandelt.
Ihre Metadaten sind sofort durchsuchbar; Inhalte werden nur bei Bedarf
heruntergeladen, extrahiert, lokal gecacht und in einem separaten
Volltextindex abgelegt. PDF-Seiten, Extraktionsmethode, Inhalts-Hash und
Warnungen bleiben nachvollziehbar.

Jede inhaltliche Prelude-Antwort soll mit dem Abschnitt
**Vorgehen und Reproduktion** enden. Er dokumentiert Arbeitsschritte,
Tabellen- und Dokumentquellen, SQL und – falls vorhanden – das Resultat-Handle.

Nicht enthalten sind Literaturrecherche, Statistik, Regression und
wissenschaftliche Berichte (Maestro) sowie eigene Unternehmensdaten,
Datenschutz- und Auditfunktionen (Orchestra).

```powershell
uv sync --extra dev
uv run --frozen pytest -q
uv run --frozen soriono-prelude doctor
uv run --frozen soriono-prelude mcp-server
```

Details: [Client-Workflow](docs/CLIENT_WORKFLOW.md),
[Architektur](docs/ARCHITECTURE.md) und
[Installation/Offline](docs/DISTRIBUTION.md).

Installationsanleitungen:

- [Soriono Prelude mit Codex verwenden](docs/CODEX_INSTALLATION.md)
- [MCPB-Distribution und Offline-Verhalten](docs/DISTRIBUTION.md)
