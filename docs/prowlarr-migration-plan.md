# Media stack: diagnosis + Jackett → Prowlarr migration

## Status (2026-09-28)
Phase 0 (backups) and Phase A (blockers) **done**; details and fixes in `docs/TROUBLESHOOTING.md`.
Next: Phase B (add Prowlarr) — waiting for go-ahead.

## ▶ EXECUTION SCOPE (approved 2026-09-27): Phase 0 + Phase A ONLY, then STOP and report
- First save this full plan as `docs/prowlarr-migration-plan.md` (for resuming at Phase B later;
  not the final `docs/prowlarr-migration.md`).
- Phase 0 backups as written.
- Step 6: **the user** saves Mapped path=`/data/torrents` in the rdtclient UI. I verify it
  persisted in rdtclient.db (read-only sqlite). If it didn't persist, I investigate why
  (e.g. UI validation error, WAL not checkpointed, container writing to a different DB path,
  save button not hit) and report **before** any retry.
- Step 7: Language=Any on Radarr profile 1 **plus** a TRaSH-based custom format that prefers
  multi-audio releases (MULTi/DUAL/Dual-Audio title regex, based on the TRaSH MULTi CF JSON),
  with a positive score in profile 1. **Show the CF JSON + score to the user before applying.**
- Step 8: Reacher SeriesSearch + Radarr MissingMoviesSearch, after steps 6 and 7.
- Report: BB S2 imported **via hardlink** (same inode / link count 2 for source and
  `/data/media/series/Breaking Bad/...`), Sonarr + Radarr health clear of RemotePathMapping /
  DownloadClientRootFolder errors, Reacher search results + grabs (+ import status).
- Do NOT start Phase B+ until the user confirms.

## Context
Sonarr/Radarr "find few results and nothing downloads". The read-only diagnosis below found that
the stated premise is partly wrong: things *did* download (Lanterns 5/6 eps, Scary Movie 3,
all via qBittorrent), and interactive searches return plenty of approved releases. The real
blockers are **one rdtclient setting, missing searches, and indexer hygiene**, not the
indexer manager. The Prowlarr migration is still worth doing (central indexer management,
Full Sync, tag-based FlareSolverr), but it will not by itself fix downloads. So the plan fixes
the blockers first (Phase A), then migrates (Phases B–F).

Note: CLAUDE.md records "Jackett instead of Prowlarr (decision made)". This request reverses
that decision explicitly; Phase F updates CLAUDE.md to match.

---

## Part 1: Diagnosis findings (done, read-only)

