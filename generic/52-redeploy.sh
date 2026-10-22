#!/usr/bin/env bash
# 52-redeploy.sh — run ON THE HOST as root:  52-redeploy.sh NAME
# Redeploy $HEARDOS_ROOT/stacks/NAME as it is on disk (compose.yml + .env + compose.override.yml), through Portainer when
# the plan has a token, else docker compose. The installer runs this after changing an app's settings.
. "$(dirname "$0")/lib/common.sh"
require_root; load_env
name="${1:?stack name}"
[[ "$name" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "bad stack name '$name'"
deploy_stack "$name"
msg "$name redeployed"
