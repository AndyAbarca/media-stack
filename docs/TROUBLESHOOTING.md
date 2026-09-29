# Troubleshooting — registro de incidentes

Registro de problemas reales que tuvo este stack, cómo se diagnosticaron y cómo
se resolvieron. La idea es no volver a debuggear lo mismo dos veces.

Formato de cada entrada: **síntoma → causa → evidencia → solución →
prevención**. Las entradas más nuevas van arriba.

Comandos útiles para diagnosticar (las API keys se leen de `config.xml`, nunca
se imprimen):

```bash
# API de Sonarr/Radarr desde adentro del container (con UrlBase)
key=$(grep -oP '(?<=<ApiKey>)[^<]+' config/sonarr/config.xml)
docker exec sonarr curl -s -H "X-Api-Key: $key" http://localhost:8989/sonarr/api/v3/health
docker exec sonarr curl -s -H "X-Api-Key: $key" "http://localhost:8989/sonarr/api/v3/queue?pageSize=50"

# Settings de rdtclient (lectura, sin tocar la DB)
python3 -c "import sqlite3;c=sqlite3.connect('file:config/rdtclient/rdtclient.db?mode=ro',uri=True);print(c.execute(\"select SettingId,Value from Settings where SettingId like 'DownloadClient:%Path%'\").fetchall())"

# ¿Un archivo es hardlink? (links=2 => comparte bloques con otro path)
stat -c '%i links=%h %n' <archivo>
```

---

## 2026-09-28 — Bazarr no baja subtítulos

**Síntoma.** Batman Knightfall (y todo lo demás) sin subtítulos externos. La UI
de Bazarr mostraba "subtítulos", pero en disco no había ningún `.srt`.

**Causa (tres problemas encadenados).**
1. **Ninguna película ni serie tenía perfil de idioma** (`profileId = None`) y
   el perfil por defecto estaba activado pero vacío. Bazarr no busca nada para
   contenido sin perfil. El historial tenía 0 descargas.
2. **OpenSubtitles.com rechaza el login**: `AuthenticationError 'Login
   failed'`. Bazarr lo bloquea (throttle) por 12 horas.
3. Los proveedores "sin cuenta" que se probaron no sirven desde acá:
   - Podnapisi: dominio muerto (NXDOMAIN, incluso en el DNS de Cloudflare).
   - Subdivx: 403 por Cloudflare, y Bazarr 1.6.2 ni siquiera trae el módulo.

Lo que se veía en la UI eran pistas **embebidas** dentro de los `.mkv`, no
descargas.

**Solución aplicada.**
- Perfil 1 como default para series y películas, y asignado a las 6 películas y
  3 series (API: `POST /api/movies` y `/api/series` con `profileid`).
- Proveedores: `opensubtitlescom`, `embeddedsubtitles`, `yifysubtitles`
  (películas) y `gestdown` (series, espejo de Addic7ed).
- Primer subtítulo real bajado: `Scary Movie 3 … .en.srt` (YIFY).

**Causa del login fallido (resuelta).** La cuenta era de **opensubtitles.org**,
no de **opensubtitles.com**. Son sitios distintos y Bazarr usa `.com`. Se creó
una cuenta en `.com` y el proveedor quedó en `Good`. Para probar credenciales:
entrar con los mismos datos a https://www.opensubtitles.com/en/login. En Bazarr
va el **usuario**, no el email. Después de corregirlas: System → Providers →
"Reset", para no esperar las 12 horas de throttle.

**Pendiente (opcional).** El perfil se llama "English" pero tiene `en` + `ea`
(español latino). YIFY casi no tiene "latino": ofrece "Spanish" (`es`). Si se
acepta castellano, sumar `es` al perfil.

**Verificación.**
`find data/media -iname '*.srt'` tiene que listar archivos. En Bazarr, la
pestaña **History** tiene que mostrar descargas con proveedor.

---

## 2026-09-27 — Scary Movie (2000) no reproduce en Jellyfin Web

**Síntoma.** La película queda cargando para siempre en el navegador. Otras
películas andan bien. Desactivar subtítulos y recargar la página no cambia nada.

