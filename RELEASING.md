# Soriono Prelude veröffentlichen

Dieses öffentliche Repository enthält ausschließlich Soriono Prelude.
Soriono Maestro und Soriono Orchestra werden aus privaten Repositories gebaut
und dürfen weder als Quellcode noch als Release-Artefakte hier erscheinen.

Normale Pushes und Pull Requests testen Prelude auf Windows, macOS und Linux
mit Python 3.12 und 3.13. Vollständige Release-Gates laufen bei einem Tag
`soriono-prelude-v*` oder bei manueller Auslösung.

## Einmalige Einrichtung

Die GitHub-Umgebung `production-signing` benötigt die in
`docs/DISTRIBUTION.md` beschriebenen DigiCert-, Zertifikats- und
GPG-Secrets.

## Release-Ablauf

1. Den `main`-Workflow vollständig bestehen lassen.
2. Einen signierten Tag erstellen:

   ```bash
   git tag -s soriono-prelude-v0.3.0-rc.1 -m "Soriono Prelude 0.3.0-rc.1"
   git push origin soriono-prelude-v0.3.0-rc.1
   ```

3. GitHub baut das exakt getestete MCPB, prüft es auf allen Plattformen,
   signiert es im HSM und veröffentlicht MCPB, SHA-256 und CycloneDX-SBOM als
   Prerelease.