### Facts (with evidence)
| # | Finding | Evidence |
|---|---|---|
| F1 | **rdtclient reports a relative path to the *arr apps.** Its DB has `DownloadClient:DownloadPath=/data/torrents` but `DownloadClient:MappedPath=torrents`, so the earlier "fix" to Mapped path never got saved. | rdtclient.db Settings; Sonarr health: `Remote download client qBittorrent_rdtc places downloads in torrents/torrents/tv-sonarr but this is not a valid alpine path`; log: `value [torrents/tv-sonarr/Breaking Bad - Temporada 2 …/] is not a valid *nix path. paths must start with /` |
| F2 | Because of F1, **Breaking Bad S2 is downloaded but stuck.** It's 24 GB in `data/torrents/tv-sonarr/…`, rdtclient status `downloaded` since 2026‑09‑22, and Sonarr shows 13 queue rows as `completed / importing` with `outputPath=torrents/tv-sonarr/…` (relative). | `/api/v3/queue`, `du`, rdtclient Torrents table |
| F3 | **Reacher was never searched.** The logs only show refresh and disk-scan events for it, with no EpisodeSearch. An interactive search now returns **16 approved** releases (e.g. `Reacher S01 1080p BluRay x265`, 229 seeders). Same for Scary Movie 2: **15 approved** (TPB, up to 56 seeders). | Sonarr/Radarr logs; `/api/v3/release` |
| F4 | **Radarr profiles have Language = "Original".** Spanish releases of English-language films are rejected (`Original Language (English) is wanted, but found Spanish`). Sonarr v4 has no profile language, so it doesn't have this issue. | `/api/v3/qualityprofile`, release rejections |
| F5 | **9 Jackett indexers in each *arr app, not 4:** aniRena, apachetorrent, bangumi, bigfangroup, divxtotal, dmhy, dontorrent, nyaasi, thepiratebay. Opensharing and 1337x are configured in Jackett but not in the *arr apps. | `/api/v3/indexer`, `config/jackett/Jackett/Indexers/` |
| F6 | **Category mapping is wrong.** Sonarr's TPB has 34 *anime* categories (movies 2000–2060, etc.) and Nyaa has standard TV cats 5030/5040. Radarr's Nyaa and TPB have categories Radarr warns about (`does not support the following categories`). | `/api/v3/indexer`, Radarr log |
| F7 | Indexer URLs use `http://192.168.68.57:9117` (host IP hairpin) instead of `http://jackett:9117`, which contradicts `docs/INDEXERS.md`. It works (302), but depends on the host IP and the published port. | `/api/v3/indexer` |
| F8 | **FlareSolverr is running but not wired in.** Jackett has `FlareSolverrUrl: null`, so 1337x fails with `Challenge detected but FlareSolverr is not configured`. | ServerConfig.json, Jackett logs, `flaresolverr:8191` → "ready" |
| F9 | Per-indexer test for "breaking bad": TPB 100 results, divxtotal 63, bigfangroup 18, apachetorrent 0, nyaasi 0 (expected, it's anime), **dontorrent 400 (upstream `todotorrents.org` Error 522, site down)**. | curl to Jackett torznab |
| F10 | Download clients are reachable from Sonarr. `rdtclient:6500` returns 200, and `gluetun:8090` returns 403 (qBittorrent's normal unauthenticated answer). Priorities are rdt=1, qBit=2 in both apps. No Remote Path Mappings, no custom formats. | `/api/v3/downloadclient`, curl |
| F11 | Disk: 47 GB free on `/data`. The stuck 24 GB BB pack blocks space, and one Reacher release was rejected with `Importing after download will exceed available disk space`. | `df`, release rejections |

### Hypotheses (not proven)
- H1: The "few results" feeling comes from the RSS feed being dominated by unrelated/Russian/anime
  releases. 16.6k `Unknown Series` and 14k `Unknown Movie` rejections are normal RSS noise, not an error.
- H2: Movies like Supergirl (2026) and the Batman: Knightfall parts may not be released yet, so there's
  nothing to find regardless of config.

---

## Part 2: Plan

### Phase 0: Backups (before any change)
Why: every step below is reversible only if we have the "before" state. *arr apps keep
state in SQLite. Copying a live SQLite file with `cp` can capture a half-written page, so we use
each app's own backup command, plus a consistent SQLite copy for rdtclient.

1. `B=~/HomeLab/backups/streaming-$(date +%Y%m%d-%H%M%S)` (outside the repo, so it never ends up in git).
2. `cp docker-compose.yml caddy/Caddyfile scripts/configure-base-urls.sh CLAUDE.md "$B/"`.
3. Sonarr/Radarr: `POST /api/v3/command {"name":"Backup"}`, then copy the resulting zip from
   `config/<app>/Backups/manual/`. Also tar `config/{sonarr,radarr,jackett}`. Jackett is file-based
   JSON, so tar is safe.
4. rdtclient: Python `sqlite3.Connection.backup()` of `config/rdtclient/rdtclient.db`. This is
   the online-backup API, safe with WAL mode.
5. Export the current *arr indexer lists (`GET /api/v3/indexer`) to JSON in `$B`, so we can
   recreate the Jackett feeds if needed. Secrets come back masked, which is fine.

### Phase A: Fix the real blockers (small, targeted)
6. **rdtclient Mapped path → `/data/torrents`** (F1/F2). Do it in the rdtclient UI
   (Settings → Download Client) and confirm it in the DB afterwards.
   Why: the rdt-client README says *Mapped path* is the path reported back to Sonarr/Radarr. Both
   containers mount the host dir at the same `/data/torrents`, so the correct value is identical
   to Download path and no Remote Path Mapping is needed. This is the TRaSH "same path everywhere"
   principle (trash-guides.info → File and Folder Structure → Docker).
   Verify: Sonarr's queue `outputPath` becomes `/data/torrents/tv-sonarr/…`, BB S2 imports
   (hardlink), the health error clears after `POST /api/v3/command {"name":"CheckHealth"}`, and
   the same holds in Radarr.
7. **Radarr language.** Change the profile language from "Original" to "Any" on the
   profile(s) in use (id 1 "Any"). Why: TRaSH Guides recommend handling language with Custom
   Formats rather than the profile's language field. With "Original", Spanish releases of
   English films get rejected, and most of your working sources are Spanish. **Decision
   (2026-09-27): "Any" + a TRaSH-based custom format that prefers multi-audio releases.**
8. **Trigger the missing searches.** Run `SeriesSearch` for Reacher (id 3) and `MissingMoviesSearch`
   in Radarr, *after* step 6 so grabs can import. Why: Sonarr/Radarr only search on add when
   "Start search for missing" is ticked. Otherwise they wait for RSS, which only sees *new*
   releases (wiki.servarr.com/sonarr/faq, "How does Sonarr find episodes?").

### Phase B: Add Prowlarr to compose (diff shown before applying)
9. Compose diff to add:
   ```yaml
   prowlarr:
     image: lscr.io/linuxserver/prowlarr:latest
     container_name: prowlarr
     restart: unless-stopped
     environment: [PUID=1000, PGID=1000, TZ=America/Argentina/Mendoza]
     volumes:
       - ${CONFIG_DIR:-./config}/prowlarr:/config
     networks: [proxy]
   ```
   - No `/data` mount, because Prowlarr never touches media files (linuxserver.io prowlarr docs
     only list `/config`).
   - **No `ports:`**: exposed through Caddy at `/prowlarr`, like Sonarr/Radarr (your choice).
     Port 9696 was checked free anyway (`ss -tuln`, `docker ps --filter publish=9696`). I'll
     re-run with `sudo ss -tulpn` as CLAUDE.md requires.
   - **Not behind gluetun.** Why: (a) the VPN exists to hide *P2P traffic*, and Prowlarr only
     makes HTTPS queries to indexer websites, the same reasoning that already keeps rdtclient off the
     VPN. (b) `network_mode: service:gluetun` removes the `prowlarr` hostname from the `proxy`
     network (the same issue that forced the `gluetun:8090` workaround for qBittorrent), and every
     gluetun restart or VPN drop would break indexer sync and searches. (c) If an indexer is ever
     ISP-blocked, the Servarr-sanctioned way is a per-indexer **Indexer Proxy** (HTTP/SOCKS)
     selected by tag (wiki.servarr.com/prowlarr/settings → Indexer Proxies). Gluetun's built-in
     HTTP proxy (`HTTPPROXY=on`, gluetun wiki) could serve that later. Nothing is blocked today
     (F9), so it's not added now.
   - Jackett gets `profiles: ["rollback"]`. Why: `docker compose stop` alone isn't durable, because
     the next plain `docker compose up -d` would start it again. A profile keeps the service defined
     (easy rollback) but excluded from default `up`.
10. Caddyfile: add an `@prowlarr path /prowlarr*` → `reverse_proxy prowlarr:9696` block and add `/prowlarr`
    to the 404 hint. `scripts/configure-base-urls.sh`: add prowlarr using the existing `wait_for_config`
    and `set_urlbase_xml` helpers.
11. Apply scoped: `docker compose up -d prowlarr`, then run the base-url script (it only restarts what
    it changed), then `docker compose restart caddy`. The Caddyfile has `admin off`, so no hot
    reload is possible, and the web UIs blip for about 2s. Jellyfin, calisteniapp-*, and gluetun
    are not touched.
12. **Auth.** In the first-run UI set Authentication = Forms, Required = Enabled (not "Disabled for
    local addresses"). Verify in `config/prowlarr/config.xml` (`AuthenticationMethod=Forms`,
    `AuthenticationRequired=Enabled`) and check that `curl /prowlarr/` without a session gets a
    redirect to login, per CLAUDE.md.

### Phase C: Configure Prowlarr (UI + API, container names only)
13. **FlareSolverr proxy.** Settings → Indexers → add FlareSolverr, host `http://flaresolverr:8191/`,
    tag `flaresolverr`. Why tag-based: Prowlarr sends a request through FlareSolverr only for
    indexers sharing the tag, and it spins up a headless Chrome per request, so running it on
    every indexer would be slow and wasteful (wiki.servarr.com/prowlarr/settings → Indexer Proxies).
14. **Apps** (Settings → Apps), Sync Level **Full Sync**:
    - Sonarr: Prowlarr Server `http://prowlarr:9696/prowlarr`, Sonarr Server `http://sonarr:8989/sonarr`.
    - Radarr: `http://prowlarr:9696/prowlarr`, `http://radarr:7878/radarr`.
    - The UrlBase suffix is required because both apps run with a UrlBase. Why container names: they
      resolve on the `proxy` network regardless of the host IP, which fixes F7. Why Full Sync: Prowlarr
      becomes the single source of truth, and categories, priorities, and removals propagate
      automatically. Manual edits made inside Sonarr/Radarr get overwritten, which is the intended
      trade-off (wiki.servarr.com/prowlarr/settings → Applications, Sync Level).
    - Sonarr **Anime Sync Categories** = `5070` (TV/Anime). Standard sync categories stay at their
      defaults. This is how Nyaa ends up in Sonarr's *Anime Categories* field: Prowlarr places an
      indexer's categories into the matching field.
15. **Indexers** (the "useful set" you chose):
    | Indexer | Why | Notes |
    |---|---|---|
    | Nyaa.si | anime | Its definition maps anime to 5070. **Verify after sync** that Sonarr shows it with `animeCategories=[5070]` and no standard 5030/5040, and that it's absent from Radarr (no movie cats → Prowlarr skips it). If Nyaa's "Live Action" subcategory leaks 5000 into standard cats, fall back to restricting Sonarr's standard Sync Categories to subcats. |
    | The Pirate Bay | general, strongest results today (F9) | |
    | divxtotal | Spanish TV/movies (63 results) | |
    | bigfangroup | Multi-audio releases, grabbed Scary Movie 3 | |
    | 1337x | general | tag `flaresolverr`. Cloudflare sometimes beats FlareSolverr anyway, so if tests fail it's marked as a known limitation, not force-fixed. |
    | Opensharing | only if its Prowlarr definition exposes TV/Movie cats | Checked in "Add Indexer" before adding. |
    | dontorrent | Spanish source of all Lanterns grabs | Added **disabled** while `todotorrents.org` returns 522; re-enable when "Test" passes. |
    Dropped: aniRena, bangumi, dmhy (redundant with Nyaa for anime), and apachetorrent (0 results).
    Public alternatives to try if results stay weak (check each still works with "Test" first):
    **EZTV** (TV), **YTS** (movies, small encodes), **Knaben** (meta-search aggregator),
    **AnimeTosho** (anime, mirrors Nyaa with better metadata).

### Phase D: Verify Prowlarr-synced indexers (the old Jackett ones are still present)
16. In each app, `GET /api/v3/indexer` shows `… (Prowlarr)` entries with base URL `http://prowlarr:9696/prowlarr/<id>/`,
    correct categories, and **no** anime cats on TPB (fixes F6).
17. `POST /api/v3/indexer/test` for each Prowlarr indexer returns OK.
18. Repeat the interactive searches from the diagnosis (Reacher S1, Scary Movie 2). Prowlarr-sourced
    releases appear with approvals ≥ the Jackett baseline (16 / 15). One real grab completes and
    imports via rdtclient.

### Phase E: Retire Jackett (only after D passes)
19. `DELETE /api/v3/indexer/{id}` for the 9 Jackett Torznab entries in Sonarr and Radarr (IDs taken from the
    Phase 0 export).
20. `docker compose stop jackett`. Don't remove it: the container, image, and `config/jackett` stay
    for one week. Put a reminder date in the doc (2026‑10‑04 if we do this today) for deciding
    on final removal.

### Phase F: Docs
21. Write `docs/prowlarr-migration.md` (English): final architecture diagram (text), what changed and why
    (with the citations above), how to verify (the exact API calls/curl from Phase D), and how to roll back:
    `docker compose --profile rollback up -d jackett`, recreate the Torznab feeds from the Phase 0 JSON
    (the Jackett API key is re-read from ServerConfig.json, never pasted), disable the Prowlarr apps,
    and restore the backups if needed.
22. Update CLAUDE.md: Jackett → Prowlarr decision, Prowlarr under `/prowlarr`, Jackett in the `rollback`
    profile, and rdtclient Mapped path. Mention `docs/prowlarr-migration.md` from `docs/INDEXERS.md`.
23. Show the full `git diff`. I'll commit only if you ask (compose, Caddyfile, script, docs, and CLAUDE.md
    only, with no unrelated changes).

## Guardrails applied throughout
- API keys are read from `config.xml` / `ServerConfig.json` into shell variables and never echoed.
  No `docker compose config` (it would expand the VPN key).
- Only `prowlarr`, `caddy`, `sonarr`/`radarr` (via the base-url script if needed), and `jackett` (stop)
  are touched. No `down`, no `-v`, no deletes under `data/` or `config/`.
- The compose diff is shown and approved before it's applied (CLAUDE.md rule).
