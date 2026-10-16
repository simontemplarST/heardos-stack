#!/bin/bash
# nc-external-storages.sh — register read-only "Local" external storages in Nextcloud for the directories
# mounted at /mnt/* by the Runtipi override (user-config/migrated/nextcloud). Idempotent: skips existing mount points.
# Run on CT 108 as root:  bash /root/nc-external-storages.sh
OCC="docker exec -u www-data nextcloud_migrated-nextcloud-1 php occ"
existing=$($OCC files_external:list 2>/dev/null | awk -F'|' '/^\| [0-9]+/ {gsub(/^ +| +$/,"",$3); print $3}')
add() { # add "Mount name" /mnt/path
  if grep -qx "/$1" <<<"$existing"; then echo "exists: /$1"; return; fi
  id=$($OCC files_external:create "/$1" local null::null --config datadir="$2" 2>/dev/null | tr -dc 0-9)
  $OCC files_external:option "$id" readonly true >/dev/null 2>&1
  $OCC files_external:option "$id" filesystem_check_changes 1 >/dev/null 2>&1
  n=$(docker exec -u www-data nextcloud_migrated-nextcloud-1 sh -c "test -r '$2' && echo readable || echo UNREADABLE; ls -A '$2' | wc -l")
  echo "created /$1 (id $id) <- $2 : $(echo $n)"
}
add "Books" /mnt/books
add "Immich photos" /mnt/photos   # not "/Photos": that would shadow every user's default Photos folder
add "Comics" /mnt/comics
add "Movies" /mnt/movies
add "TV" /mnt/tv
add "Podcasts" /mnt/podcasts
add "ROMs" /mnt/roms
add "Mealie recipes" /mnt/mealie-recipes
add "Paperless export" /mnt/paperless-export
echo "== all external storages"; $OCC files_external:list 2>/dev/null | awk -F'|' '/^\| [0-9]+/ {printf "%s|%s|%s|%s\n",$2,$3,$7,$8}'
