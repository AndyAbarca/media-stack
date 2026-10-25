# Media Stack — Homelab Streaming

## Contexto
Stack de medios auto-hospedado basado en Pelado-Nerdworks/media-stack
(Caddy + Jellyfin + Sonarr + Radarr + Bazarr + qBittorrent + Jackett +
FlareSolverr + Jellyseerr/Seerr + Wizarr) en Docker Compose, con Gluetun
(ProtonVPN) delante de qBittorrent y rdt-client (Real-Debrid) como
downloader adicional. 12 containers en total.

## Cómo trabajar conmigo
- Estoy aprendiendo DevOps con este homelab. Explicá el **por qué** de cada
  paso, no solo los comandos.
- Respaldá las decisiones con documentación oficial y citala
  (wiki.servarr.com, trash-guides.info, docs.linuxserver.io, wiki de gluetun).
- Flujo para cambios de infra: **plan → backup → cambio acotado → verificar
  con evidencia → reportar**. Apruebo por fases ("solo Fase 0 + A"); al
  terminar una fase, frená y reportá. Nunca pases a la siguiente sin mi OK.
- Commits en español: asunto descriptivo + línea "Por qué:" + bullets con los
  cambios concretos. Nunca un one-liner pelado.
- Push siempre con `git push origin main` (ver Git abajo).

## Estado actual (verificado 2026-10-15)
- Fase: armado y testeo en VM de Linux Mint (lab de DevOps), host `andy-dell`,
  IP `192.168.68.57`, path `/home/andy_dell/HomeLab/Streaming`. Los 12
  containers arriba; gluetun y rdtclient healthy.
- Plan: una vez estable, migrar a banco de pruebas físico (AMD FX-8350). Ver
  checklist más abajo.
- El Jellyfin nativo (apt) de esta VM ya fue desinstalado — el único Jellyfin
  válido es el del contenedor.
- **Gluetun** (ProtonVPN, Wireguard): qBittorrent corre con
  `network_mode: "service:gluetun"`, server `Netherlands`, port forwarding
  activo (`VPN_PORT_FORWARDING=on`) que empuja el puerto asignado a la API de
  qBittorrent vía `VPN_PORT_FORWARDING_UP_COMMAND`.
- **rdt-client** (Real-Debrid): Download Client en Radarr y Sonarr con
  Priority 1; qBittorrent en Priority 2 (backup vía VPN). Config validada
  (2026-09-28): Download path y **Mapped path = `/data/torrents`** (absoluto,
  es lo que ve Sonarr/Radarr), Connection Timeout 30000, 2 conexiones por
  descarga, Minimum free space 2 GB. Sus imports son *moves* atómicos, no
  hardlinks (Real-Debrid seedea, rdtclient no). Real-Debrid a veces rechaza
  releases con "Infringing file": sacarlos de la cola con blocklist y elegir
  otro.
- **Radarr** (2026-09-28): perfiles "Any" y "HD-1080p" topean en Bluray-1080p
  (sin Remux ni 2160p); "Any" tiene Language=Any + CF "MULTi / Dual Audio"
  (+100). Seerr pide todo con el perfil "Any". Sonarr y Radarr con Minimum
  Free Space = 2048 MB.
- **Bazarr** (2026-09-28): perfil 1 como default y asignado a todo;
  providers `opensubtitlescom` (cuenta de opensubtitles **.com**, usuario no
  email), `embeddedsubtitles`, `yifysubtitles`, `gestdown`.
- Disco `/data` (ext4, `/dev/sdb1`, 137 GB, 65% usado al 2026-10-15): la
  reserva de root (5%) topea a los containers (UID 1000) en ~95%. Decisión:
  dejarlo así, no cambiar con tune2fs.
- Jellyfin Web no reproduce REMUX con DTS/PGS (Scary Movie 2000); la app de
  escritorio (Jellyfin Media Player) sí.
