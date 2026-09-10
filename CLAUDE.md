# Media Stack — Homelab Streaming

## Contexto
Stack de medios auto-hospedado basado en Pelado-Nerdworks/media-stack
(Caddy + Jellyfin + Sonarr + Radarr + Bazarr + qBittorrent + Jackett +
FlareSolverr + Jellyseerr + Wizarr) en Docker Compose.

## Estado actual
- Fase: armado y testeo en VM de Linux Mint (lab de DevOps), host `andy-dell`,
  path `/home/andy_dell/HomeLab/Streaming`.
- Plan: una vez funcionando acá, migrar a banco de pruebas físico (AMD FX-8350).
- El Jellyfin nativo (apt) de esta VM ya fue desinstalado — el único Jellyfin
  válido de acá en adelante es el del contenedor.

## Decisiones de arquitectura (tomadas — no proponer alternativas sin que se pida)
- Base: clone de media-stack, no armar cada pieza por separado.
- VPN: se va a sumar Gluetun. qBittorrent va a correr con
  `network_mode: "service:gluetun"` — comparte el namespace de red completo,
  no alcanza con ponerlo en la misma red de Docker. Una vez armado esto, el
  download client en Sonarr/Radarr apunta a host `gluetun`, no `qbittorrent`.
- Jackett en vez de Prowlarr (decisión tomada).
- Layout TRaSH Guides: un solo volumen `/data` compartido entre qBittorrent
  y los *arr, para hardlinks + moves atómicos.
- PUID/PGID=1000, TZ=America/Argentina/Mendoza.
- Sin subpath (puerto directo, no detrás de Caddy): qBittorrent (8080, luego
  vía gluetun), Jackett (9117), FlareSolverr (8191), Jellyfin (8096).

## Comandos frecuentes
- docker compose ps / docker compose logs -f <servicio>
- docker compose pull && docker compose up -d
- bash scripts/init-data-dirs.sh (una sola vez, idempotente)
- bash scripts/configure-base-urls.sh (post-boot, idempotente)

## Reglas para vos (Claude)
- No corras `docker compose down -v` ni borres nada bajo data/ o config/ sin
  confirmación explícita.
- No toques .env ni credenciales de VPN/indexers sin mostrar el diff antes.
- Cualquier cambio al compose (sumar Gluetun, cambiar network_mode, etc.):
  mostrame el diff antes de aplicarlo.
