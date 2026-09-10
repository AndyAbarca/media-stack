#!/usr/bin/env bash
# Configura las "Base URL" / "UrlBase" de cada app para que matcheen
# los subpaths de Caddy (/sonarr, /radarr, /bazarr, etc.). Jellyfin NO
# pasa por acá: corre en su propio puerto (:8096), sin BaseUrl.
#
# Después de esto, cada app responde SOLO en su subpath:
#   http://<IP>/sonarr     → Sonarr (con UrlBase=/sonarr)
#   etc.
#
# Idempotente: si el valor ya está seteado, no hace nada.
#
# Apps que NO soportan subpath fácilmente y quedan con un warning:
#   - jellyseerr: depende de la versión, intentamos APP_BASE_URL como env var
#   - wizarr: se configura via web UI en el primer login
#
# Uso:
#   ./scripts/configure-base-urls.sh
#
# Requiere:
#   - El stack ya levantado (docker compose up -d)
#   - Los containers ya booteados al menos una vez (para que exista config.xml)

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { printf "${GREEN}[+]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
err()  { printf "${RED}[x]${NC} %s\n" "$*"; }

wait_for_config() {
  local container="$1"
  local path="$2"
  local timeout="${3:-120}"
  log "Esperando que ${container} genere ${path}..."
  for i in $(seq 1 "$timeout"); do
    if docker exec "$container" test -f "$path" 2>/dev/null; then
      log "  ${container}: ${path} listo (${i}s)"
      return 0
    fi
    sleep 1
  done
  err "  ${container}: TIMEOUT esperando ${path}"
  return 1
}

set_urlbase_xml() {
  # Apps *arr: edita <UrlBase></UrlBase> en config.xml
  local container="$1"
  local path="$2"
  local value="$3"
  docker exec "$container" bash -c "
    if grep -q '<UrlBase></UrlBase>' '${path}'; then
      sed -i 's|<UrlBase></UrlBase>|<UrlBase>${value}</UrlBase>|' '${path}'
      echo '  UrlBase actualizado a ${value}'
    elif grep -q '<UrlBase>${value}</UrlBase>' '${path}'; then
      echo '  UrlBase ya estaba en ${value} (no-op)'
    else
      echo '  WARN: no se encontró <UrlBase></UrlBase> en ${path}; revisar manualmente'
    fi
  "
}

set_urlbase_bazarr() {
  # Bazarr: usa config.yaml; la key "base_url" vive anidada dentro de la
  # sección "general:" (no top-level). El archivo tiene otras cuatro
  # apariciones de "base_url:" en otras secciones (radarr/sonarr/subliminal)
  # que no hay que tocar, por eso el grep/sed se restringen al rango de
  # líneas entre "general:" y el próximo bloque de nivel superior. Se usa
  # un heredoc (en vez del patrón bash -c "..." de las otras funciones)
  # porque el valor vacío real es el string YAML `''`, y así evitamos
  # pelear con escapes de comillas anidadas.
  local container="$1"
  local path="$2"
  local value="$3"
  docker exec -i "$container" bash -s -- "$path" "$value" <<'BAZARR_SCRIPT'
    path="$1"
    value="$2"
    general_block=$(sed -n "/^general:/,/^[^[:space:]]/p" "$path")
    if echo "$general_block" | grep -qE "^[[:space:]]*base_url: ${value}\$"; then
      echo "  Bazarr general.base_url ya estaba en ${value} (no-op)"
    elif echo "$general_block" | grep -qE "^[[:space:]]*base_url: ''\$"; then
      sed -i "/^general:/,/^[^[:space:]]/ s|^\([[:space:]]*base_url:\) ''\$|\1 ${value}|" "$path"
      echo "  Bazarr general.base_url actualizado a ${value}"
    else
      echo "  WARN: no se encontro general.base_url vacio en ${path}; revisar manualmente"
    fi
BAZARR_SCRIPT
}

set_legacy_auth_jellyfin() {
  # Jellyfin 12.x: EnableLegacyAuthorization viene en false por defecto en
  # system.xml, lo cual rompe la autenticación de apps de terceros (Wizarr,
  # Jellyseerr) contra la API con errores 404/401. Forzar a true lo resuelve.
  local container="$1"
  local path="$2"
  docker exec "$container" bash -c "
    if grep -q '<EnableLegacyAuthorization>false</EnableLegacyAuthorization>' '${path}'; then
      sed -i 's|<EnableLegacyAuthorization>false</EnableLegacyAuthorization>|<EnableLegacyAuthorization>true</EnableLegacyAuthorization>|' '${path}'
      echo '  EnableLegacyAuthorization actualizado a true (fix bug Jellyfin 12.x)'
    elif grep -q '<EnableLegacyAuthorization>true</EnableLegacyAuthorization>' '${path}'; then
      echo '  EnableLegacyAuthorization ya estaba en true (no-op)'
    else
      echo '  WARN: no se encontró <EnableLegacyAuthorization> en ${path}; revisar manualmente'
    fi
  "
}

restart_apps() {
  log "Reiniciando apps para que apliquen los nuevos URL Base..."
  for c in "$@"; do
    if docker ps -q -f name="^/${c}\$" >/dev/null 2>&1; then
      docker restart "$c" >/dev/null
      log "  ${c}: reiniciado"
    else
      warn "  ${c}: no estaba corriendo, skip"
    fi
  done
}

main() {
  log "============================================"
  log "  Configurando URL Base del *arr stack"
  log "============================================"
  echo

  # *arr apps (config.xml con <UrlBase></UrlBase>)
  wait_for_config sonarr /config/config.xml 180
  set_urlbase_xml sonarr /config/config.xml /sonarr
  echo

  wait_for_config radarr /config/config.xml 180
  set_urlbase_xml radarr /config/config.xml /radarr
  echo

  # Bazarr (config.yaml en /config/config/config.yaml por la imagen LSIO)
  wait_for_config bazarr /config/config/config.yaml 180
  set_urlbase_bazarr bazarr /config/config/config.yaml /bazarr
  echo

  # Jellyseerr: depende de la versión. Si soporta APP_BASE_URL como env var,
  # hay que setearlo en el compose antes del primer boot. Si no, queda en root.
  warn "Jellyseerr: verificar si soporta APP_BASE_URL como env var. Si no, queda accesible solo en root"
  echo

  # Wizarr: usa SQLite, no se puede editar fácil
  warn "Wizarr: configurar URL base manualmente en el primer login (Settings → General → Application URL)"
  echo

  # Jellyfin: fix bug conocido 12.x — EnableLegacyAuthorization=false rompe
  # la autenticación de apps de terceros (Wizarr, Jellyseerr) contra la API
  wait_for_config jellyfin /config/system.xml 180
  set_legacy_auth_jellyfin jellyfin /config/system.xml
  echo

  # Restart apps para que apliquen cambios
  restart_apps sonarr radarr bazarr jellyfin

  echo
  log "============================================"
  log "  Listo. URLs de acceso:"
  log "    http://<IP>/sonarr      → TV shows"
  log "    http://<IP>/radarr      → Movies"
  log "    http://<IP>/bazarr      → Subtitles"
  log "    http://<IP>/jellyseerr  → Requests (si soporta subpath)"
  log "    http://<IP>/wizarr      → Invitations (configurar manualmente)"
  log "    http://<IP>:8080        → qBittorrent (puerto dedicado)"
  log "    http://<IP>:9117        → Jackett (puerto dedicado)"
  log "    http://<IP>:8191        → FlareSolverr (puerto dedicado)"
  log "    http://<IP>:8096        → Jellyfin (puerto dedicado)"
  log "============================================"
}

main "$@"
