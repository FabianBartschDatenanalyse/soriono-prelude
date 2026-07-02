# Soriono Prelude

**Alle Daten der Schweiz mit einem Klick.**

Prelude enthält den vollständigen lokalen Katalog mit 22’635 Schweizer
Open-Data-Ressourcen. Es bietet Suche, Materialisierung, Schema-Inspektion,
abgesicherte lokale SQL-Abfragen, paginierte Resultate und
Reproduktionspakete.

Jede materialisierte Quelle erhält einen `sql_name`. Client-SQL darf nur diese
Aliase verwenden; Dateipfade, URLs und DuckDB-Reader bleiben serverintern.
Das vollständige Resultat wird lokal gespeichert, während MCP maximal 200
Vorschauzeilen beziehungsweise 500 Zeilen pro Seite zurückgibt.

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
