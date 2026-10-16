# The cloud front door

`https://cloud.<domain>/<slug>` is one memorable address for everything. It is a Caddy site on the lighthouse
(`edge/Caddyfile`) that does three things:

1. **Redirects by slug.** A `map {uri} {dest}` block holds one regular expression per Authentik application slug plus
   the public subdomain name as an alias, so `/immich` and `/photos` both become `https://photos.<domain>/`. Sub-paths
   and query strings are carried over: `/photos/albums/1?x=1` → `https://photos.<domain>/albums/1?x=1`. Bare `/baikal`
   lands on the Baïkal admin page. `/` and anything unknown go to the Authentik application library.
2. **Serves brand assets** under `/brand/*` from `/srv/heardos-brand` on the VPS, for the email templates and anything
   else that needs a public image URL. This `handle_path` comes first; the redirect lives inside a `handle` block so it
   does not swallow the asset path (Caddy runs `redir` before `handle` otherwise).
3. **Sets the same security headers** as every other site.

What points at it:

- Every Authentik application's launch URL (`authentik/launch-urls.py`, idempotent, with `--restore`).
- The Homepage hub tiles (`apps/homepage-hub/services.yaml`).
- The family dashboard's card links (`apps/dash/set-secrets.sh`, the `*_PUBLIC_URL` variables).

Why a redirector and not path-based hosting: Immich, Mealie, Home Assistant, Homebox, Actual Budget, Invidious,
Homepage and Authentik itself do not support being served under a path prefix. The redirect gives the single address
without breaking any of them.

Adding an app: one map line in the Caddyfile, validate, reload; re-run `launch-urls.py`.
