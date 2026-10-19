#!/usr/bin/env python3
"""ics-feeds-sync: import read-only ICS feeds into Baikal calendars (owned by the `feeds` user) and share them
read-only with family members. Stdlib only. Config: ics-feeds.json next to this script.

{
  "baikal_url": "http://dav.192-168-10-20.sslip.io/dav.php",
  "baikal_db": "/root/runtipi/app-data/migrated/baikal/specific/db/db.sqlite",
  "user": "feeds", "password": "...",
  "share_with": {"principals/alice": "mailto:admin@example.net", "principals/bob": "mailto:bob@example.net"},
  "strip_alarms": true,
  "feeds": [
    {"id": "kids-practice", "label": "Kids practice", "url": "https://...ics", "color": "#F5A623"},
    {"id": "springfield-events", "label": "Springfield events", "color": "#00897B", "days_back": 7, "days_ahead": 180,   # optional window
     "sources": [{"label": "Discover Springfield", "url": "https://calendar.google.com/calendar/ical/.../public/basic.ics"}]},
                                                      # days_back/days_ahead (feed- or source-level) drop events outside
                                                      # today-back..today+ahead; recurring events are kept. Omit = import all.
    {"id": "local-government", "label": "Local government", "color": "#6D4C41",
     "share_with": ["principals/alice"],                      # optional: subset of the global share_with
     "sources": [ {"label": "Example County", "url": "https://...catID=14&feed=calendar"},
                  {"label": "Springfield", "type": "ai1ec-agenda", "url": "https://www.city.example.org"} ]}
    {"id": "meal-plan", "label": "Meals", "color": "#FB8C00",
     "sources": [{"label": "Meals", "type": "mealie-mealplan", "url": "http://127.0.0.1:9925", "public_url": "https://mealie.example.com",
                  "group_slug": "home", "token": "<mealie api token>", "days_back": 7, "days_ahead": 60}]},
    {"id": "work-alice", "label": "Work", "url": "https://outlook.office365.com/owa/calendar/.../reachcalendar.ics",
     "target": {"principal": "principals/alice", "uri": "default"}}   # write INTO an existing user calendar instead of
                                                                        # a separate read-only one; only our own events are
                                                                        # ever deleted there (tracked in the state file)
  ]   # several sources merged into one calendar; type ai1ec-agenda scrapes a WordPress All-in-One
      # Event Calendar agenda view (used because city.example.org' own ICS export is broken server-side)
  ]
}
Run:  ics-feeds-sync.py              sync all feeds
      ics-feeds-sync.py --feed ID    sync one feed (one CronMaster job per feed)
      ics-feeds-sync.py --remove ID  delete a feed calendar (and its shares) that is no longer wanted
      ics-feeds-sync.py --install-jobs   (re)write one CronMaster job per feed in root's crontab
                                         (ids ics-feed-<id>, every 15 min, logged via cron-log-wrapper.sh)
"""
import base64, hashlib, json, os, re, sqlite3, sys, time, urllib.request, urllib.error, urllib.parse, uuid
from xml.etree import ElementTree as ET
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "ics-feeds.json")))
STATE_DIR = os.path.join(HERE, ".ics-feeds-state"); os.makedirs(STATE_DIR, exist_ok=True)
BASE = CFG["baikal_url"].rstrip("/") + "/calendars/" + CFG["user"] + "/"
AUTH = "Basic " + base64.b64encode(f'{CFG["user"]}:{CFG["password"]}'.encode()).decode()
UA = "ics-feeds-sync/1.0 (+example.com)"

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

MAX_BYTES = int(CFG.get("max_feed_mb", 20)) * 1024 * 1024   # a feed/page larger than this is refused, not buffered

def read_capped(r, what):
    data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise SystemExit(f"{what}: response larger than {MAX_BYTES >> 20} MB, refused")
    return data

