#!/usr/bin/env python3
"""60-baikal.py — manage Baïkal users, calendars and shares straight in its sqlite DB (its UI cannot share). Run ON THE HOST.
  user NAME EMAIL DISPLAYNAME [-|PASSWORD]        create/update a Baïkal user. Password from $HEARDOS_PASSWORD_FILE, or
                                                  `-` = one line on stdin (PASSWORD on argv still works but is visible
                                                  in ps; avoid it). None given: generated and handed back via
                                                  $HEARDOS_PASSWORD_FILE or a HEARDOS_OUTPUT line, never printed
  calendar OWNER URI NAME [--color #RRGGBB] [--todo] [--coowner USER]   create a calendar (VEVENT, or VTODO list with --todo)
  share OWNER URI WITH_USER {ro|rw|owner} [--name N] [--color #RRGGBB]  share an existing calendar
  ls                                              show every principal's calendars as sabre sees them
Back up db.sqlite first (the script does it too). Container must be installed via 50-app.sh baikal and the wizard finished."""
import hashlib, os, secrets, shutil, sqlite3, sys, time, uuid

ROOT = os.environ.get("HEARDOS_ROOT", "/opt/heardos"); DB = f"{ROOT}/data/baikal/specific/db/db.sqlite"; REALM = "BaikalDAV"
if not os.path.exists(DB): raise SystemExit(f"{DB} not found — finish the Baïkal web wizard first")
shutil.copy2(DB, DB + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
c = sqlite3.connect(DB); a = sys.argv[1:]
def opt(flag, default=None):
    if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
    return default
def flagged(flag):
    if flag in a: a.remove(flag); return True
    return False
ACCESS = {"owner": 1, "ro": 2, "rw": 3}

if a[:1] == ["user"]:
    _, name, email, disp = a[:4]; arg = a[4] if len(a) > 4 else None
    f = os.environ.get("HEARDOS_PASSWORD_FILE")
    if arg == "-": pw = sys.stdin.readline().rstrip("\n") or sys.exit("empty password on stdin")
    elif arg: pw = arg; print("warning: password given on the command line; prefer HEARDOS_PASSWORD_FILE or '-'", file=sys.stderr)
    elif f and os.path.exists(f) and os.path.getsize(f): pw = open(f).read().strip()
    else: pw = None
    generated = pw is None
    if generated: pw = secrets.token_urlsafe(14)
    dig = hashlib.md5(f"{name}:{REALM}:{pw}".encode()).hexdigest()
    if c.execute("select 1 from users where username=?", (name,)).fetchone():
        c.execute("update users set digesta1=? where username=?", (dig, name)); print(f"user {name}: password set")
    else:
        c.execute("insert into users(username,digesta1) values(?,?)", (name, dig))
        c.execute("insert into principals(uri,email,displayname) values(?,?,?)", (f"principals/{name}", email, disp))
        cid = c.execute("insert into calendars(synctoken,components) values(1,'VEVENT,VTODO')").lastrowid
        c.execute("insert into calendarinstances(calendarid,principaluri,access,displayname,uri,calendarorder,transparent,share_invitestatus) values(?,?,1,?,'default',0,0,2)", (cid, f"principals/{name}", disp))
        print(f"user {name} created with calendar 'default'")
    if generated:   # hand it back without a clear-text print (the installer masks HEARDOS_OUTPUT lines)
        if f:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, pw.encode()); os.close(fd); print(f"  password written to {f}")
        else: print(f"HEARDOS_OUTPUT BAIKAL_{name.upper().replace('-', '_')}_PASSWORD={pw}")
elif a[:1] == ["calendar"]:
    color = opt("--color", "#2E7D32") + "FF"; todo = flagged("--todo"); co = opt("--coowner")
    _, owner, uri, name = a[:4]
    cid = c.execute("insert into calendars(synctoken,components) values(1,?)", ("VTODO" if todo else "VEVENT",)).lastrowid
    for p in [owner] + ([co] if co else []):
        c.execute("insert into calendarinstances(calendarid,principaluri,access,displayname,uri,calendarorder,calendarcolor,transparent,share_invitestatus) values(?,?,1,?,?,0,?,0,2)", (cid, f"principals/{p}", name, uri, color))
    print(f"calendar {name} ({'VTODO' if todo else 'VEVENT'}) id {cid} owned by {owner}{' and ' + co if co else ''}")
elif a[:1] == ["share"]:
    name = opt("--name"); color = opt("--color")
    _, owner, uri, with_user, mode = a[:5]
    row = c.execute("select calendarid,displayname,calendarcolor from calendarinstances where principaluri=? and uri=?", (f"principals/{owner}", uri)).fetchone() or sys.exit("no such calendar")
    cid, dn, col = row; email = c.execute("select email from principals where uri=?", (f"principals/{with_user}",)).fetchone()[0]
    acc = ACCESS[mode]; inst_uri = uri if acc == 1 else str(uuid.uuid4())
    if c.execute("select 1 from calendarinstances where calendarid=? and principaluri=?", (cid, f"principals/{with_user}")).fetchone():
        c.execute("update calendarinstances set access=?, displayname=coalesce(?,displayname), calendarcolor=coalesce(?,calendarcolor) where calendarid=? and principaluri=?", (acc, name, (color + "FF") if color else None, cid, f"principals/{with_user}"))
    else:
        c.execute("insert into calendarinstances(calendarid,principaluri,access,displayname,uri,calendarorder,calendarcolor,transparent,share_href,share_displayname,share_invitestatus) values(?,?,?,?,?,0,?,?,?,?,2)",
                  (cid, f"principals/{with_user}", acc, name or dn, inst_uri, (color + "FF") if color else col, 1 if acc == 2 else 0, None if acc == 1 else f"mailto:{email}", None if acc == 1 else with_user))
    print(f"{owner}/{uri} shared with {with_user} as {mode}")
elif a[:1] == ["ls"]:
    for p, in c.execute("select uri from principals where uri like 'principals/%' order by uri"):
        rows = c.execute("select ci.displayname,ci.access,c.components,(select count(*) from calendarobjects o where o.calendarid=c.id) from calendarinstances ci join calendars c on c.id=ci.calendarid where ci.principaluri=?", (p,)).fetchall()
        print(p + ": " + ", ".join(f"{n} ({ {1:'owner',2:'ro',3:'rw'}.get(acc, acc)}, {comp}, {cnt})" for n, acc, comp, cnt in rows))
else: raise SystemExit(__doc__)
c.commit()
