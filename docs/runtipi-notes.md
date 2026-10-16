# Operating Runtipi: things that bite

Collected from running HeardOS on Runtipi v4.10. Each one cost real time once.

## `update-config` rewrites `app.env` from the store form

Send **every** form field with the right types (booleans as booleans), and put the fields at the **top level** of the
JSON body next to `openPort`, `exposed`, `exposedLocal`, `isVisibleOnGuestDashboard`. A nested `form` object is
accepted with 200 and silently ignored, which produces either "Variable … is required" 500s or, worse, a blank variable
that crash-loops the app (Mealie's `ALLOW_SIGNUP`, 2026-10-15). `security/close-lan-ports.sh` shows the working shape.
Prefer a user-config override for anything the form does not own.

## Never set `exposedLocal=false` on an app whose public router uses `@docker`

Local exposure owns the container's Traefik labels. Turn it off and the Docker-provider service disappears; a public
router that referenced it loses its backend and the hostname falls through to the Runtipi dashboard. Public routers
should use file-provider services by container name (`host/traefik/public-example.yml` does), and only `openPort`
should ever be changed.

## The dashboard is the catch-all

A hostname that answers with `<meta charSet="UTF-8">` (camel case) or
`{"statusCode":404,"message":"Cannot GET …"}` is the Runtipi dashboard, not your app. It means a router vanished.

## The API wants a specific Origin

`tipi.py` sets `Origin`/`Referer` to the base URL it is called with. `http://localhost` is refused with "Invalid request
origin"; use the LAN address, or the overlay address when away from home. Login uses `RUNTIPI_ADMIN_USER`, not
`_USERNAME`.

## Custom apps

`POST /api/custom-apps` takes compose-style JSON: `{"name": …, "config": {"services": {…}, "x-runtipi": {"schema_version": 2}}}`.
A one-shot container (`restart: "no"`) is a handy way to run a command inside the host's Docker network when SSH is
not available; its status shows "stopped" once it exits, which is success, so read the logs. The SSE log stream at
`/api/sse/app-logs?appUrn=<urn>` only yields output for a stream opened **after** install; start the app again to replay.

## Overrides are the durable place for settings

`user-config/<store>/<app>/docker-compose.yml` is merged on every start and survives store updates. Environment,
extra mounts, OIDC settings, CPU caps all belong there. The store's own compose must not be edited.

## Images with long health checks

Traefik's Docker provider hides a router while its container's health check is "starting". After a restart a LAN name
can be missing for a minute or more. Public routers avoid the issue by not using the Docker provider at all.

## Exec hygiene

`docker exec -i` without an attached stdin hangs forever when driven from a script. Use `docker exec … </dev/null`,
or copy a script in and run it with the app's own shell (`ak shell -c 'exec(open(...).read())'` for Authentik).

## Immich can starve everything

A single ffmpeg transcode ignored the configured thread limit and pushed the host to load 30+, which knocked Authentik
over. Immich's server and ML containers carry `cpus:` caps in their override; keep them.
