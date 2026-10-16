#!/usr/bin/env python3
"""44-authentik-brand.py — point Authentik's brand at the files the installer uploaded to media/public/branding:
title, per-theme logo, favicon, default flow background and custom CSS. Run ON THE HOST (or anywhere with the plan file).
Media paths are relative to /media/public (Authentik rejects absolute paths). Idempotent."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import AK, load_env

env = load_env(); ak = AK(env)
root = env.get("HEARDOS_ROOT", "/opt/heardos")
css_file = f"{root}/data/authentik/media/public/branding/custom.css"
css = open(css_file).read() if os.path.exists(css_file) else ""
if os.environ.get("HEARDOS_DRY_RUN"):
    print("[dry-run] would set the Authentik brand"); sys.exit(0)
brand = ak.results("/core/brands/")[0]
ak.patch(f"/core/brands/{brand['brand_uuid']}/", {
    "branding_title": env.get("BRAND_TITLE") or "HeardOS",
    "branding_logo": "branding/logo-%(theme)s.png",
    "branding_favicon": "branding/favicon-64.png",
    "branding_default_flow_background": "branding/flow-background.jpg",
    "branding_custom_css": css,
})
for f in ak.results("/flows/instances/"):   # flows with their own background would ignore the brand default
    if f.get("background") and "branding/" not in str(f.get("background")):
        try:
            ak.patch(f"/flows/instances/{f['slug']}/", {"background": "branding/flow-background.jpg"})
        except SystemExit:
            pass
print(f"brand applied: {env.get('BRAND_TITLE') or 'HeardOS'}")
