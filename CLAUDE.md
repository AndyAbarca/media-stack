# Media Stack — Homelab Streaming

## Contexto
Stack de medios auto-hospedado basado en Pelado-Nerdworks/media-stack
(Caddy + Jellyfin + Sonarr + Radarr + Bazarr + qBittorrent + Jackett +
FlareSolverr + Jellyseerr + Wizarr) en Docker Compose, con Gluetun
(ProtonVPN) delante de qBittorrent y rdt-client (Real-Debrid) como
downloader adicional.

## Estado actual
- Fase: armado y testeo en VM de Linux Mint (lab de DevOps), host `andy-dell`,
  path `/home/andy_dell/HomeLab/Streaming`.
- Plan: una vez funcionando acá, migrar a banco de pruebas físico (AMD FX-8350).
- El Jellyfin nativo (apt) de esta VM ya fue desinstalado — el único Jellyfin
  válido de acá en adelante es el del contenedor.
- Gluetun (ProtonVPN, Wireguard) ya está sumado y en uso: qBittorrent corre
  con `network_mode: "service:gluetun"`, server actual `Netherlands`, y port
  forwarding activo (`VPN_PORT_FORWARDING=on`) que empuja el puerto asignado
  por ProtonVPN a la API de qBittorrent vía `VPN_PORT_FORWARDING_UP_COMMAND`.
- rdt-client (Real-Debrid) ya está sumado, levantado y configurado como
  Download Client en Radarr y Sonarr con Priority 1; qBittorrent quedó en
  Priority 2 (backup vía VPN).
- Config de rdt-client validada (2026-09-28): Download path y **Mapped path =
  `/data/torrents`** (tiene que ser absoluto, es lo que ve Sonarr/Radarr),
  Connection Timeout 30000, 2 conexiones por descarga, Minimum free space 2 GB.
  Sus imports son *moves* atómicos, no hardlinks (Real-Debrid seedea, rdtclient
  no). Real-Debrid a veces rechaza releases con "Infringing file": hay que
  sacarlos de la cola con blocklist y elegir otro.
- Radarr (2026-09-28): perfiles "Any" y "HD-1080p" topean en Bluray-1080p (sin
  Remux ni 2160p); "Any" tiene Language=Any + CF "MULTi / Dual Audio" (+100).
  Sonarr y Radarr con Minimum Free Space = 2048 MB.
- Disco `/data` (ext4, `/dev/sdb1`): la reserva de root (5%) topea a los
  containers (UID 1000) en ~95%. Decisión: dejarlo así, no cambiar con tune2fs.
- Jellyfin Web no reproduce REMUX con DTS/PGS (Scary Movie 2000); la app de
  escritorio (Jellyfin Media Player) sí.
- Historial de problemas y soluciones: `docs/TROUBLESHOOTING.md`. Leerlo antes
  de diagnosticar algo que "no descarga" o "no importa".
- Git: `main` trackea `upstream` (Pelado-Nerdworks). Pushear siempre con
  `git push origin main` explícito, nunca al upstream.
- `./data` en el repo es un symlink a `/data/Streaming` (mount externo del
  host), no un directorio real — tenerlo en cuenta antes de asumir rutas o
  espacio libre en el filesystem del repo. Existe también `data.bak/`
  (untracked, no tocar sin que el usuario lo pida — parece backup previo al
  symlink).
- Desalineado conocido con la documentación (no corregido aún, avisar antes
  de tocarlo):
  - README.md y el mensaje 404 del Caddyfile todavía dicen que qBittorrent
    está en `:8080` — en realidad corre con `WEBUI_PORT=8090` detrás de
    gluetun (que publica `8090:8090`).
  - `.env.example` documenta `SERVER_COUNTRIES` como variable configurable,
    pero `docker-compose.yml` tiene el valor hardcodeado (`Netherlands`), no
    usa `${SERVER_COUNTRIES}`.

## Decisiones de arquitectura (tomadas — no proponer alternativas sin que se pida)
- Base: clone de media-stack, no armar cada pieza por separado.
- VPN: Gluetun ya sumado. qBittorrent corre con
  `network_mode: "service:gluetun"` — comparte el namespace de red completo,
  no alcanza con ponerlo en la misma red de Docker. Por esto, el download
  client de qBittorrent en Sonarr/Radarr apunta a host `gluetun` (puerto
  8090), no a `qbittorrent` — ese hostname ya no resuelve en la red `proxy`
  porque qBittorrent no está attached ahí directamente.
- rdt-client (Real-Debrid) como downloader adicional, con prioridad sobre
  qBittorrent en Radarr/Sonarr (Priority 1 vs. 2). No hace P2P — solo HTTPS
  contra la API de Real-Debrid — por eso va en la red `proxy` normal, SIN
  pasar por gluetun/VPN. Monta el mismo `${DATA_DIR}/torrents` que usa
  qBittorrent para que los *arr encuentren los archivos igual.
- Jackett en vez de Prowlarr (decisión tomada). La migración a Prowlarr se
  diagnosticó y planificó (`docs/prowlarr-migration-plan.md`), pero **está en
  pausa por decisión del usuario (2026-09-28)**. No instalar Prowlarr ni
  proponerlo de nuevo salvo que se pida.
- Layout TRaSH Guides: un solo volumen `/data` compartido entre qBittorrent,
  rdt-client y los *arr, para hardlinks + moves atómicos.
- PUID/PGID=1000, TZ=America/Argentina/Mendoza — hardcodeados por servicio en
  el compose (no son variables de `.env`; las únicas env vars reales del
  proyecto son `CONFIG_DIR`, `DATA_DIR` y `PROTONVPN_WG_PRIVATE_KEY`).
- Sin subpath (puerto directo, no detrás de Caddy): qBittorrent (vía gluetun,
  WEBUI_PORT 8090), Jackett (9117), FlareSolverr (8191), Jellyfin (8096),
  rdt-client (6500).

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
- Antes de agregar o modificar cualquier "ports:" en docker-compose.yml,
  correr `sudo ss -tulpn | grep <puerto>` y `docker ps -a --filter
  publish=<puerto>` para confirmar que no hay un conflicto con otro
  servicio de esta VM (ya pasó una vez con CockroachDB en el 8080).
- Cualquier servicio nuevo con WebUI debe tener autenticación (usuario/
  contraseña) confirmada como activa antes de darlo por terminado — no
  asumir que el default de la imagen ya la tiene.
- Nunca configurar nada que requiera exposición a internet (dominios
  públicos, Let's Encrypt con dominio real, UPnP, port forwarding a nivel
  router) sin preguntar explícitamente primero. Este stack es solo-LAN
  por decisión explícita.
- Al commitear, revisá que el diff en stage sea justo el cambio pedido — si
  hay otros cambios pendientes sin commitear en el mismo archivo (por
  ejemplo, algo que el usuario venía editando), no los mezcles sin
  confirmar primero.
