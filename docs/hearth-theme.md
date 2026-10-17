# One look across the apps (Hearth Crest)

The Authentik login page sets the look: ink `#1f2023` / charcoal `#26272b` surfaces, light `#ececec` text, orange
`#f2a35e` primary (hover `#f5b984`, links `#ed8325`, darker `#c86a16`), `#3a3b40` borders, Inter. Each app gets as much of
it as it allows. Files: [`apps/brand-theme/`](../apps/brand-theme/).

| App | Mechanism |
|---|---|
| Immich | system config `theme.customCss` — `--immich-*` and the `--immich-ui-primary-*` scale (light mode uses `#ed8325` for contrast) |
| Mealie | `THEME_{DARK,LIGHT}_{PRIMARY,ACCENT,SECONDARY}` env |
| Nextcloud | `occ theming:config` primary_color, background (flow background image), logo, logoheader, favicon, name |
| Listmonk | settings `appearance.admin.custom_css` (CSS variables) and `appearance.public.custom_css` (+ logo/favicon URLs) |
| Wallos | per user in its DB: `dark_theme`, `custom_colors`, `custom_css_style` |
| Homepage hub | `custom.css` |
| HortusFox | `AppModel.custom_head_code` (a `<style>` block; `LOAD_FILE` is not allowed, use an escaped SQL literal) |
| Vaultwarden | `data/templates/scss/user.vaultwarden.scss.hbs` — the 2026 web vault uses a `--color-brand-050…950` hex scale |
| Home Assistant | a `themes/` YAML ("HeardOS Hearth", dark + light) set as the backend default with `frontend.set_theme` |
| Portainer / Paperless | logo (and app title) only |
| Navidrome / Calibre-Web / Invidious | closest built-in dark theme as the default |

Not themeable: Kasm Workspaces (branding is a licensed feature on the community edition), Proxmox, Baïkal, OnlyOffice,
ArchiveBox (would need custom Django templates); Homebox and Actual only offer per-user built-in themes.
