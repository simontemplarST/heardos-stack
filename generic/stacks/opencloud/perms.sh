#!/bin/sh
# opencloud-perms: OpenCloud (PosixFS) creates files 0600 and folders 0700. The shared spaces are also read by other apps
# (Navidrome uid 1000 reads Music, Paperless gid MEDIA_GID consumes the inbox), so re-open what appears: files ug+rw,o+r, dirs 2775.
# OpenCloud's own .oc-nodes / .oc-tmp stay private. Space folders are mounted at /spaces/<name> by compose.override.yml.
set -u
ls -d /spaces/* >/dev/null 2>&1 || { echo "no spaces mounted yet"; exec sleep infinity; }
sweep() { find /spaces/*/ \( -name .oc-nodes -o -name .oc-tmp \) -prune -o \( -type d ! -perm 2775 -exec chmod 2775 {} + \) -o \( -type f ! -perm -0664 -exec chmod ug+rw,o+r {} + \) 2>/dev/null; }
sweep; echo "initial sweep done"
( while sleep 3600; do sweep; done ) &
exec inotifywait -m -r -q -e create -e moved_to -e close_write --exclude '/\.oc-(nodes|tmp)(/|$)' --format '%w%f' /spaces/*/ |
  while IFS= read -r p; do if [ -d "$p" ]; then chmod 2775 "$p" 2>/dev/null; else chmod ug+rw,o+r "$p" 2>/dev/null; fi; done