**Causa.** Incompatibilidad del **cliente web** con este archivo en particular:
un REMUX 1080p de ~21 Mbps con audio DTS-HD MA y 6 pistas de subtítulos PGS.
No es un problema del servidor ni de la red. La causa raíz exacta dentro del
navegador no se identificó.

**Evidencia.**
- `log_20260927.log`: en ningún intento aparece `started playback` para esta
  película. Con Scary Movie 2, desde el mismo navegador, sí aparece.
- `FFmpeg.Transcode-*` (con subtítulos): ffmpeg quemaba los PGS (`overlay` +
  `libx264`) a **3.5x** la velocidad real. El servidor iba sobrado.
- `FFmpeg.DirectStream-*` (sin subtítulos): el video se copiaba tal cual
  (`-codec:v:0 copy`), solo el DTS se pasaba a AAC, a **11–17x**.
- En los dos casos Jellyfin mata el job al minuto ("kill timer") porque el
  cliente deja de pedir segmentos.
- **Jellyfin Media Player (app de escritorio) lo reproduce perfecto.** Descarta
  que sea un tema de ancho de banda o del archivo.

**Solución.** Usar la app de escritorio (jellyfin.org/downloads → Clients →
Windows). Reproduce DTS y PGS de forma nativa con mpv, sin que el servidor
convierta nada.

**Prevención.** Evitar REMUX en la biblioteca. Además de pesar mucho, fuerzan
transcode o direct-stream en clientes web y TVs. **Aplicado (2026-09-28):**
los perfiles "Any" y "HD-1080p" de Radarr llegan hasta Bluray-1080p, sin
Remux-1080p ni 2160p. El archivo actual de Scary Movie (REMUX) queda como está:
Radarr no lo reemplaza solo, porque un Bluray-1080p no cuenta como "mejora".

---

## 2026-09-27 — Radarr eligió REMUX/2160p gigantes y llenó el disco

**Síntoma.** Después de "Search all missing", Radarr mandó a bajar Supergirl
REMUX 2160p (63 GB) y Batman Knightfall REMUX 2160p (52 GB) con 47 GB libres.

**Causa.**
1. Las películas estaban en el perfil **"Any"**, que permite hasta Remux-2160p y
   BR-DISK. Radarr siempre elige la mejor calidad permitida.
2. Real-Debrid entrega algunos releases como **`.rar`**. rdtclient baja el
   `.rar` y después lo descomprime, así que ocupa **el doble** durante un rato.
   Supergirl hubiera necesitado ~126 GB.
3. El chequeo de espacio de Sonarr/Radarr ("Minimum Free Space") es **por
   release**: no suma las descargas concurrentes.

**Solución.**
- Se canceló Supergirl REMUX y se re-agregó la película con perfil
  **HD-1080p** (id 4). Se bajó `Supergirl (2026) MULTI ESP-ENG … Bluray 1080p`
  (6.1 GB).
- Batman Knightfall 1/2/3 pasaron a HD-1080p. Se bajó la Parte 1 como WEBRip
  1080p (1.6 GB). Las partes 2 y 3 todavía no tienen release.
- Cuidado: **cancelar desde Radarr puede borrar la película de la biblioteca**
  (pasó con Supergirl: `MovieService|Deleted movie`). Revisar el diálogo de
  borrado antes de confirmar.

**Prevención.**
- Límite duro: ext4 reserva 5% del disco para root y los containers corren como
  UID 1000, así que nunca pueden llenar más de ~95% de `/data`. Se evaluó
  moverlo a 97% (`sudo tune2fs -m 3 /dev/sdb1`). **Decisión: dejarlo en 95%.**
- Límites blandos (aplicados):
  - rdtclient `MinimumFreeSpaceGB = 2`: pausa las descargas.
  - Sonarr y Radarr "Minimum Free Space" = **2048 MB**: rechazan un release si
    importarlo dejaría menos de eso libre (chequeo por release, no suma
    concurrentes).
- Perfiles de Radarr (aplicado 2026-09-28): "Any" y "HD-1080p" topean en
  Bluray-1080p, sin Remux-1080p, 2160p ni BR-DISK.
- Pendiente opcional: CF de TRaSH "Upscaled" con puntaje negativo. Ya no es
  crítico porque los upscales son 2160p y el tope es 1080p.
