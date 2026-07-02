# Installation und Offline-Verhalten

Das unterstützte Release-Format ist das plattformübergreifende UV-MCPB.
Es startet mit `uv run --frozen --no-dev`.

Beim ersten Start benötigt UV Internetzugriff, falls Python-Abhängigkeiten noch
nicht im lokalen UV-Cache liegen. GPKG, SHP und KML benötigen beim ersten
Spatial-Zugriff zusätzlich den Download der DuckDB-Spatial-Erweiterung. Danach
funktionieren normale Starts und bereits gecachte Quellen offline. Neue
öffentliche Quellen können offline nicht materialisiert werden.

Release-Artefakte dieser kostenlosen RC-Stufe sind gültig, wenn der
GPG-signierte Git-Tag, die SHA-256-Prüfsumme und die CycloneDX-SBOM
erfolgreich geprüft wurden.

Das MCPB selbst besitzt dabei noch kein kommerzielles
Code-Signing-Zertifikat. Eine DigiCert- beziehungsweise HSM-Signatur kann
später ergänzt werden, ohne das aktuelle Herkunfts- und Integritätsmodell zu
ersetzen.

## Release-Schlüssel

Der öffentliche Release-Schlüssel liegt unter
`security/release-signing-key.asc`. Der private Schlüssel und seine
Passphrase bleiben ausschließlich auf dem Rechner des Herausgebers.
