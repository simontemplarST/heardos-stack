#!/usr/bin/env bash
# close-lan-ports.sh — set openPort=false on Paperless and Homebox by resubmitting their
# FULL Runtipi form (every store form field, correct types) so app.env is regenerated with nothing blank.
# Runs the API call on CT 108 so secrets never leave the box. Verify afterwards with security-check.sh.
#   bash close-lan-ports.sh homebox      (do Homebox first; it has the fewest fields)
#   bash close-lan-ports.sh paperless-ngx
set -euo pipefail
set -a; . ~/.config/credentials.env; set +a
APP="${1:?homebox | paperless-ngx}"
ssh -o BatchMode=yes root@192.168.10.20 "RUNTIPI_ADMIN_USER='$RUNTIPI_ADMIN_USER' RUNTIPI_ADMIN_PASSWORD='$RUNTIPI_ADMIN_PASSWORD' python3 - '$APP'" <<'PY'
import json, os, sys, urllib.request, http.cookiejar, glob, time, subprocess
app=sys.argv[1]; BASE='http://192.168.10.20'; H={'Content-Type':'application/json','Origin':BASE,'Referer':BASE+'/'}
cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def call(m,p,d=None):
    r=urllib.request.Request(BASE+p,method=m,data=json.dumps(d).encode() if d is not None else None,headers=H)
    try:
        with op.open(r,timeout=180) as x: b=x.read(); return x.status,(json.loads(b) if b else {})
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
print('login',call('POST','/api/auth/login',{'username':os.environ['RUNTIPI_ADMIN_USER'],'password':os.environ['RUNTIPI_ADMIN_PASSWORD']})[0])
cfg=json.load(open(f'/root/runtipi/apps/migrated/{app}/config.json'))
env=dict(l.rstrip('\n').split('=',1) for l in open(f'/root/runtipi/app-data/migrated/{app}/app.env') if '=' in l)
form={'openPort': False, 'exposed': False, 'exposedLocal': True, 'isVisibleOnGuestDashboard': False}   # exposedLocal MUST stay true: it owns the Traefik labels that the public routers' @docker services come from (2026-10-15 outage)
for f in cfg.get('form_fields',[]):
    k=f['env_variable']; v=env.get(k,'')
    if f.get('type')=='boolean': v=(str(v).lower()=='true')
    form[k]=v
print('form keys:', sorted(form))
body=dict(form); body['form']=dict(form)   # Runtipi reads the fields at the TOP level (verified 2026-10-15); nested 'form' is ignored
s,d=call('PATCH',f'/api/app-lifecycle/{app}:migrated/update-config',body)
print('update-config',s,str(d)[:200])
if s==200:
    print('restart',call('POST',f'/api/app-lifecycle/{app}:migrated/restart',{})[0]); time.sleep(45)
    print(subprocess.run(['bash','-c',f'docker ps --format "{{{{.Names}}}} {{{{.Status}}}}" | grep {app}; ss -ltn | grep -E ":(8012|7745) " || echo "ports 8012/7745: closed"'],capture_output=True,text=True).stdout)
PY