- Hueco conocido, **no corregido por decisión (2026-09-28)**: en Radarr
  "Bluray-1080p" no tiene tamaño máximo (Settings → Quality), así que algún
  encode puede pesar 25–40 GB. El resto de las calidades 1080p topean en
  100 MB/min (~11.7 GB para 2 h). Se acepta el riesgo hasta migrar al hardware
  nuevo: `/data` es un disco aparte del sistema, así que llenarlo no congela la
  VM, y lo peor que pasa es que una descarga falle y haya que borrarla.
- Seerr pide todo con el perfil "Any" (id 1), tanto en Radarr como en Sonarr.

---

## 2026-09-27 — rdtclient: descargas fallan con SSL / timeout

**Síntoma.** Algunas descargas quedan en "warning" en Sonarr/Radarr con
`qBittorrent is reporting an error`. En Reacher S04 fallaron 2 de 8 archivos.

**Causa.** Configuración agresiva del downloader interno (Bezzad) de rdtclient:
`ParallelCount = 8` (**conexiones por descarga**, no descargas simultáneas) y
`Timeout = 5000` ms. Con 5 descargas activas eran ~40 conexiones a Real-Debrid,
y los handshakes TLS no terminaban en 5 s.

**Evidencia.** `config/rdtclient/rdtclient.log`: solo aparecen
`The SSL connection could not be established` y `HttpClient.Timeout of 5
seconds elapsing`. La API de Real-Debrid respondía 200 desde el container.

**Solución.** En la UI de rdtclient (Settings → Download Client):
`Connection Timeout = 30000`, `Parallel connections per download = 2`,
`Minimum free disk space (GB) = 2`. Después, Retry de las descargas fallidas
desde la UI.

**Prevención.** No subir las conexiones paralelas. Si vuelve a pasar, mirar
primero `rdtclient.log`.

---

## 2026-09-27 — Real-Debrid rechaza releases: "Infringing file"

**Síntoma.** El ítem aparece en la cola con 0 bytes y
`qBittorrent is reporting an error`. En rdtclient:
`Could not add to provider: Infringing file`.

**Causa.** Real-Debrid bloquea ciertos torrents puntuales por DMCA. No es un
problema de configuración.

**Solución.** Radarr/Sonarr **no** lo tratan como fallido y no buscan otro
release solos. Hay que sacarlo de la cola con *Remove from client* +
*Blocklist* y elegir otro release (Interactive Search). En general, otro
release de la misma película sí funciona.

Ojo: si la película se borra y se vuelve a agregar, el ítem fantasma de
rdtclient puede asociarse a la película nueva. Radarr entonces rechaza todo con
`Quality for release in queue already meets cutoff`. Se soluciona igual: sacar
ese ítem de la cola.

---

## 2026-09-27 — Descargas de rdtclient "completed" pero nunca se importan

**Síntoma.** Breaking Bad T2 (24 GB) llevaba días descargada y seguía en la
cola de Sonarr como `completed / importing`. Health check de Sonarr y Radarr:
`Remote download client qBittorrent_rdtc places downloads in
torrents/torrents/tv-sonarr but this is not a valid alpine path`.

**Causa.** En rdtclient, **Mapped path** estaba en `torrents` (relativo). Es el
path que rdtclient les reporta a los *arr como `save_path`. Un intento anterior
de corregirlo no se había guardado.

**Evidencia.** `rdtclient.db` → `DownloadClient:MappedPath = torrents`. Log de
Sonarr: `value [torrents/tv-sonarr/…] is not a valid *nix path. paths must
start with /`.

**Solución.**
1. rdtclient → Settings → Download Client → **Mapped path = `/data/torrents`**
   (igual que Download path, porque rdtclient y los *arr montan el mismo
   `/data/torrents`). Verificar en la DB que haya quedado guardado.
2. `docker compose restart sonarr`. Las descargas que ya estaban trabadas en
   estado `Importing` no se reintentan solas: Sonarr guarda ese estado en
   memoria, y el restart lo reconstruye con el path nuevo. El import corrió
   ~20 s después.
