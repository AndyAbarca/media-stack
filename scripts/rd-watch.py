#!/usr/bin/env python3
"""
rd-watch: detecta descargas de rdt-client (Real-Debrid) trabadas en Radarr y
las pasa a qBittorrent (vía gluetun/VPN).

Por qué existe: rdt-client imita a qBittorrent pero no traduce bien los
estados de RD. Un torrent en `magnet_conversion` sin seeders le llega a
Radarr como un `downloading` normal, y un rechazo "Infringing file" como un
warning genérico. Radarr nunca los da por fallidos, así que no busca otro
release (ver docs/TROUBLESHOOTING.md, 2026-11-05).

Modos:
  status          Tabla de estado. Solo lectura: no escribe nada.
  fix             Actualiza los strikes y, para cada película que lo
                  necesite, pide confirmación y aplica el fallback.
  fix --yes       Igual, sin confirmar (para cron).

Reglas:
  - Un release de RD está MUERTO si no avanza durante MAX_STRIKES chequeos
    de `fix` seguidos (separados por al menos MIN_STRIKE_INTERVAL). Si avanza
    aunque sea un poco, los strikes vuelven a 0.
  - Una película pasa a qBittorrent si tiene un release muerto, o si todos
    sus items en rdt-client son rechazos de RD (Infringing, etc.) con más de
    REJECT_GRACE de antigüedad. Si algún release avanza, no se toca nada.
  - Fallback: quita de rdt-client todos los items de la película (blocklist
    solo de los grabs registrados, skipRedownload para que Radarr no vuelva
    a RD) y bajo por qBittorrent el release aprobado con más seeders.

Lee las API keys de config/<app>/config.xml (nunca las imprime) y la DB de
rdt-client en modo read-only. Estado y log en config/rd-watch/.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- Parámetros ------------------------------------------------------------
MAX_STRIKES = 3                 # chequeos seguidos sin avance → muerto
MIN_STRIKE_INTERVAL = 8 * 60    # seg; evita sumar strikes corriendo fix seguido
REJECT_GRACE = 5 * 60           # seg; margen para que Radarr termine de probar releases
MIN_SEEDERS = 5                 # no hacer fallback a un release casi muerto
STUCK_STATES = {"magnet_conversion", "queued", "waiting_files_selection", "magnet_error"}

ROOT = Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    # Respeta CONFIG_DIR de .env (como el compose); default ./config
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^CONFIG_DIR=(.+)$", env.read_text(), re.M)
        if m:
            p = Path(m.group(1).strip().strip('"'))
            return p if p.is_absolute() else ROOT / p
    return ROOT / "config"


CONFIG = config_dir()
STATE_DIR = CONFIG / "rd-watch"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "rd-watch.log"
RDT_DB = CONFIG / "rdtclient" / "rdtclient.db"
RADARR_URL = "http://localhost/radarr/api/v3"   # vía Caddy (:80)


def api_key(app: str) -> str:
    xml = (CONFIG / app / "config.xml").read_text()
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", xml).group(1)


class Arr:
    def __init__(self, base: str, key: str):
        self.base, self.key = base, key

    def call(self, method: str, path: str, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-Api-Key", self.key)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None


def now() -> float:
    return time.time()


def parse_ts(s: str) -> float:
    # rdt-client guarda "2026-11-05 22:40:11.9507907+00:00" (7 decimales)
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    return datetime.fromisoformat(s).timestamp()


def log(msg: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")
    print(line)


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)   # escritura atómica


def rdt_rows() -> dict:
    """hash → fila de rdt-client (read-only)."""
    c = sqlite3.connect(f"file:{RDT_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "select Hash, RdName, RdStatusRaw, RdSeeders, RdProgress, Error, Added, Category "
        "from Torrents"
    ).fetchall()
    c.close()
    return {r["Hash"].lower(): dict(r) for r in rows}


def download_clients(radarr: Arr):
    """(nombre del cliente rdt-client, id del qBittorrent real)."""
    rdt_name, qbit_id = None, None
    for c in radarr.call("GET", "/downloadclient"):
        host = next((f.get("value") for f in c["fields"] if f["name"] == "host"), "")
        if host == "rdtclient":
            rdt_name = c["name"]
        elif host == "gluetun" and c["enable"]:
            qbit_id = c["id"]
    if not rdt_name or not qbit_id:
        sys.exit("No encontré los download clients (host rdtclient / gluetun) en Radarr.")
    return rdt_name, qbit_id


def classify(item: dict, row: dict | None, st: dict, t: float, update: bool) -> str:
    """Devuelve: ok | avanza | sospechoso | muerto | rechazado | desconocido.
    Si update=True, actualiza strikes en `st` (dict del estado de ese hash)."""
    if row is None:
        return "desconocido"
    if row["Error"]:
        return "rechazado"
    status = row["RdStatusRaw"] or ""
    prog = row["RdProgress"] or 0
    seeders = row["RdSeeders"] or 0
    last = st.get("progress")

    if status in ("downloaded",) or prog >= 100:
        st.update(strikes=0, progress=prog)
        return "ok"

    if last is not None:
        stuck_now = prog <= last
    else:
        stuck_now = status in STUCK_STATES and seeders == 0

    if update and t - st.get("last_check", 0) >= MIN_STRIKE_INTERVAL:
        st["strikes"] = st.get("strikes", 0) + 1 if stuck_now else 0
        st["progress"] = prog
        st["last_check"] = t

    strikes = st.get("strikes", 0)
    if strikes >= MAX_STRIKES:
        return "muerto"
    if stuck_now:
        return "sospechoso"
    return "avanza"


def best_release(radarr: Arr, movie_id: int):
    rels = radarr.call("GET", f"/release?movieId={movie_id}", timeout=300)
    ok = [r for r in rels if not r["rejected"] and r.get("protocol") == "torrent"
          and (r.get("seeders") or 0) >= MIN_SEEDERS]
    return max(ok, key=lambda r: r.get("seeders") or 0) if ok else None


def fallback(radarr: Arr, qbit_id: int, movie: str, movie_id: int, items: list):
    grabbed = [i["id"] for i, verdict in items if verdict != "rechazado"]
    rejected = [i["id"] for i, verdict in items if verdict == "rechazado"]
    # Blocklist solo de grabs registrados: a los rechazos de RD Radarr no los
    # bloquea (nunca quedaron como grab), solo se limpian.
    for ids, bl in ((grabbed, "true"), (rejected, "false")):
        if ids:
            radarr.call("DELETE", f"/queue/bulk?removeFromClient=true&blocklist={bl}"
                        "&skipRedownload=true", {"ids": ids})
    log(f"[{movie}] quitados de rdt-client: {len(grabbed)} con blocklist, "
        f"{len(rejected)} rechazos limpiados")

    rel = best_release(radarr, movie_id)
    if not rel:
        log(f"[{movie}] ⚠ no hay release aprobado con >= {MIN_SEEDERS} seeders; "
            "queda sin descarga, revisar a mano")
        return
    radarr.call("POST", "/release", {"guid": rel["guid"], "indexerId": rel["indexerId"],
                                     "downloadClientId": qbit_id})
    log(f"[{movie}] → qBittorrent: {rel['title']} ({rel['seeders']} seeders, "
        f"{rel['size'] / 2**30:.1f} GB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["status", "fix"])
    ap.add_argument("--yes", action="store_true", help="no pedir confirmación (cron)")
    args = ap.parse_args()
    fix = args.mode == "fix"

    radarr = Arr(RADARR_URL, api_key("radarr"))
    rdt_name, qbit_id = download_clients(radarr)
    rows = rdt_rows()
    state = load_state()
    t = now()

    queue = radarr.call("GET", "/queue?pageSize=500&includeMovie=true")["records"]
    in_queue = {q["downloadId"].lower() for q in queue if q.get("downloadId")}

    # Agrupar items de rdt-client por película
    movies: dict[int, dict] = {}
    for q in queue:
        if q["downloadClient"] != rdt_name:
            continue
        h = q["downloadId"].lower()
        row = rows.get(h)
        st = state.setdefault(h, {})
        verdict = classify(q, row, st, t, update=fix)
        m = movies.setdefault(q["movieId"], {"title": q["movie"]["title"], "items": [],
                                              "qbit": False})
        m["items"].append((q, verdict))
        age = t - parse_ts(row["Added"]) if row else 0
        print(f"{verdict:<11} {(row or {}).get('RdStatusRaw') or '-':<18} "
              f"seed {(row or {}).get('RdSeeders') if row else '-'!s:>4}  "
              f"prog {(row or {}).get('RdProgress') if row else '-'!s:>4}%  "
              f"strikes {st.get('strikes', 0)}/{MAX_STRIKES}  "
              f"edad {int(age // 60):>4}m  {q['title'][:60]}")

    for q in queue:   # ¿la película ya tiene una descarga por qBittorrent?
        if q["downloadClient"] != rdt_name and q["movieId"] in movies:
            movies[q["movieId"]]["qbit"] = True

    # Filas de rdt-client que Radarr ya no muestra (rechazos sueltos)
    orphans = [r for h, r in rows.items() if h not in in_queue and r["Category"] == "radarr"
               and r["Error"]]
    if orphans:
        print(f"\n{len(orphans)} rechazo(s) en rdt-client fuera de la cola de Radarr "
              "(borrar desde la UI de rdt-client, :6500):")
        for r in orphans:
            print(f"   - {r['RdName']}  ({r['Error']})")

    if not movies:
        print("Sin descargas de rdt-client en la cola de Radarr.")

    # Decidir por película
    for movie_id, m in movies.items():
        verdicts = [v for _, v in m["items"]]
        rejected_old = all(
            v == "rechazado" and t - parse_ts(rows[i["downloadId"].lower()]["Added"]) >= REJECT_GRACE
            for i, v in m["items"]
        )
        needs = "muerto" in verdicts and not ({"avanza", "ok"} & set(verdicts)) or rejected_old
        if not needs:
            continue
        reason = "release muerto en RD" if "muerto" in verdicts else "todos rechazados por RD"
        if m["qbit"]:
            print(f"\n[{m['title']}] {reason}, pero ya tiene descarga por qBittorrent: no toco nada.")
            continue
        print(f"\n[{m['title']}] {reason} → pasar a qBittorrent")
        if not fix:
            continue
        if not args.yes and input("  ¿Aplicar? [s/N] ").strip().lower() != "s":
            print("  salteado")
            continue
        try:
            fallback(radarr, qbit_id, m["title"], movie_id, m["items"])
        except urllib.error.URLError as e:
            log(f"[{m['title']}] ✗ error de API: {e}")

    if fix:
        # Olvidar hashes que ya no están en la cola
        save_state({h: s for h, s in state.items() if h in in_queue})


if __name__ == "__main__":
    main()