- Historial de problemas y soluciones: `docs/TROUBLESHOOTING.md`. Leerlo antes
  de diagnosticar algo que "no descarga" o "no importa".

### Pendientes abiertos (detalle en `docs/TROUBLESHOOTING.md`)
- FlareSolverr sin configurar en Jackett (`FlareSolverrUrl` vacío) → 1337x
  falla. Fix: Jackett → FlareSolverr API URL = `http://flaresolverr:8191`.
- Indexer dontorrent caído (Error 522).
- Categorías mal mapeadas en indexers de Jackett y URLs con la IP del host en
  vez de `jackett:9117`.
- "Allowed Hosts" sin configurar en Sonarr/Radarr (warning).
- **Sin alarmas/monitoreo**: si Real-Debrid deja de ser premium, rdt-client
  falla con `Permission denied`, Radarr prueba release tras release y la API
  devuelve 429 (incidente 2026-10-25). Sumar un aviso: Connect en los *arr y/o
  un script con cron contra la API de RD. RD premium vence el 2027-04-23.
- Riesgo aceptado: Bluray-1080p sin tamaño máximo en Radarr.
- Opcional: perfil de Bazarr "English" tiene `en` + `ea`; sumar `es` si se
  acepta castellano. CF MULTi sin puntaje en "HD-1080p".

### Desalineados conocidos (no corregidos aún — avisar antes de tocarlos)
- qBittorrent figura en `:8080`, pero corre con `WEBUI_PORT=8090` detrás de
  gluetun (que publica `8090:8090`). Aparece mal en:
  - `README.md` (puertos, tabla de accesos, troubleshooting) — y README:143
    dice que el download client va a host `qbittorrent` puerto `8080`; lo
    correcto es `gluetun:8090`. README tampoco documenta gluetun ni rdt-client.
  - El mensaje 404 y un comentario del `caddy/Caddyfile`.
  - Comentarios de `docker-compose.yml` (cabecera, bloque gluetun, bloque
    qbittorrent).
  - `scripts/configure-base-urls.sh` (log final).
- `.env.example` documenta `SERVER_COUNTRIES` (con `Argentina`), pero el
  compose tiene `Netherlands` hardcodeado y no usa `${SERVER_COUNTRIES}`.
- IP `192.168.68.57` hardcodeada en el compose
  (`JELLYFIN_PublishedServerUrl` y `APP_URL` de wizarr).
- `.gitignore` tiene `data/` (con barra), que no matchea el symlink `data` →
  aparece como untracked. Nunca usar `git add -A` / `git add .`; agregar
  archivos por nombre.
- `rdtclient` usa `rogerfar/rdtclient` sin tag (el resto declara `:latest`).

## Decisiones de arquitectura (tomadas — no proponer alternativas sin que se pida)
- Base: clone de media-stack, no armar cada pieza por separado.
- VPN: Gluetun. qBittorrent corre con `network_mode: "service:gluetun"` —
  comparte el namespace de red completo, no alcanza con ponerlo en la misma
  red de Docker. Por esto, el download client de qBittorrent en Sonarr/Radarr
  apunta a host `gluetun` (puerto 8090), no a `qbittorrent` — ese hostname no
  resuelve en la red `proxy`.
- rdt-client (Real-Debrid) como downloader adicional, con prioridad sobre
  qBittorrent (Priority 1 vs. 2). No hace P2P — solo HTTPS contra la API de
  Real-Debrid — por eso va en la red `proxy` normal, SIN gluetun/VPN. Monta el
  mismo `${DATA_DIR}/torrents` que qBittorrent.
- Jackett en vez de Prowlarr. La migración a Prowlarr se diagnosticó y
  planificó (`docs/prowlarr-migration-plan.md`), pero **está en pausa por
  decisión del usuario (2026-09-28)**. No instalar Prowlarr ni proponerlo de
  nuevo salvo que se pida.
- Layout TRaSH Guides: un solo volumen `/data` compartido entre qBittorrent,
  rdt-client y los *arr, para hardlinks + moves atómicos
  (`docs/DATA_LAYOUT.md`).
