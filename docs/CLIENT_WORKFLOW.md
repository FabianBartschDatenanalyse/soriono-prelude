# Client-Workflow

1. Der MCP-Client erstellt knappe Suchfassungen auf Deutsch, Französisch,
   Italienisch und Englisch, erhält Eigennamen, Orte, Jahre, Kennungen und
   Dateiformate und übergibt sie als `search_queries` mit den Schlüsseln
   `de`, `fr`, `it` und `en`. `search_resources` durchsucht sie parallel und
   fusioniert die Treffer mit der unveränderten Originalfrage.
2. `get_resource_profile` oder `get_context_bundle` prüft Herkunft und
   Readiness.
3. `materialize_resource` registriert die Quelle und liefert ihren `sql_name`.
4. `inspect_source` prüft Schema, maximal 100 Stichprobenzeilen und maximal
   200 Distinct-Werte.
5. `validate_sql` prüft ausschließlich aliasbasiertes, schreibgeschütztes SQL.
6. `execute_sql` speichert das vollständige Resultat lokal und liefert maximal
   200 Vorschauzeilen.
7. `get_result_page` liefert höchstens 500 Zeilen pro Seite;
   `get_result_summary` beschreibt das vollständige Ergebnis.
8. `format_reproduction_bundle` dokumentiert Ressourcen-IDs, `sql_name` und SQL.

Reader wie `read_csv_auto`, `read_text`, `read_blob`, `glob` oder
`sqlite_scan` sind im Client-SQL verboten.
