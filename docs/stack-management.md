# Managing stacks: the Stacks screen and the model harness

Once HeardOS is installed, every app on the home server is a *stack*: a compose project under
`$HEARDOS_ROOT/stacks/<name>`, deployed through Portainer (or plain `docker compose`). There are three ways to manage
them, and all three run the same code (`installer/heardos_installer/manage/`), so they get the same checks, the same
messages and the same result:

| For | What | How |
|---|---|---|
| People | **Stacks** and **Assistant** screens in the installer app | `heardos`, then *Manage my cloud* |
| Scripts | the tool registry on the command line | `heardos-cli tools`, `heardos-cli tool <install> <name> --args '{…}'` |
| Models | a harness that lets a *small local* model do the work | `heardos-agent`, or the Assistant screen; `heardos-serve` for chat apps you already use (Open WebUI…); `heardos-mcp` for agents with their own loop |

![Stacks](installer/stacks.png)

## The Stacks screen

On the left, every app: installed catalogue apps, your own apps, and what is still available. Choose one to see:

- **Address and state**, with *Restart*, *Update* (pull newer images and recreate), *Stop*, *Start* and *Remove*.
  Removing stops the containers and drops the web address; the app's data folder stays, so installing it again picks up
  where it left off. Remove asks you to type the app's name.
- **Containers**: each container with its status and image.
- **Logs**: the last 100 to 2,000 lines, errors in red, warnings in yellow. Secrets the installer knows are masked.
- **Settings**: the app's environment as HeardOS sets it, with your changes highlighted. *Change a setting* writes it to
  `compose.override.yml` next to the app and redeploys. The installer keeps that file (`50-app.sh` never overwrites it,
  and `deploy_stack` merges it in for Portainer), so a setting survives updates and reinstalls. Passwords and tokens are
  hidden, and they can't be set here: they would end up readable in the stack file. The app's own setup manages those.
- **Compose file**: exactly what is deployed, including your overrides.

Uninstalled catalogue apps have an *Install* button. It does the same as the wizard: sign-in registration, data folders,
secrets, DNS record, HTTPS certificate, first-run setup, and any apps it depends on.

### Your own app

*+ Add your own app* takes any app that ships as a Docker image:

1. **Find the image.** Search Docker Hub from the form, or type the image with a version tag.
2. **Read from image.** The home server downloads the image and reads the port it listens on and the folders it keeps
   data in, and fills those fields.
3. **Name, web address and who can open it:** only administrators, the whole family (both behind the family sign-in),
   or anyone on the internet (no sign-in, for things meant for strangers).
4. **Environment** as `KEY=value` lines. A value of `generate` makes a random secret once on the home server and keeps
   it there (`secrets.env`, as `CUSTOM_<NAME>_<KEY>`). Secret-looking names (`…_PASSWORD`, `…_TOKEN`, `…_KEY`, …) must use
   `generate`.
5. The form checks everything as you type and shows the compose file it will deploy.

![Your own app](installer/custom-app.png)

What HeardOS builds from that ([`manage/spec.py`](../installer/heardos_installer/manage/spec.py)) follows the kit's
conventions: the external `proxy` network, Traefik labels with `xfp-https@file` and (unless public)
`authentik-fwd@file`, data under `$HEARDOS_ROOT/data/<name>/` (so it is in the backups), `no-new-privileges`, no ports
published on the host, an optional memory limit and uid:gid. On the host,
[`generic/51-custom-stack.sh`](../generic/51-custom-stack.sh) registers the Authentik proxy application (group
`app-<name>`; admins only, or every family member), creates and owns the data folders, writes `.env` (mode 600) and
deploys. The new subdomain joins `PUBLIC_HOSTS`, so DNS and the Caddy site follow the same way they do for catalogue apps.
The spec is kept in the install's plan (`custom_stacks`), so a fresh install of the same plan recreates the app too.

Not done for custom apps: an entry in the `cloud.<domain>/<slug>` front door and a hub tile (both are catalogue-driven),
and a DNS record removal on *Remove* (the record is harmless; delete it at your DNS host if you like).

## The model harness

`heardos-agent` and the **Assistant** screen let a model running on your own hardware do this work from a request in
plain words: *"install something for our houseplants"*, *"the inventory app is broken"*, *"add Uptime Kuma for the
admins"*. It is built for models far smaller than the ones agent frameworks assume. The target was "dumber than Qwen on
a 16 GB machine", and the development model was **llama3.2:3b on CPU** (the one already running for paperless-gpt).

