# HeardOS logo — concept 2a (Hearth Crest)

Colours: orange #f2a35e · ink #1f2023 · light #ececec · charcoal #26272b
Wordmark: Young Serif (converted to outlines; no font needed).

- svg/mark — symbol only (colour on light/dark, mono black/white, orange)
- svg/lockup — horizontal and stacked lockups
- svg/icon — app icons and favicon.svg (switches to light in dark mode)
- png — rasterised versions; mark 16–1024, lockups 128/512px tall, icons 180/192/512, apple-touch-icon, favicon 16/32

Favicon/small sizes drop the inner keyline.

HTML:
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">

background: png/background/flow-background-2560x1440.jpg — generated 2026-10-16 (charcoal->ink vignette, hearth-orange glow lower right, faint mono shield watermark, 96px slate grid at 3.5%); Authentik branding_default_flow_background. Generator: ~/heardos-install/authentik/make-flow-background.py
