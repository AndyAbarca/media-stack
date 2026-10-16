# Kit para un Project en Claude Desktop

La app de escritorio de Claude (en Windows) no ve esta VM: no lee el repo, ni
la memoria local de Claude Code, ni puede correr comandos. Para que tenga el
mismo contexto, se arma un **Project** con estos archivos.

## Armado (una sola vez)
1. En la app: **Projects → Create project** (ej. "Homelab Streaming").
2. **Instructions**: pegar el contenido de `PROJECT_INSTRUCTIONS.md`.
3. **Project knowledge**: subir estos archivos del repo:
   - `CLAUDE.md` — estado, decisiones, pendientes, reglas.
   - `docker-compose.yml` — definición real de los servicios (no tiene
     secretos: la clave de VPN viene de `.env`).
   - `caddy/Caddyfile` — rutas de subpath.
   - `docs/TROUBLESHOOTING.md` — incidentes resueltos.
   - `docs/DATA_LAYOUT.md` — layout TRaSH de `/data`.
   - `docs/INDEXERS.md` — Jackett + FlareSolverr + indexers.

**Nunca subir** `.env`, nada de `config/` (tiene API keys y bases de datos) ni
`data.bak/`.

## Mantenimiento
Los archivos subidos son una foto: no se actualizan solos. Re-subir el que
cambie cuando:
- se commitea un cambio en `CLAUDE.md`, el compose o el Caddyfile;
- se agrega una entrada a `docs/TROUBLESHOOTING.md`;
- se migra al FX-8350 (cambian IP, rutas y estado).

Atajo para ver qué cambió desde la última subida:
`git log --oneline --since=<fecha> -- CLAUDE.md docker-compose.yml caddy/ docs/`

## Prueba rápida
Preguntarle al Project: "¿a qué host y puerto apunta qBittorrent en Sonarr?".
Tiene que responder `gluetun:8090` (no `qbittorrent:8080`).