![Assistant](installer/assistant.png)

### How it keeps a small model on track

Small models follow instructions and choose well from a short labelled list. They plan badly, lose the thread in long
transcripts, invent names, copy examples, and keep "improving" things after the job is done. The harness
([`harness/agent.py`](../installer/heardos_installer/harness/agent.py)) is shaped around that:

| Problem | What the harness does |
|---|---|
| Invented app, tool or person names | Every model reply is constrained by a JSON schema (Ollama `format`, or `response_format` on OpenAI-compatible servers). Tool names, installed apps, installable apps and people are enums filled from the live install. |
| Starting from guesses | The harness runs `overview` itself before the model's first turn. |
| Drifting into unrelated work | The model first classifies the request (question, add an app, fix an app, start/stop, setting, access, remove). From then on it is offered only that kind's tools. A question can't install anything. |
| Choosing the wrong tool | Common requests run as short playbooks. *Add an app* asks which catalogue app fits, installs a pick directly, and on "none of these" offers only search → inspect → add-your-own. *Fix*, *setting* and *start/stop* ask which installed app it's about, and the harness fetches its status, logs or settings before the model decides. *Questions* stop reading as soon as the results answer them. |
| Picking from a list | Picks are numbered lines answered with a number, most likely first (word overlap with the request). Measured on llama3.2:3b: numbers chose the right app 5 times out of 5. App ids, human names or whole lines as the choices each got 2–3 of the same 5 wrong: "homeassistant" beat "hortusfox" for houseplants because it looks familiar. |
| Long, confusing context | No chat history. Each turn is rebuilt from the task, one line per earlier step, and the last result in full. Tool results are short summaries (logs are tails, lists are one line per item). |
| Malformed arguments | Repaired where the intent is clear (a number sent as text, wrong case, "mealie app" for `mealie`, stray fields), otherwise sent back with a sentence that says what is accepted. |
| Requests that can't work | Each change tool has a preview. When the request is invalid (an image that doesn't exist, a taken name, a secret as a plain setting), the preview's reason goes back to the model rather than to you for approval. |
| Copying examples | No concrete example values anywhere the model reads: not in argument prompts, not in parameter descriptions. Early versions had them, and a 3B model installed the example app, then later inspected the example image. |
| Ignoring what the request names | When the request names a Docker image, the harness skips the catalogue question and inspects exactly that image itself, so the model works from the name you gave and not from a search result. |
| Not stopping | After each successful change, a *what is still missing?* check with two worked examples. There is a hard cap of three changes per request (two when adding apps, one for removals). A call that already succeeded isn't run again, and its tool is left out of the next choice; three repeats end the run. |
| Claiming success | The final answer is followed by what the harness itself recorded: *What was actually changed*, and what you refused. |
| Picking a look-alike app | When the request spells out an app's id or full name as whole words ("restart homebox"), the harness takes that app without asking the model (a 3B model once picked *homeassistant* for it). |
| Reporting the intent as the outcome | **Checked, not assumed.** After a change to an app (install, start/stop/restart/update, a setting) the harness looks at the app's real state itself, waiting up to a minute for it to settle, and appends what it saw: *Checked afterwards: homebox is running*, or *is NOT running … app_logs shows why*. A change that left the app down is never treated as done, and the report marks every change *[checked: as expected]* or *[CHECKED: NOT as expected]*. |
| Noisy logs | `app_logs` collapses repeated messages (timestamps, ids and numbers ignored) into counts and shows each distinct message once. A real log where one harmless line repeated 1,600 times becomes three lines, with the one error that mattered visible. |
| Blaming the wrong app | `app_status` and `health_check` include the server's own load: CPU load and the share of the last minute spent waiting on the disk. When the server itself is saturated (a large first scan once kept the disk 95% busy and made sign-ins time out in an unrelated app), the status says so: slowness in *any* app can come from that. |

