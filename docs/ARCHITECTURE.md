# Architektur

Soriono Prelude ist ein lokaler MCP-Server für Suche, Materialisierung und
SQL-Analyse. Sprachmodell-Reasoning findet ausschließlich im verbundenen
MCP-Client statt.

## Datenfluss

1. Der Client durchsucht den lokalen SQLite-FTS5-Katalog.
2. Eine Ressource wird materialisiert und erhält einen stabilen `sql_name`.
3. Prelude erzeugt intern eine temporäre DuckDB-View. Pfad und Reader bleiben
   serverintern.
4. Client-SQL darf ausschließlich registrierte `sql_name`-Views, CTEs und
   Subqueries referenzieren. Reader-, Netzwerk-, Secret- und
   Extension-Funktionen sind gesperrt.
5. Das vollständige Ergebnis wird lokal als Parquet gespeichert; über MCP
   werden Vorschau, Seiten und Zusammenfassung ausgeliefert.

## Dokumentfluss

1. Ein separater SQLite-FTS5-Index enthält 15’859 Dokumentmetadaten aus
   opendata.swiss.
2. `search_documents` durchsucht Titel, Datensatztitel, Beschreibung,
   Herausgeber und bereits extrahierte Textabschnitte.
3. `materialize_document` akzeptiert nur öffentliche HTTP(S)-Ziele, begrenzt
   Download- und Archivgrößen und extrahiert Inhalte in einen lokalen Cache.
4. `read_document` liefert begrenzte Chunks mit PDF-Seitenzahl, Überschrift,
   Extraktionsmethode und Inhalts-Hash.
5. Dokumente werden nicht in DuckDB registriert und nicht als Tabellen
   interpretiert.

SQLite ersetzt Vespa vollständig im Endnutzer-Runtime. Spatial wird nur beim
ersten GPKG-, SHP- oder KML-Zugriff geladen und anschließend lokal gecacht.

## Grenzen

Prelude enthält keine Literaturrecherche, statistischen Tests, Regressionen
oder wissenschaftlichen Berichte. Diese Funktionen gehören zu Maestro.
Eigene Unternehmensdaten, Datenschutzregeln und Auditpfade gehören zu
Orchestra.