def dav(method, url, body=None, headers=None):
    h = {"Authorization": AUTH, "User-Agent": UA}; h.update(headers or {})
    req = urllib.request.Request(url, data=body.encode() if isinstance(body, str) else body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, read_capped(r, url).decode(errors="replace")
    except urllib.error.HTTPError as e: return e.code, e.read(65536).decode(errors="replace")

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r: return read_capped(r, url).decode("utf-8", errors="replace")

def unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text)

def blocks(lines, name):
    """yield lists of lines for each BEGIN:name ... END:name (non-nested)."""
    out, cur = [], None
    for ln in lines:
        if ln == f"BEGIN:{name}": cur = [ln]
        elif ln == f"END:{name}" and cur is not None: cur.append(ln); out.append(cur); cur = None
        elif cur is not None: cur.append(ln)
    return out

def strip_component(lines, name):
    out, depth = [], 0
    for ln in lines:
        if ln == f"BEGIN:{name}": depth += 1; continue
        if ln == f"END:{name}" and depth: depth -= 1; continue
        if not depth: out.append(ln)
    return out

def prop(lines, key):
    for ln in lines:
        if ln.split(":", 1)[0].split(";", 1)[0].upper() == key: return ln
    return None

def fold(line):
    """RFC 5545 line folding at <=75 octets, never splitting a UTF-8 sequence."""
    b = line.encode(); out = []
    while len(b) > 74:
        cut = 74
        while cut > 1 and (b[cut] & 0xC0) == 0x80: cut -= 1   # back off to a character boundary
        out.append(b[:cut]); b = b" " + b[cut:]
    out.append(b); return b"\r\n".join(out).decode()

# ---------------------------------------------------------------------------------------------
# Source adapters. A source may carry "type": "ai1ec-agenda" (WordPress All-in-One Event Calendar
# whose ICS export is broken, e.g. city.example.org). We scrape the agenda JSON view and emit ICS.
# ---------------------------------------------------------------------------------------------
import html as _html
from datetime import datetime, timedelta

MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}

def _ai1ec_time(txt, day):
    """'Oct 13 @ 4:30 pm' | 'Oct 13 @ 4:30 pm – 6:00 pm' | 'all-day' | 'Oct 13 – Oct 15' -> (start, end, allday)"""
    t = re.sub(r"\s+", " ", _html.unescape(txt)).strip().replace("–", "-").replace("—", "-")
    if "all-day" in t.lower():
        return day, None, True
    m = re.search(r"(?:@ |^)(\d{1,2}(?::\d{2})? ?[ap]m)(?: - (?:\w{3} \d{1,2} @ )?(\d{1,2}(?::\d{2})? ?[ap]m))?", t, re.I)
    if not m: return day, None, True
    def hm(x):
        x = x.lower().replace(" ", ""); ap = x[-2:]; x = x[:-2]; h, _, mi = x.partition(":"); h = int(h) % 12 + (12 if ap == "pm" else 0)
        return int(h), int(mi or 0)
    h1, m1 = hm(m.group(1)); start = day.replace(hour=h1, minute=m1)
    end = None
    if m.group(2):
        h2, m2 = hm(m.group(2)); end = day.replace(hour=h2, minute=m2)
        if end <= start: end += timedelta(days=1)
    return start, end, False