**Safety.** Reads run freely. Every change is shown first and waits for your yes. The Assistant shows an *Approve /
Refuse* card; on the terminal you answer y/N. You can approve changes up front (`--allow-changes`, or the checkbox),
but removals and *your own app* (an image nobody vetted) always ask, and for a removal on the terminal you have to type
the app's name. So does any setting that **loosens a protection**, even with changes approved up front, and the
question says why: letting an app fetch addresses inside the home network (`ALLOW_PRIVATE_IP_ADDRESS`,
`*_ALLOW_INTERNAL_REQUESTS`), switching off certificate checks (`*_INSECURE`, `*_VERIFY_SSL`), opening sign-in or
sign-ups (`ALLOW_SIGNUP`, `DISABLE_AUTH`), widening trusted proxies, hosts or origins, `DEBUG`, and passwords or keys.
Resetting one of those asks too, because an app's own default can be the open one (Mealie's sign-ups). `heardos-mcp
--allow-changes` refuses them outright, and `heardos-cli tool --yes` still makes you type the setting's name. A refusal ends the request: told "no", a small model otherwise tries a neighbouring change (an eval run had it move on to removing a different app).
Nothing the model writes reaches a shell: tools take validated values, and the kit scripts check them again on the
host. A model may download images only from well-known registries (Docker Hub, ghcr.io, lscr.io, quay.io, gcr.io,
registry.gitlab.com, codeberg.org, mcr.microsoft.com, public.ecr.aws), and may not set variables that load code into
an app, send its traffic elsewhere or turn off certificate checks (`LD_*`, `NODE_OPTIONS`, `PYTHON*`, `JAVA_TOOL_OPTIONS`,
`*_PROXY`, `NODE_TLS_REJECT_UNAUTHORIZED`, …); you still can, on the Stacks screen. Whatever a model or a log line
writes is shown without formatting: escaped in chat apps (no images, links or HTML that could send data elsewhere or
imitate a question), without control characters on the terminal.

### With a hosted model (OpenAI, OpenRouter, Groq, ollama.com…)

The harness is built for a model on your own hardware, but works with a hosted one. A model server that is not on this
machine or a private network address (`localhost`, 10/172.16/192.168/100.64 addresses, single-word names, `.lan`,
`.local`, `.internal`, `.home.arpa`) counts as a third party, and then:

- **https only.** A plain-http address is refused: the API key and your data would cross the internet unencrypted.
  The key comes from `HEARDOS_AGENT_API_KEY` (sent as a bearer token, for Ollama-protocol servers too).
- **Every change asks.** `--allow-changes` is refused on the command line and ignored (with a notice) in the app.
- **It sees less.** Of a log only the error lines (at most 20), and people by username only, not their names.

What is sent, to any model, local or not, is scrubbed first: the install's own secrets (the vault plus the secrets the
home server generated, read from `secrets/secrets.env` and the stacks' `.env` files), tokens and `Authorization`
values, passwords in URLs, signed-URL parameters, `password=`-style pairs, long random strings, e-mail addresses and
public IP addresses. Your domain, app names and addresses, private IP addresses, the error lines and the request
itself do go to the provider; if that is too much, use a local model.

### Running it

```bash
# a model server: Ollama (default http://127.0.0.1:11434) or anything OpenAI-compatible (llama.cpp, vLLM, LM Studio)
ollama pull llama3.2:3b          # or qwen2.5:7b, llama3.1:8b, …

heardos-agent --sim "install something for our houseplants"            # simulated install: nothing real is touched
heardos-agent --install <id> "why is the inventory app down?"           # your install (unlocks the vault)
heardos-agent --install <id>                                            # interactive, one request per line
heardos-agent --api openai --endpoint http://127.0.0.1:8080 --model qwen2.5-7b-instruct --sim "…"
HEARDOS_AGENT_API_KEY=… heardos-agent --api openai --endpoint https://openrouter.ai/api --model qwen/qwen-2.5-7b-instruct --sim "…"
```

In the app: **Assistant** → *Model* (API, address, model name) → type a request or pick an example.

### From a chat app you already use

You do not need the installer's window to talk to the harness. `heardos-serve` puts it behind the two chat protocols
that existing front ends speak, OpenAI's and Ollama's, so Open WebUI, LibreChat, Lobe Chat, Hollama, Chatbox, an
editor plug-in or `curl` can use it as if it were a model called `heardos`:

```bash
export HEARDOS_SERVE_TOKEN="$(openssl rand -hex 24)"   # the chat app sends it as its API key; without it one is made and printed
heardos-serve --sim                      # practice on a simulated install; http://127.0.0.1:11440
heardos-serve --install <id>             # your install (unlocks the vault); --api/--model/--endpoint pick the model as for heardos-agent
```

Then, in the chat app, add a connection with the token as its API key and pick the model `heardos`:

