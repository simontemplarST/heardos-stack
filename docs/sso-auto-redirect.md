# Skipping app login pages when already signed in

Authentik sessions already carry people through the forward-auth gate on every site. What remains is each app's own login
page with a "Sign in with HeardOS" button. Most apps can skip it and go straight through the OIDC flow; the session makes that
invisible. Password login stays enabled everywhere as break-glass.

| App | Setting | Break-glass URL |
|---|---|---|
| Immich | `PUT /api/system-config` → `oauth.autoLaunch: true` | `/auth/login?autoLaunch=0` |
| Paperless-ngx | `PAPERLESS_REDIRECT_LOGIN_TO_SSO=true` (the login page auto-submits the OIDC form) | `/accounts/login/?loginRedirectChain=false` |
| Mealie | `OIDC_AUTO_REDIRECT=true` | `/login?direct=1` |
| Homebox (≥ 0.26) | `HBOX_OIDC_AUTO_REDIRECT=true` | unset the variable |
| Nextcloud (user_oidc) | `occ config:app:set user_oidc allow_multiple_user_backends --value=0` | `/login?direct=1` |
| Kasm Workspaces | OIDC config `auto_login: true` via the admin API (`update_oidc_config`: send list fields such as `scope` as newline-joined strings or it returns 500) | `/#/staticlogin` |
| Proxmox VE | `pveum realm modify <oidc-realm> --default 1` | pick `pam` / `pve` in the realm list |

Already seamless: apps that trust the proxy's identity headers (Homepage, the dashboard, Navidrome, Calibre-Web) and
Actual Budget with `ACTUAL_OPENID_ENFORCE=true`.

Cannot skip:

- **Wallos** (5.8): password login can be disabled, but there is no auto-redirect, so the page shows one button.
- **Portainer CE**: the internal login form cannot be hidden.
- **Vaultwarden**: the master password is the vault's encryption key; SSO cannot replace it by design.

The OIDC stack templates in `generic/stacks/` (paperless, mealie, homebox) ship with these settings on.

Related fix: `generic/42-ak-app.py` attaches the email scope mapping to every OIDC provider it creates (it used to look the
mapping up by an outdated name and silently skip it). Apps that request `openid profile email` (Kasm, Ziti's console) fail
with "scope has changed" or reject the token without it.
