#!/bin/bash
# dl-cookbooks.sh [manifest.json] — downloads every PDF (+EPUB where present) from archive.org into /root/cookbooks/<id>/
set -u; M="${1:-/root/cookbooks.json}"; mkdir -p /root/cookbooks; cd /root/cookbooks
python3 -c 'import json,sys;[print(b["id"],b["pdf"],b["epub"] or "-",sep="\t") for b in json.load(open(sys.argv[1]))]' "$M" | while IFS=$'\t' read -r id pdf epub; do
  mkdir -p "$id"
  for f in "$pdf" "$epub"; do [ "$f" = "-" ] && continue
    [ -s "$id/$f" ] && { echo "have $id/$f"; continue; }
    url="https://archive.org/download/$id/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$f")"
    curl -sL --retry 3 -o "$id/$f.part" "$url" && mv "$id/$f.part" "$id/$f" && echo "ok $id/$f $(stat -c %s "$id/$f")" || echo "FAIL $id/$f"
  done
done
echo DONE