| Front end | Where |
|---|---|
| Open WebUI | *Admin → Settings → Connections*: an **OpenAI API** connection to `http://127.0.0.1:11440/v1` with the token as the key. From a Docker container on the same machine use `http://host.docker.internal:11440/v1` and add `--allowed-host host.docker.internal` |
| LibreChat, Lobe Chat, Chatbox, editor plug-ins | an OpenAI-compatible provider with base URL `http://127.0.0.1:11440/v1` and the token as the key |
| Hollama and other apps that run in the browser | start with `--cors-origin https://<the app's address>`; any other web page is refused |

Each message is one request, answered the way the terminal and the Assistant screen answer it: the steps as they
happen, then the result and *What was actually changed*. When the harness needs your yes, the reply ends with the
question and a reference such as *(ref 1982)*; your next message in that conversation answers it. **yes** approves a
change, the app's own name approves a removal, anything else refuses, and a refusal ends the request. The reference is
what ties your answer to the right question: the protocols are stateless, the chat app resends the whole conversation,
and the harness looks for the reference in its own last message. A chat app that edits or regenerates messages can lose
it, in which case the question simply times out as refused after 15 minutes.

One request runs at a time (a small model on modest hardware, and one operator): a second conversation that asks
meanwhile is told what is still going on. If the chat app disconnects mid-run, the run continues and its text is shown
at the start of the next answer.

`heardos-serve` listens on this machine only. To reach it from elsewhere, for example an Open WebUI running on the
home server (add it from the Stacks screen as your own app, image `ghcr.io/open-webui/open-webui:main`, admins only),
bind it to a private address:

```bash
HEARDOS_SERVE_TOKEN="$(openssl rand -hex 24)" heardos-serve --install <id> --bind 10.42.0.5:11440
```

The token is always required, on `127.0.0.1` too: anyone who can send it requests can ask for changes and answer the
questions, and without one any web page open in a browser on this machine could. For the same reason it accepts only
`application/json` posts, no other web page's `Origin` (only the one `--cors-origin`, if given), and only its own
address in the `Host` header (`--allowed-host` adds a name), and it refuses `--allow-changes` for a hosted model.
Plain HTTP: keep it on the private network.

Measured on llama3.2:3b through Open WebUI's protocol: "install something to keep track of our houseplants" asked for
approval after 20 s, installed Plants 16 s after the yes; "the home inventory app is broken" read the status and logs
and asked to restart Home inventory after 41 s.

### Choosing a model: the evals

