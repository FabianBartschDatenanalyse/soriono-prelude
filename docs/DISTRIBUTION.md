# Installation und Offline-Verhalten

Das unterstützte Release-Format ist das plattformübergreifende UV-MCPB.
Es startet mit `uv run --frozen --no-dev`.

Beim ersten Start benötigt UV Internetzugriff, falls Python-Abhängigkeiten noch
nicht im lokalen UV-Cache liegen. GPKG, SHP und KML benötigen beim ersten
Spatial-Zugriff zusätzlich den Download der DuckDB-Spatial-Erweiterung. Danach
funktionieren normale Starts und bereits gecachte Quellen offline. Neue
öffentliche Quellen können offline nicht materialisiert werden.

Release-Artefakte sind nur gültig, wenn MCPB-Signaturprüfung, SHA-256,
SBOM und signierter Git-Tag erfolgreich geprüft wurden.