def fetch_ai1ec_agenda(base, max_pages=8, tzid="America/Chicago"):
    """Walk the agenda view forward from today and return an ICS string."""
    events, seen = [], set()
    url = f"{base.rstrip('/')}/calendar/?ai1ec=action~agenda&request_type=json"   # today forward
    for off in range(max_pages):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r: page = json.loads(read_capped(r, url).decode("utf-8", "replace"))
        h = re.sub(r"\s+", " ", _html.unescape(page.get("html", "")))
        days = re.split(r'<div class="ai1ec-date(?=[ "])', h)[1:]
        for d in days:
            md = re.search(r"exact_date~(\d{1,2})-(\d{1,2})-(\d{4})", d); me = re.search(r"exact_date~(\d{9,11})\b", d)
            if md: day = datetime(int(md.group(3)), int(md.group(1)), int(md.group(2)))
            elif me: dd = datetime.utcfromtimestamp(int(me.group(1))).date(); day = datetime(dd.year, dd.month, dd.day)
            else: continue
            for ev in re.split(r'<div class="ai1ec-event ai1ec-event-id-', d)[1:]:
                ids = re.match(r"(\d+) ai1ec-event-instance-id-(\d+)", ev)
                if not ids: continue
                key = (ids.group(1), ids.group(2))
                if key in seen: continue
                seen.add(key)
                title = re.sub(r"\s+", " ", re.search(r'ai1ec-event-title">(.*?)</span>', ev, re.S).group(1)).strip() if re.search(r'ai1ec-event-title">', ev) else "Event"
                title = re.sub(r"<[^>]+>", "", title).strip()
                tm = re.search(r'ai1ec-event-time">(.*?)</div>', ev, re.S); tm = tm.group(1) if tm else ""
                start, end, allday = _ai1ec_time(tm, day)
                dend = re.search(r'data-end="(\d{4}-\d\d-\d\d)T(\d\d):(\d\d)', ev)
                if end is None and dend and not allday:
                    end = datetime(*map(int, dend.group(1).split("-")), int(dend.group(2)), int(dend.group(3)))
                    if end <= start: end = start + timedelta(hours=1)
                desc = re.search(r'ai1ec-event-description">(.*?)</div>', ev, re.S)
                desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc.group(1))).strip() if desc else ""
                loc = re.search(r'ai1ec-event-location">(.*?)</div>', ev, re.S)
                loc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", loc.group(1))).strip() if loc else ""
                link = re.search(r'href="(https?://[^"]+/event/[^"]+)"', ev); link = link.group(1) if link else ""
                esc = lambda v: v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
                lines = ["BEGIN:VEVENT", f"UID:ai1ec-{key[0]}-{key[1]}@{urllib.parse.urlparse(base).hostname}", f"SUMMARY:{esc(title)}"]
                if allday:
                    lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{start + timedelta(days=1):%Y%m%d}"]
                else:
                    lines += [f"DTSTART;TZID={tzid}:{start:%Y%m%dT%H%M%S}", f"DTEND;TZID={tzid}:{(end or start + timedelta(hours=1)):%Y%m%dT%H%M%S}"]
                if desc: lines.append(f"DESCRIPTION:{esc(desc)}")
                if loc: lines.append(f"LOCATION:{esc(loc)}")
                if link: lines.append(f"URL:{link}")
                lines.append("END:VEVENT"); events.append("\r\n".join(lines))
        nxt = re.search(r'<a[^>]*class="[^"]*ai1ec-next-page(?![^"]*ai1ec-disabled)[^"]*"[^>]*href="([^"]+)"', h) or \
              re.search(r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*ai1ec-next-page(?![^"]*ai1ec-disabled)[^"]*"', h)
        if not nxt or not days: break
        # follow the next-page link only on the calendar's own site (scheme + host): the page is untrusted HTML
        nurl = urllib.parse.urljoin(url, nxt.group(1)); a, b = urllib.parse.urlsplit(base), urllib.parse.urlsplit(nurl)
        if (a.scheme, a.netloc.lower()) != (b.scheme, b.netloc.lower()):
            log(f"  ai1ec agenda {base}: next-page link to another site ignored ({b.scheme}://{b.netloc})"); break
        url = nurl; url += ("&" if "?" in url else "?") + "request_type=json" if "request_type=json" not in url else ""
    log(f"  ai1ec agenda {base}: {len(events)} events from {off + 1} page(s)")
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//example.com//ai1ec-agenda//EN\r\n" + "\r\n".join(events) + "\r\nEND:VCALENDAR\r\n"

def fetch_mealie_mealplan(src):
    """Mealie meal plan -> ICS (one all-day event per plan entry). src: {url, token, public_url?, days_back?, days_ahead?}"""
    from datetime import date
    base = src["url"].rstrip("/"); pub = src.get("public_url", base).rstrip("/")
    start = date.today() - timedelta(days=int(src.get("days_back", 7))); end = date.today() + timedelta(days=int(src.get("days_ahead", 60)))
    url = f"{base}/api/households/mealplans?start_date={start:%Y-%m-%d}&end_date={end:%Y-%m-%d}&perPage=-1"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Authorization": "Bearer " + src["token"], "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r: data = json.loads(read_capped(r, url).decode())
    items = data.get("items", data if isinstance(data, list) else [])
    gslug = src.get("group_slug", "")
    esc = lambda v: str(v).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")
    out = []
    for it in items:
        rec = it.get("recipe") or {}
        kind = (it.get("entryType") or "meal").capitalize()
        name = rec.get("name") or it.get("title") or kind
        d = datetime.strptime(it["date"], "%Y-%m-%d")
        desc = [x for x in (it.get("text"), rec.get("description")) if x]
        link = f"{pub}/g/{gslug}/r/{rec['slug']}" if rec.get("slug") and gslug else ""
        lines = ["BEGIN:VEVENT", f"UID:mealie-plan-{it['id']}@{urllib.parse.urlparse(base).hostname}",
                 f"SUMMARY:{esc(kind + ': ' + name)}", f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                 "TRANSP:TRANSPARENT", f"CATEGORIES:{esc(kind)}"]
        if desc: lines.append(f"DESCRIPTION:{esc(chr(10).join(desc))}")
        if link: lines.append(f"URL:{link}")
        lines.append("END:VEVENT"); out.append("\r\n".join(lines))
    log(f"  mealie meal plan {base}: {len(out)} entries {start}..{end}")
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//example.com//mealie-mealplan//EN\r\n" + "\r\n".join(out) + ("\r\n" if out else "") + "END:VCALENDAR\r\n"

def fetch_source(src):
    if src.get("type") == "ai1ec-agenda": return fetch_ai1ec_agenda(src["url"])
    if src.get("type") == "mealie-mealplan": return fetch_mealie_mealplan(src)
    return fetch(src["url"])

def ensure_target(feed):
    """Feed writes into somebody's existing calendar: give the `feeds` user a hidden read-write share of it (DB)
    and return the feeds user's URL for that share."""
    t = feed["target"]; inst_uri = "target-" + feed["id"]
    c = sqlite3.connect(CFG["baikal_db"])
    row = c.execute("select calendarid from calendarinstances where principaluri=? and uri=? and access=1", (t["principal"], t["uri"])).fetchone()
    if not row: raise SystemExit(f"target calendar {t['principal']}/{t['uri']} not found")
    calid = row[0]
    if not c.execute("select 1 from calendarinstances where calendarid=? and principaluri=?", (calid, f"principals/{CFG['user']}")).fetchone():
        c.execute("""insert into calendarinstances(calendarid,principaluri,access,displayname,uri,description,calendarorder,calendarcolor,
                     timezone,transparent,share_href,share_displayname,share_invitestatus) values(?,?,3,?,?,?,0,NULL,NULL,0,?,?,2)""",
                  (calid, f"principals/{CFG['user']}", f"[{feed['label']} -> {t['principal'].split('/')[-1]}]", inst_uri,
                   f"ics-feeds-sync writes feed {feed['id']} here", f"principal:principals/{CFG['user']}", "Imported schedules"))
        log(f"[{feed['id']}] feeds user granted write access to {t['principal']}/{t['uri']}")
    c.commit(); c.close()
    return BASE + inst_uri + "/"

def ensure_calendar(feed):
    """MKCALENDAR for the feeds user, then (DB) share read-only with each family principal."""
    if feed.get("target"): return ensure_target(feed)
    url = BASE + feed["id"] + "/"
    st, _ = dav("PROPFIND", url, headers={"Depth": "0"})
    if st == 404:
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:A="http://apple.com/ns/ical/">
 <D:set><D:prop><D:displayname>{feed['label']}</D:displayname>
 <C:calendar-description>Imported read-only: {", ".join(src['label'] for src in sources_of(feed))}</C:calendar-description>
 <C:supported-calendar-component-set><C:comp name="VEVENT"/></C:supported-calendar-component-set>
 <A:calendar-color>{feed.get('color','#607D8B')}FF</A:calendar-color></D:prop></D:set></C:mkcalendar>"""
        st, txt = dav("MKCALENDAR", url, body, {"Content-Type": "application/xml"})
        if st not in (201, 200): raise SystemExit(f"MKCALENDAR {feed['id']} failed: {st} {txt[:200]}")
        log(f"[{feed['id']}] calendar created")
    elif st not in (207, 200): raise SystemExit(f"PROPFIND {url} -> {st}")
    # read-only shares, accepted, straight in the DB (sabre schema; same as FamCal)
    c = sqlite3.connect(CFG["baikal_db"])
    row = c.execute("select calendarid from calendarinstances where principaluri=? and uri=?", (f"principals/{CFG['user']}", feed["id"])).fetchone()
    if not row: raise SystemExit(f"calendar {feed['id']} not found in DB after MKCALENDAR")
    calid = row[0]; color = feed.get("color", "#607D8B") + "FF"
    wanted = feed.get("share_with") or list(CFG["share_with"])
    c.execute("delete from calendarinstances where calendarid=? and access!=1 and principaluri not in (%s)" % ",".join("?"*len(wanted)), (calid, *wanted))
    for principal, href in CFG["share_with"].items():
        if principal not in wanted: continue
        if c.execute("select 1 from calendarinstances where calendarid=? and principaluri=?", (calid, principal)).fetchone():
            c.execute("update calendarinstances set displayname=?, calendarcolor=? where calendarid=? and principaluri=?", (feed["label"], color, calid, principal))
        else:
            c.execute("""insert into calendarinstances(calendarid,principaluri,access,displayname,uri,description,calendarorder,calendarcolor,
                         timezone,transparent,share_href,share_displayname,share_invitestatus) values(?,?,2,?,?,?,0,?,NULL,1,?,?,2)""",
                      (calid, principal, feed["label"], str(uuid.uuid4()), f"Imported read-only feed: {feed['label']}", color, href, principal.split("/")[-1]))
            log(f"[{feed['id']}] shared read-only with {principal}")
    c.commit(); c.close()
    return url

def existing_resources(url):
    st, txt = dav("PROPFIND", url, '<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>', {"Depth": "1", "Content-Type": "application/xml"})
    if st != 207: raise SystemExit(f"PROPFIND {url} -> {st}")
    names = set()
    for href in ET.fromstring(txt).iter("{DAV:}href"):
        h = href.text or ""
        if h.endswith(".ics"): names.add(h.rsplit("/", 1)[-1])
    return names

def sources_of(feed):
    """A feed is either {"label","url"} or {"sources":[{"label","url"},...]} merged into one calendar."""
    return feed.get("sources") or [{"label": feed["label"], "url": feed["url"]}]

def _dtstart_date(ev):
    """date of DTSTART (DATE or DATE-TIME; time zone ignored, day precision is all the window needs)."""
    l = prop(ev, "DTSTART")
    if not l: return None
    m = re.search(r":(\d{8})", l)
    return date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8])) if m else None

def in_window(ev, window):
    """window = (days_back, days_ahead) or None. Recurring events are kept when they start before the window ends."""
    if not window: return True
    d = _dtstart_date(ev)
    if d is None: return True
    lo, hi = date.today() - timedelta(days=int(window[0])), date.today() + timedelta(days=int(window[1]))
    if d > hi: return False
    rr = prop(ev, "RRULE")
    if rr:
        # a series that already ended before the window is as stale as a single past event (and a fully-excluded
        # one makes sabre/dav throw NoInstancesException on PUT)
        m = re.search(r"UNTIL=(\d{8})", rr)
        if m and date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8])) < lo: return False
        return True
    if prop(ev, "RDATE"): return True
    return d >= lo

def window_of(src, feed):
    """Per-source days_back/days_ahead, falling back to the feed's; absent = import everything (old behaviour).
    Big public Google calendars carry years of history, so window them (e.g. 7 back / 180 ahead)."""
    for o in (src, feed):
        if o and ("days_back" in o or "days_ahead" in o): return (o.get("days_back", 7), o.get("days_ahead", 365))
    return None

def build_objects(feed, ics, label=None, window=None):
    label = label or feed["label"]
    lines = [l for l in unfold(ics).split("\n") if l]
    tz = blocks(lines, "VTIMEZONE")
    tzlines = [l for b in tz for l in b]
    objs = {}
    for ev in blocks(lines, "VEVENT"):
        if not in_window(ev, window): continue
        if CFG.get("strip_alarms", True): ev = strip_component(ev, "VALARM")
        uidl = prop(ev, "UID"); uid = uidl.split(":", 1)[1] if uidl else hashlib.sha1("\n".join(ev).encode()).hexdigest()
        # Feeds (Google especially) set DTSTAMP to the download time, which would make every run look changed.
        # Replace it with something deterministic so unchanged events hash the same on every run.
        src = prop(ev, "LAST-MODIFIED") or prop(ev, "CREATED")
        stamp = src.split(":", 1)[1] if src and src.split(":", 1)[1].endswith("Z") else "19700101T000000Z"
        ev = [l for l in ev if l.split(":", 1)[0].split(";", 1)[0].upper() != "DTSTAMP"]
        ev.insert(1, "DTSTAMP:" + stamp)
        new = []
        for ln in ev:
            key = ln.split(":", 1)[0].split(";", 1)[0].upper()
            if key == "SUMMARY":
                head, val = ln.split(":", 1); new.append(f"{head}:{label}: {val}" if val.strip() else f"{head}:{label}")
            elif key in ("ORGANIZER", "ATTENDEE"):
                continue  # keep Apple from treating imported items as invitations we must answer
            else: new.append(ln)
        if not prop(new, "SUMMARY"): new.insert(1, f"SUMMARY:{label}")
        objs.setdefault(uid, []).extend(new)
    out = {}
    for uid, evlines in objs.items():
        name = hashlib.sha1((label + "|" + uid).encode()).hexdigest() + ".ics"  # label keeps UIDs from different sources apart
        body = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//example.com//ics-feeds-sync//EN", f"X-WR-CALNAME:{feed['label']}"] + tzlines + evlines + ["END:VCALENDAR"]
        out[name] = "\r\n".join(fold(l) for l in body) + "\r\n"
    return out

def sync(feed):
    url = ensure_calendar(feed)
    statef = os.path.join(STATE_DIR, feed["id"] + ".json")
    state = json.load(open(statef)) if os.path.exists(statef) else {}
    objs, failed_sources = {}, []
    for src in sources_of(feed):
        try:
            ics = fetch_source(src)
            if "BEGIN:VCALENDAR" not in ics: raise ValueError("not an ICS payload")
            objs.update(build_objects(feed, ics, src["label"], window=None if src.get("type") == "mealie-mealplan" else window_of(src, feed)))
        except Exception as e:
            log(f"[{feed['id']}] source '{src['label']}' FAILED, keeping its existing events: {e}"); failed_sources.append(src["label"])
    if failed_sources and len(failed_sources) == len(sources_of(feed)): return False
    remote = existing_resources(url)
    put = skipped = deleted = failed = 0
    for name, body in objs.items():
        h = hashlib.sha1(body.encode()).hexdigest()
        if state.get(name) == h and name in remote: skipped += 1; continue
        st, txt = dav("PUT", url + name, body, {"Content-Type": "text/calendar; charset=utf-8"})
        if st in (200, 201, 204): state[name] = h; put += 1
        else: failed += 1; log(f"[{feed['id']}] PUT {name} -> {st} {txt[:160]}")
    stale = (set(state) if feed.get("target") else (remote | set(state))) - set(objs)  # target: only what we created
    if failed_sources:  # cannot tell which resources belonged to a failed source by name, so delete nothing this run
        log(f"[{feed['id']}] skipping deletions because a source failed"); stale = set()
    for name in stale:
        st, _ = dav("DELETE", url + name)
        if st in (200, 204, 404): state.pop(name, None); deleted += 1
        else: failed += 1; log(f"[{feed['id']}] DELETE {name} -> {st}")
    json.dump(state, open(statef, "w"))
    log(f"[{feed['id']}] events={len(objs)} put={put} unchanged={skipped} deleted={deleted} failed={failed}")
    return failed == 0 and not failed_sources

def remove(fid):
    feed = next((f for f in CFG.get("feeds", []) if f["id"] == fid), None)
    if feed and feed.get("target"):
        url = BASE + "target-" + fid + "/"; sf = os.path.join(STATE_DIR, fid + ".json")
        state = json.load(open(sf)) if os.path.exists(sf) else {}
        for name in state: dav("DELETE", url + name)
        st, _ = dav("DELETE", url)   # sabre deletes only the sharee instance for non-owners
        log(f"removed {fid}: {len(state)} imported events deleted from target, share dropped (DELETE -> {st})")
        os.path.exists(sf) and os.remove(sf); return
    url = BASE + fid + "/"
    c = sqlite3.connect(CFG["baikal_db"])
    row = c.execute("select calendarid from calendarinstances where principaluri=? and uri=?", (f"principals/{CFG['user']}", fid)).fetchone()
    if row:
        c.execute("delete from calendarinstances where calendarid=? and access!=1", (row[0],)); c.commit()
    c.close()
    st, _ = dav("DELETE", url); log(f"removed {fid}: DELETE -> {st}")
    sf = os.path.join(STATE_DIR, fid + ".json"); os.path.exists(sf) and os.remove(sf)

def install_jobs():
    """One CronMaster job per feed. Blocks are '# id: ics-feed-<id>' + cron line; other jobs are left alone."""
    import subprocess
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines()
    out, skip = [], False
    for ln in cur:
        if ln.startswith("# id: ics-feed-") or ln.startswith("# id: ics-feeds"): skip = True; continue
        if skip and ln.strip() and not ln.startswith("#"): skip = False; continue
        skip = False; out.append(ln)
    wrapper = os.path.join(os.path.dirname(HERE), "data", "cron-log-wrapper.sh")
    every = CFG.get("cron_schedule", "*/15 * * * *")
    for f in CFG.get("feeds", []):
        jid = "ics-feed-" + f["id"]
        out += [f"# id: {jid}", f'{every} {wrapper} "{jid}" python3 {os.path.abspath(__file__)} --feed {f["id"]}']
    subprocess.run(["crontab", "-"], input="\n".join(l for l in out if l is not None) + "\n", text=True, check=True)
    print("\n".join(out))

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--install-jobs": install_jobs(); sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--remove": remove(sys.argv[2]); sys.exit(0)
    st, _ = dav("PROPFIND", BASE, headers={"Depth": "0"})
    if st not in (207, 200): raise SystemExit(f"cannot reach Baikal as {CFG['user']}: {st}")
    only = sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == "--feed" else None
    feeds = [f for f in CFG.get("feeds", []) if only in (None, f["id"])]
    if only and not feeds: raise SystemExit(f"no feed with id {only!r} in ics-feeds.json")
    ok = True
    for f in feeds:
        try: ok &= sync(f)
        except SystemExit as e: log(f"[{f.get('id')}] ERROR {e}"); ok = False
    log("done" if ok else "done with errors"); sys.exit(0 if ok else 1)