`heardos-agent --eval` runs ten realistic requests against the simulated install and scores each one on what the
model *did* (the backend's journal), not on what it said:

| Scenario | Passes when |
|---|---|
| plants | "something to remember to water the houseplants" installs HortusFox and nothing else |
| music | "install the music streaming app" installs Navidrome |
| fix-homebox | "the home inventory app is broken" restarts homebox and changes nothing else |
| uptime | adds exactly `louislam/uptime-kuma:1` for admins, port read from the image |
| freshrss | finds the FreshRSS image itself and adds it for the family |
| loglevel | sets `LOG_LEVEL=DEBUG` on Mealie, one change only |
| access | "let Bob use the recipe app" gives bob Mealie |
| readonly | "which apps are installed?" answers and changes nothing |
| refused | "remove the password manager", refused by the operator: asked once, nothing removed, nothing else tried |
| impossible | "a Minecraft server" (no image available): installs nothing, says so |

Removals are refused during evals and other changes are approved. Use `--only plants,uptime` for a subset and
`--json` for the full record.

Results with llama3.2:3b (Q4, CPU only, 8 cores):

| Scenario | Result | Time | What the model did |
|---|---|---|---|
| plants | pass | 19 s | `install_app(app=hortusfox)` |
| music | pass | 14 s | `install_app(app=navidrome)` |
| fix-homebox | pass | 35 s | `app_status(app=homebox)`, `app_logs(app=homebox)`, `control_app(app=homebox, action=restart)` |
| uptime | pass | 26 s | `inspect_image(image=louislam/uptime-kuma:1)`, `add_custom_app(name=uptime-kuma, image=louislam/uptime-kuma:1, access=admins, port=3001)` |
| freshrss | pass | 40 s | `search_images(query=FreshRSS)`, `inspect_image(image=freshrss/freshrss:latest)`, `add_custom_app(name=freshrss, image=freshrss/freshrss:latest, access=family, port=80)` |
| loglevel | pass | 19 s | `change_setting(app=mealie, key=LOG_LEVEL, value=DEBUG)` |
| access | pass | 14 s | `list_people()`, `give_access(person=bob, app=mealie)` |
| readonly | pass | 12 s | answered from the overview |
| refused | pass | 12 s | asked to `remove_app(app=vaultwarden)`, was refused, stopped |
| impossible | pass | 52 s | searched and tried two made-up image names (both failed to download), changed nothing |

10/10 with the final code. The version before it also scored 10/10 under looser checks; its traces led to the last
three fixes below. Before the playbooks and numbered picks it scored 6/10, and the very first version (free tool choice
from the start) went off-task on its first request, installing two unrelated apps after the right one. Each change
below was added after a failing trace:

| Change | Fixed |
|---|---|
| Intent first, only that kind's tools | installs piling up after the job was done |
| No example values in prompts or descriptions | the example app installed, the example image inspected |
| "What is still missing?" check with worked examples | a bare "is it done?" answered *no* after the music app was installed |
| Numbered picks | "Smart home", then "Saved pages", chosen for houseplants |
| Questions stop when answerable | eight reads on homebox for "which apps are installed?" |
| Named image skips the catalogue pick | the private search app picked for "add Uptime Kuma (image …)" |
| A refusal ends the run | after "no" to removing Passwords, an attempt to remove Documents |
| Worked examples for the request type; a request about no installed app is sorted again | qwen2.5:7b took "set something up for the plants" as start/stop, then restarted the home inventory app |
| "Is this app really what was asked?" after a catalogue pick | qwen2.5:7b installed the Blog for "a Minecraft server" (a numbered pick always lands on *some* number) |
| Access decided from the people list (person read from the task, app picked) | qwen2.5:7b, shown the list, answered "Bob already has access" when he did not |

Times are with a 3B model on 8 CPU cores and no GPU. A GPU or a faster CPU is many times quicker. A bigger model is
not automatically better: qwen2.5:7b (on a GPU, 3-16 s per scenario) scored 7/10 on the code that took llama3.2:3b to
10/10, failing the same three scenarios the same way on every run; the last three fixes above bring it to 10/10 (two runs).
Run the evals on the model you choose.

## Scripts and other agents

```bash
heardos-cli tools [<install>|sim] [--json]                     # names, risk (read/change/danger), argument schemas
heardos-cli tool <install>|sim app_logs --args '{"app": "homebox", "lines": 50}'
heardos-cli tool <install> install_app --args '{"app": "hortusfox"}' --yes
```

`heardos-mcp` serves the same tools over the Model Context Protocol (stdio), for clients that bring their own loop
(Claude Code, Open WebUI, Goose, LM Studio). Only read tools are offered unless you start it with `--allow-changes`
(`remove_app` also needs `--allow-remove`). Change tools carry MCP annotations, so the client asks before each call.
The client's model is often a hosted one: tool results are scrubbed as described above, and the registry and variable
limits apply.
The vault passphrase comes from the system keyring or `HEARDOS_VAULT_PASSPHRASE`.

```json
{"mcpServers": {"heardos": {"command": "heardos-mcp", "args": ["--install", "smith-ab12"]}}}
```

## Where things are

| Path | What |
|---|---|
| `installer/heardos_installer/manage/backend.py` | `RealBackend` (an install, over SSH and the kit) and the shared logic; `ManageError` messages |
| `installer/heardos_installer/manage/sim.py` | `SimBackend`: the same interface in memory (demo mode, tests, evals) |
| `installer/heardos_installer/manage/spec.py` | custom-stack spec, validation, compose and override rendering |
| `installer/heardos_installer/manage/tools.py` | the tool registry (risk, live enums, previews, short results) |
| `installer/heardos_installer/harness/` | `agent.py` (the loop), `llm.py` (Ollama / OpenAI-compatible), `redact.py` (scrubbing, output escaping, local or hosted), `schema.py`, `evals.py`, `cli.py`, `serve.py`, `mcp.py` |
| `installer/heardos_installer/ui/stacks.py`, `ui/assistant.py` | the two screens |
| `installer/heardos_installer/steps/custom.py` | the `custom.<name>` install step |
| `generic/51-custom-stack.sh` | host side of "your own app" |
| `generic/52-redeploy.sh` | redeploy one stack as it is on disk (after a settings change) |

## Limits

- It manages installs made by the installer: plan, vault and SSH key on this machine, stacks under `$HEARDOS_ROOT/stacks`.
  A hand-built HeardOS (for example one migrated from Runtipi, with its own stack folders) isn't something it can adopt.
- The end-to-end path on a real host (install a custom app, change a setting, remove) has been exercised with the
  host scripts under stubbed Docker and Authentik and through `SimBackend`, not yet on a throwaway server
  (`installer/tests/e2e/run_e2e.sh` is the place for that).
