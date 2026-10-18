#!/bin/sh
# fetch.sh DATA_DIR ZIM — download a Kiwix ZIM with aria2 via Kiwix's metalink (many mirrors in parallel, piece hashes
# checked, resumable), check it against Kiwix's sha256, then restart the kiwix container. 50-app.sh kiwix starts this as
# the transient unit kiwix-fetch; re-run the same command to resume. Never add --check-integrity on resume: it re-hashes
# the whole (100+ GB, sparse) file before downloading anything.
set -eu
D="${1:?data dir}"; Z="${2:?zim file name}"; U="https://download.kiwix.org/zim/wikipedia"
case "$Z" in wikipedia_*) ;; *) U="https://download.kiwix.org/zim/${Z%%_*}" ;; esac
cd "$D"
[ -s "$Z.sha256" ] || curl -fsSL "$U/$Z.sha256" -o "$Z.sha256"
aria2c -c --file-allocation=none --summary-interval=300 --console-log-level=warn \
  --split=16 --max-connection-per-server=4 --min-split-size=20M --uri-selector=adaptive \
  --lowest-speed-limit=500K --max-tries=0 --retry-wait=10 "$U/$Z.meta4"
[ -f "$Z.aria2" ] && { echo "download incomplete — re-run to resume"; exit 1; }
if [ "$(sha256sum "$Z" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$Z.sha256")" ]; then
  echo "VERIFIED $Z"; docker restart kiwix >/dev/null && echo "kiwix restarted"
else
  echo "CHECKSUM MISMATCH for $Z — delete it and re-run"; exit 1
fi