- PUID/PGID=1000, TZ=America/Argentina/Mendoza — hardcodeados por servicio en
  el compose. Las únicas env vars reales son `CONFIG_DIR`, `DATA_DIR` y
  `PROTONVPN_WG_PRIVATE_KEY`.
- Detrás de Caddy (`:80`, subpath): `/sonarr`, `/radarr`, `/bazarr`,
  `/jellyseerr`, `/wizarr`. Caddyfile en `./caddy/Caddyfile`, `auto_https off`.
- Sin subpath (puerto directo): qBittorrent (vía gluetun, 8090), Jackett
  (9117), FlareSolverr (8191), Jellyfin (8096), rdt-client (6500).

## Git
- `main` trackea `upstream` (Pelado-Nerdworks). Pushear siempre con
  `git push origin main` explícito, nunca al upstream.
- `./data` es un symlink a `/data/Streaming` (mount externo del host), no un
  directorio real — tenerlo en cuenta antes de asumir rutas o espacio libre.
- `data.bak/` (untracked): parece backup previo al symlink. No tocar sin que
  el usuario lo pida.

## Migración al FX-8350 — checklist
1. Cambiar la IP hardcodeada del compose (Jellyfin y wizarr) a la nueva.
2. Ajustar `CONFIG_DIR` / `DATA_DIR` en `.env` si cambian las rutas.
3. Copiar `config/` y `data/` **preservando hardlinks** (`rsync -aH`). Copiar
   sin `-H` ya duplicó archivos una vez (10/9, al mover `./data`).
4. Confirmar que existe `/dev/net/tun` (gluetun) y que el usuario del host es
   UID/GID 1000.
5. Repetir el chequeo de puertos (regla abajo) en la máquina nueva.
6. Actualizar "Estado actual" de este archivo y re-subir el kit de Claude
   Desktop.

## Claude Desktop (Windows)
`docs/claude-desktop/` tiene las instrucciones y la lista de archivos para un
Project en la app de Claude (solo para conversar, sin acceso a esta VM). Si
cambia este archivo, el compose o TROUBLESHOOTING, avisar que hay que
re-subirlos.

## Comandos frecuentes
- docker compose ps / docker compose logs -f <servicio>
- docker compose pull && docker compose up -d
- bash scripts/init-data-dirs.sh (una sola vez, idempotente)
- bash scripts/configure-base-urls.sh (post-boot, idempotente)

## Reglas para vos (Claude)
- No corras `docker compose down -v` ni borres nada bajo data/ o config/ sin
  confirmación explícita.
- No toques .env ni credenciales de VPN/indexers sin mostrar el diff antes.
  Nunca imprimas API keys ni la clave de Wireguard (leerlas de `config.xml` a
  una variable, como en TROUBLESHOOTING).
- Cualquier cambio al compose (sumar servicios, cambiar network_mode, etc.):
  mostrame el diff antes de aplicarlo.
- Antes de agregar o modificar cualquier "ports:" en docker-compose.yml,
  correr `sudo ss -tulpn | grep <puerto>` y `docker ps -a --filter
  publish=<puerto>` para confirmar que no hay conflicto con otro servicio de
  esta VM (ya pasó una vez con CockroachDB en el 8080).
- Cualquier servicio nuevo con WebUI debe tener autenticación (usuario/
  contraseña) confirmada como activa antes de darlo por terminado — no asumir
  que el default de la imagen ya la tiene.
- Nunca configurar nada que requiera exposición a internet (dominios
  públicos, Let's Encrypt con dominio real, UPnP, port forwarding a nivel
  router) sin preguntar explícitamente primero. Este stack es solo-LAN por
  decisión explícita.
- Al commitear, revisá que el diff en stage sea justo el cambio pedido — si
  hay otros cambios pendientes sin commitear en el mismo archivo, no los
  mezcles sin confirmar primero.
