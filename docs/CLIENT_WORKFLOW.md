# Client-Workflow

1. Wenn eine Websuche verfügbar ist, führt der Client höchstens zwei gezielte
   Websuchen als schnelle Vorprüfung durch. Eine belastbare Webquelle wird
   nicht verworfen, nur weil der lokale Katalog keine passende Ressource
   enthält.
2. Prelude wird parallel oder anschließend verwendet, wenn strukturierte
   Daten, eine Berechnung, eine vollständige Rangliste, amtliche Open Data
   oder Reproduzierbarkeit benötigt werden. Ergänzende Web- und Katalogbelege
   werden kombiniert.
3. Bei Vergleichen prüft der Client, ob Kennzahl, Grundgesamtheit,
   geografische Ebene und mindestens zwei geeignete Zeitpunkte
   übereinstimmen. Ein ungeeigneter erster Querschnitt beendet die Suche nicht.
4. Der MCP-Client erstellt knappe Suchfassungen auf Deutsch, Französisch,
   Italienisch und Englisch, erhält Eigennamen, Orte, Jahre, Kennungen und
   Dateiformate und übergibt sie als `search_queries` mit den Schlüsseln
   `de`, `fr`, `it` und `en`. `search_resources` durchsucht sie parallel und
   fusioniert die Treffer mit der unveränderten Originalfrage.
5. `get_resource_profile` oder `get_context_bundle` prüft Herkunft und
   Readiness.
6. Bei PXWeb bedeutet `duckdb_readable: false` nur, dass kein direkter
   DuckDB-Reader verwendet wird. Vor einer Aussage zur Erreichbarkeit ruft der
   Client `materialize_resource` auf und verwendet das aktuelle Ergebnis.
7. `materialize_resource` registriert die Quelle und liefert ihren `sql_name`.
8. `inspect_source` prüft Schema, maximal 100 Stichprobenzeilen und maximal
   200 Distinct-Werte.
9. `validate_sql` prüft ausschließlich aliasbasiertes, schreibgeschütztes SQL.
10. `execute_sql` speichert das vollständige Resultat lokal und liefert maximal
   200 Vorschauzeilen.
11. `get_result_page` liefert höchstens 500 Zeilen pro Seite;
   `get_result_summary` beschreibt das vollständige Ergebnis.
12. `format_reproduction_bundle` dokumentiert Ressourcen-IDs, `sql_name` und
    SQL.

Reader wie `read_csv_auto`, `read_text`, `read_blob`, `glob` oder
`sqlite_scan` sind im Client-SQL verboten.
