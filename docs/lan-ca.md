# Trusted certificates for LAN names (step-ca)

LAN names (`<app>.192-168-10-20.sslip.io`) used to get Traefik's self-signed default certificate. A private CA now issues
a real one.

| Piece | Where | Notes |
|---|---|---|
| step-ca | its own small container (Debian 12 LXC, 1 core, 512 MB) | ACME + JWK provisioners, 90-day certs. A CA should not share a host with dozens of app containers and the Docker socket. |
| Root key | offline, on the admin laptop only | Copied off and shredded on the CA after `step ca init`; the intermediate key stays online. |
| LAN certificate | Traefik on the app host | One wildcard `*.192-168-10-20.sslip.io` (+ the bare name), served from a dynamic TLS file. |
| Renewal | `step-renew-lan.service` on the app host | `step ca renew --daemon`, authenticated by the current cert itself (no provisioner secret on the app host). Its `--exec` rewrites the TLS file with a timestamp so Traefik reloads. |

Scripts: [`security/stepca-ct113.sh`](../security/stepca-ct113.sh) (container + packages),
[`security/stepca-init.sh`](../security/stepca-init.sh) (CA init, systemd unit),
[`security/stepca-traefik-renew.sh`](../security/stepca-traefik-renew.sh) (Traefik TLS file + renewal service).

## Things that bite

- **`ProtectSystem=full` makes `/etc` read-only** → step-ca's badger database can't write its LOCK file. Add
  `ReadWritePaths=/etc/step-ca/db`.
- **A static certificate that covers a name makes Traefik skip ACME for it** ("already covered"). The old self-signed
  wildcard had to come out of the TLS store before anything else could serve those names.
- **Routers that set `tls=true` in their own Docker labels ignore the entry point's default `certResolver`.** That is why
  the wildcard is a file, not ACME: no app stack needs relabelling. New routers can still use `tls.certresolver=stepca`.
- **`step ca certificate --san` replaces the subject in the SAN list** — list every name, including the wildcard itself.
- **Traefik ignores a file that was only touched.** Identical content is "the same configuration"; the renewal hook writes
  a new timestamp line to force the reload.
- Devices show the LAN names as trusted only after the root certificate is installed on them.
