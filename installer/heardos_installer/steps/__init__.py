"""The full install DAG for a plan. Order is decided by dependencies, not by list position:

  layer 0  ssh.keygen · dns.validate · nebula.ca
  layer 1  provider.validate · host.probe
  layer 2  vps.create · host.push_kit
  layer 3  vps.harden · dns.publish · host.bootstrap          (VPS boot and home Docker install overlap)
  layer 4  vps.nebula · vps.caddy · portainer.init · host.edge (certificates issue while apps install)
  ...      host.nebula · host.authentik → authentik.bootstrap → mail.configure · apps (serial on the host) → verify
  mail.domain runs from layer 1 so Resend's DNS records propagate while everything else installs
"""
from __future__ import annotations

from ..engine.step import Step
from . import apps, brand, custom, host, local, mail, vps
from .common import apply_defaults, missing_inputs  # noqa: F401


def build_steps(plan: dict) -> list[Step]:
    out = local.steps() + vps.steps() + host.steps() + brand.steps() + mail.steps() + apps.steps(plan.get("apps") or [])
    extra = custom.steps(plan)          # "your own app" stacks: installed after the catalogue apps, checked by verify
    for s in out:
        if s.id == "verify":
            s.deps = s.deps + [c.id for c in extra]
    return out + extra
