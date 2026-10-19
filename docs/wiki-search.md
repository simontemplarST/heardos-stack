# Offline Wikipedia and private search

| App | Address | Sign-in | Notes |
|---|---|---|---|
| Kiwix | `wiki.<domain>` | Authentik gate (Authentik app `wiki`); kiwix-serve has no login of its own | Serves one ZIM file from `data/kiwix`. Full-text search built in. |
| SearXNG | `search.<domain>` (and the LAN name) | **None: public on purpose** | So every family browser, signed in or not, can use it as its search engine. Bot limiter on (Valkey), HTML results only, image proxy on, safe search moderate. |

Install with `50-app.sh kiwix` and `50-app.sh searxng`, then add `wiki` and `search` to `PUBLIC_HOSTS` and re-run
`30-vps-caddy.sh` and `32-dns.py --apply` (without `--apply` it only prints the plan).

## Kiwix: the download

`50-app.sh kiwix` deploys the stack, then starts the transient unit `kiwix-fetch`, which runs
[`apps/kiwix/fetch.sh`](../apps/kiwix/fetch.sh):

- **aria2 on Kiwix's metalink** (`<zim>.meta4`), so the file comes from several mirrors at once and every piece
  is hash-checked: about 50 MB/s on a 1 Gbit line, so roughly 40 minutes for the full English dump.
- It checks the finished file against Kiwix's `.sha256`, then runs `docker restart kiwix`.
- It resumes. Re-running `50-app.sh kiwix` or the same `systemd-run` restarts it from where it stopped.
- **Don't use `--check-integrity` on resume.** It re-hashes the whole sparse 100+ GB file before downloading anything.
- Progress: `journalctl -fu kiwix-fetch`.

Until the file is verified, the `kiwix` container restarts in a loop, printing "Here is the content of /data". That is
expected.

| `KIWIX_ZIM` (plan file) | Size | What |
|---|---|---|
| `wikipedia_en_all_maxi_2026-08.zim` (default) | ~119 GB | Every article with pictures |
| `wikipedia_en_all_nopic_<date>.zim` | ~50 GB | Every article, no pictures |
| `wikipedia_en_all_mini_<date>.zim` | ~12 GB | Intro + infobox of every article |

`50-app.sh` warns when less than `KIWIX_MIN_FREE_GB` (default 130) is free. To move to a newer dump, set `KIWIX_ZIM`,
re-run `50-app.sh kiwix` (it downloads the new file and the stack names it), then delete the old file.

### Gotcha: never pass `--port`

The image's `start.sh` already runs `kiwix-serve --port=$PORT "$@"`. Adding `--port=8080` to the command makes
kiwix-serve see the option twice, print its usage and exit, and the container restarts forever with exit code 0.
The live install hit this once, right after the download finished.

## SearXNG

- Config is rendered from [`apps/searxng/settings.yml`](../apps/searxng/settings.yml) and
  [`limiter.toml`](../apps/searxng/limiter.toml) into `data/searxng/config`. Edit the templates, then re-run
  `50-app.sh searxng`.
- **Config files must be 0644.** The container runs with `cap_drop: ALL`, so root inside it can't read a 0600 file and
  SearXNG fails to start.
- `trusted_proxies` = the docker proxy subnet and the Nebula overlay, so the limiter sees the real client address
  forwarded by Caddy → Traefik. `pass_ip` = the overlay and the home LAN, so family devices never hit the limiter.
- `SEARXNG_SECRET` is generated into `secrets.env`.
- Browser setup: search URL `https://search.<domain>/search?q=%s`.
