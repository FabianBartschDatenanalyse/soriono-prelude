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

## Signierungsumgebung

Das GitHub-Environment `production-signing` benötigt:

- `DIGICERT_SM_HOST`
- `DIGICERT_SM_API_KEY`
- `DIGICERT_SM_CLIENT_CERT_PASSWORD`
- `DIGICERT_SM_CLIENT_CERT_B64`
- `DIGICERT_SM_KEYPAIR_ALIAS`
- `DIGICERT_CLIENT_TOOLS_URL`
- `MCPB_SIGNING_CERT_PEM`
- `MCPB_CERT_CHAIN_PEM`
- `RELEASE_GPG_PUBLIC_KEY`

Der private Code-Signing-Schlüssel bleibt im DigiCert-HSM und wird nicht als
GitHub-Secret gespeichert.
