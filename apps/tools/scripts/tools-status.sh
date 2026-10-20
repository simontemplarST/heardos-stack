# @id: script_1792800000002_toolsstat
# @title: Maintenance console - status reports
# @description: Read-only reports for the Maintenance app (OliveTin): containers, disk, memory, LAN certificate, scheduled jobs, county documents, Wikipedia download, Nebula, journal errors, pending updates
#!/usr/bin/env bash
# tools-status.sh WHAT — one short report per keyword. Nothing here changes anything. Docker's own {{...}} format
# strings live in this file because OliveTin would read them as its argument templates.
set -u
S=/root/runtipi/app-data/_user/cronmaster/scripts
D=/root/runtipi/app-data/_user/cronmaster/data
case "${1:-help}" in
  containers)
    docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | sort -k1,1 | sed '1!{/^NAMES/d}'
    echo; echo "not running:"; docker ps -a --filter status=exited --filter status=dead --filter status=restarting --format '  {{.Names}}  {{.Status}}' | grep . || echo "  (none)" ;;
  containers-json)   # the Maintenance app's container list (OliveTin entity file): one JSON object per line
    docker ps -a --format '{{json .}}' > /etc/OliveTin/entities/containers.json.tmp && mv /etc/OliveTin/entities/containers.json.tmp /etc/OliveTin/entities/containers.json
    echo "$(wc -l < /etc/OliveTin/entities/containers.json) containers listed" ;;
  disk)
    df -h -x tmpfs -x devtmpfs -x overlay 2>/dev/null | sed '1!{/^Filesystem/d}'
    echo; echo "biggest app data folders:"; du -sh /root/runtipi/app-data/_user/* /root/runtipi/media/data 2>/dev/null | sort -rh | head -12 | sed 's/^/  /'
    echo; docker system df 2>/dev/null ;;
  memory)
    uptime; echo; free -h; echo; echo "top memory users:"; docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' | sort -k2 -rh | head -12 ;;
  cert)
    for c in /root/runtipi/traefik/tls/heardos-lan.crt; do echo "$c"; step certificate inspect --short "$c" 2>/dev/null || openssl x509 -in "$c" -noout -subject -dates; done
    echo; echo "public sites (from here):"; for h in auth dash photos; do printf '  %-8s ' "$h"; curl -s -o /dev/null -m 8 -w '%{http_code} %{ssl_verify_result}\n' "https://$h.example.com/" || echo "unreachable"; done ;;
  jobs)
    echo "CronMaster jobs (host crontab):"; crontab -l | awk '/^# id: /{id=$3} /^[^#]/{print "  " id "\t" $1" "$2" "$3" "$4" "$5}' | column -t -s $'\t'
    echo; echo "last runs:"; for d in "$D"/logs/*/; do f=$(ls -t "$d" 2>/dev/null | head -1); [ -n "$f" ] && printf '  %-28s %s  %s\n' "$(basename "$d")" "${f%.log}" "$(grep -m1 '^Status' "$d/$f" | awk '{print $3}')"; done ;;
  mower)
    M="/root/runtipi/media/data/documents/Example County meetings"
    echo "documents per board (pdf+docx):"; for b in "$M"/*/; do printf '  %-36s %5d\n' "$(basename "$b")" "$(find "$b" -type f \( -name '*.pdf' -o -name '*.docx' \) | wc -l)"; done
    echo; echo "newest:"; find "$M" -type f \( -name '*.pdf' -o -name '*.docx' \) -printf '%TY-%Tm-%Td  %P\n' | sort -r | head -8 | sed 's/^/  /' ;;
  archive)
    docker exec archivebox archivebox status 2>/dev/null | grep -vE '^\s*$|Hint|archivebox list' | head -20 ;;
  kiwix)
    systemctl is-active kiwix-fetch >/dev/null 2>&1 && { echo "download running:"; systemctl status kiwix-fetch --no-pager 2>/dev/null | tail -5; } || echo "no download running"
    echo; ls -lh /root/apps/kiwix/data/*.zim* 2>/dev/null | awk '{print "  " $5 "  " $9}'; echo; docker ps --filter name=kiwix --format '  kiwix container: {{.Status}}' ;;
  nebula)
    systemctl status nebula --no-pager 2>/dev/null | sed -n '1,4p'; echo; ip -4 addr show nebula 2>/dev/null | grep inet | sed 's/^/  /'
    for h in 10.42.0.1 10.42.0.10; do printf '  %-12s ' "$h"; ping -c 1 -W 2 "$h" >/dev/null 2>&1 && echo reachable || echo "NO ANSWER"; done ;;
  journal)
    journalctl -p err -n 40 --no-pager -o short 2>/dev/null ;;
  updates)
    apt-get -s upgrade 2>/dev/null | grep -E '^Inst' | awk '{print "  " $2 "  " $3 " -> " $4}' | head -40 || true
    echo; echo "$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst') packages can be upgraded (apt); container images are updated from Portainer" ;;
  nextcloud)
    docker exec -u www-data nextcloud php occ status; echo; docker exec -u www-data nextcloud php occ update:check 2>&1 | head -10 ;;
  *) echo "usage: tools-status.sh containers|containers-json|disk|memory|cert|jobs|mower|archive|kiwix|nebula|journal|updates|nextcloud" >&2; exit 2 ;;
esac
