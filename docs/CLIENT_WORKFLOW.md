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
   `de`, `fr`, `it` und `en`.
5. `search_resources` durchsucht Tabellenprofile; `search_documents`
   durchsucht getrennt die Metadaten von PDF, DOC, DOCX, ODT, RTF und HTML
   sowie bereits extrahierte Dokumentinhalte. Beide Suchwege fusionieren die
   vier Sprachfassungen mit der unveränderten Originalfrage.
6. `get_resource_profile` oder `get_context_bundle` prüft Herkunft und
   Readiness.
7. `get_document_profile` prüft Dokumentmetadaten und Extraktionsstatus.
   `materialize_document` lädt und extrahiert einen Inhalt bei Bedarf;
   `read_document` liefert begrenzte Textabschnitte mit Seitenangaben.
8. Bei PXWeb bedeutet `duckdb_readable: false` nur, dass kein direkter
   DuckDB-Reader verwendet wird. Vor einer Aussage zur Erreichbarkeit ruft der
   Client `materialize_resource` auf und verwendet das aktuelle Ergebnis.
9. `materialize_resource` registriert die Quelle und liefert ihren `sql_name`.
10. `inspect_source` prüft Schema, maximal 100 Stichprobenzeilen und maximal
   200 Distinct-Werte.
11. `validate_sql` prüft ausschließlich aliasbasiertes, schreibgeschütztes SQL.
12. `execute_sql` speichert das vollständige Resultat lokal und liefert maximal
   200 Vorschauzeilen.
13. `get_result_page` liefert höchstens 500 Zeilen pro Seite;
   `get_result_summary` beschreibt das vollständige Ergebnis.
14. Als letzten Prelude-Aufruf verwendet der Client bei jeder inhaltlichen
    Antwort `format_reproduction_bundle`. Der zurückgegebene Abschnitt
    **Vorgehen und Reproduktion** dokumentiert Schritte, Quellen-Handles,
    Dokument-IDs, SQL und Resultat-Handle. Das gilt auch ohne SQL.

Reader wie `read_csv_auto`, `read_text`, `read_blob`, `glob` oder
`sqlite_scan` sind im Client-SQL verboten. Dokumentformate werden niemals als
SQL-Tabellen geöffnet.