3. Radarr seguía marcando que `/data/torrents/radarr` no existía. rdtclient
   crea la carpeta de la categoría recién en la primera descarga, así que se
   creó a mano (vacía).

**Nota sobre hardlinks.** Las importaciones desde rdtclient son **moves**, no
hardlinks (`links=1`). Es lo esperado: rdtclient reporta el torrent como
terminado y sin seedear (el seeding lo hace Real-Debrid), así que Sonarr mueve
el archivo. Como es el mismo filesystem, el move es un rename atómico (instantáneo,
sin duplicar espacio). Las descargas de qBittorrent sí se importan como hardlink.

---

## 2026-09-27 — Reacher / películas nunca se buscaron

**Síntoma.** "Sonarr/Radarr no encuentran nada". Reacher 0/8 episodios.

**Causa.** Las series y películas se agregaron **sin** tildar "Start search for
missing". Sonarr/Radarr no buscan hacia atrás por su cuenta: el RSS solo ve
releases **nuevos**. Una búsqueda manual devolvía 16 releases aprobados para
Reacher y 15 para Scary Movie 2.

**Solución.** Series → *Search Monitored* / Movies → *Search Missing*
(o `SeriesSearch` / `MissingMoviesSearch` por API).

**Prevención.** Al agregar contenido, dejar tildado "Start search for missing".

---

## 2026-09-27 — Radarr rechaza releases en español

**Síntoma.** Rechazos `Original Language (English) is wanted, but found
Spanish`.

**Causa.** Los perfiles de Radarr tenían Language = **Original**. Sonarr v4 no
tiene este campo, por eso ahí no pasaba.

**Solución.** Perfil "Any" (id 1): Language = **Any**. Se sumó el custom format
**"MULTi / Dual Audio"** (id 1, basado en el CF "MULTi" de TRaSH Guides) con
+100, para preferir releases con varios audios. Minimum CF score = 0, así que
funciona como preferencia, no como filtro.

**Pendiente (opcional).** El CF solo tiene puntaje en el perfil "Any". En
"HD-1080p" (Supergirl, Batman Knightfall) puntúa 0.

---

## 2026-09-27 — Espacio "fantasma" en `data/torrents`

**Síntoma.** `data/torrents` pesaba mucho más que lo que había en descarga.

**Causa.**
- Copias duplicadas (`links=1`) de Linternas 101–104 y Scary Movie 3, que ya
  estaban en `media/`. Hipótesis: se importaron el 10/9, antes de que `./data`
  se moviera a `/data/Streaming` (12/9). Copiar un árbol sin preservar
  hardlinks los convierte en copias independientes.
- Contenido huérfano (Avatar TLA) que no estaba ni en `media/` ni en Sonarr.
- Descargas en curso de rdtclient: el archivo se pre-reserva con el tamaño
  final (sparse), así que `ls` muestra más de lo que realmente ocupa (`du`).

**Solución.** Se borraron los torrents desde la UI de qBittorrent con
"Also delete files". **No borrar a mano** archivos que qBittorrent sigue
seedeando: los marca como "missing files".

**Prevención.** Configurar un límite de ratio/tiempo de seeding en qBittorrent
(Options → BitTorrent → Seeding Limits). Sonarr/Radarr tienen "Remove
Completed" activado, así que limpian solos cuando se alcanza el límite
(recomendación de TRaSH Guides).

---

## Problemas conocidos abiertos

- **dontorrent** (Jackett) caído desde hace días: `todotorrents.org` devuelve
  Error 522.
- **1337x** falla porque FlareSolverr no está configurado en Jackett
  (`FlareSolverrUrl` vacío). Se puede arreglar en Jackett (Dashboard →
  FlareSolverr API URL = `http://flaresolverr:8191`) sin migrar a Prowlarr.
- Categorías mal mapeadas en los indexers de Jackett (TPB con categorías de anime
  en Sonarr, etc.) y URLs apuntando a la IP del host en vez de `jackett:9117`.
- La migración a Prowlarr resolvería los dos puntos anteriores, pero **está en
  pausa por decisión (2026-09-28)**. Ver `docs/prowlarr-migration-plan.md`.
- "Allowed Hosts" sin configurar en Sonarr/Radarr (warning).
