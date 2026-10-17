# Run on CT 109 with TOKEN=<HortusFox API key>: create the "Plant template" + recurring watering task (idempotent by name)
import json, os, subprocess, urllib.request, urllib.parse, datetime
ip=subprocess.check_output(["docker","inspect","hortusfox","--format","{{.NetworkSettings.Networks.proxy.IPAddress}}"]).decode().strip()
B=f"http://{ip}"
def api(path, **params):
    data=urllib.parse.urlencode({"token":os.environ["TOKEN"], **params}).encode()
    r=json.load(urllib.request.urlopen(urllib.request.Request(B+path, data=data), timeout=60))
    if r.get("code")!=200: raise SystemExit(f"{path}: {r}")
    return r
locs={l["name"]:l["id"] for l in api("/api/locations/list")["list"]}
existing=[p for p in api("/api/plants/list", location=locs["Templates"]).get("list",[]) if p["name"]=="Plant template"]
if existing:
    pid=existing[0]["id"]; print("plant exists:", pid)
else:
    pid=api("/api/plants/add", name="Plant template", location=locs["Templates"])["plant"]; print("plant created:", pid)
today=datetime.date.today()
fields={
 "scientific_name":"Genus species (replace with the plant's name)",
 "light_level":"light_level_indirect_light",
 "humidity":"50",
 "lifespan":"perennial",
 "hardy":"0",
 "health_state":"in_good_standing",
 "last_watered":today.isoformat(),
 "last_fertilised":today.isoformat(),
 "notes":("WATERING TEMPLATE — clone this plant for each new plant, then edit:\n"
          "1. Name, scientific name, location, photo.\n"
          "2. 'Water every (days)' attribute and the matching 'Water: …' task (Tasks → edit the recurring interval).\n"
          "3. Press 'Watered' / update Last watered each time you water; the task comes back on its own.\n\n"
          "Before watering: push a finger 2–3 cm into the soil — water only if it's dry.\n"
          "How: water slowly until it runs out of the drainage hole, empty the saucer after 15 minutes.\n"
          "Winter (Nov–Feb): roughly half as often. Signs of too much: yellow soft leaves, wet soil smell. Too little: crispy edges, drooping."),
}
for k,v in fields.items(): api("/api/plants/update", plant=pid, attribute=k, value=v)
print("fields set:", ", ".join(fields))
attrs={"Water every (days)":("int","7"),"Water amount":("string","Until it drains from the bottom (~250 ml for a 15 cm pot)"),
       "Check before watering":("string","Top 2–3 cm of soil dry"),"Fertilise":("string","Every 4 weeks, Mar–Sep, half-strength liquid feed"),
       "Pot / soil":("string","15 cm pot with drainage hole, peat-free houseplant mix")}
have={a.get("label") for a in (api("/api/plants/get", plant=pid).get("plant",{}) or {}).get("attributes",[]) or []} if False else set()
for label,(dt,content) in attrs.items():
    try: api("/api/plants/attributes/add", plant=pid, label=label, datatype=dt, content=content)
    except SystemExit as e: print("attr", label, "->", e)
print("attributes added:", ", ".join(attrs))
tasks=[t for t in api("/api/tasks/fetch").get("tasks",[]) if t.get("title")=="Water: Plant template"]
if tasks: print("task exists:", tasks[0]["id"])
else:
    due=(today+datetime.timedelta(days=7)).isoformat()
    t=api("/api/tasks/add", title="Water: Plant template", plant=pid, due_date=due, recurring_time=7, recurring_scope="days",
          description="Check the top 2–3 cm of soil; water until it drains if dry. Recurs every 7 days — change the interval to match the plant's 'Water every (days)'.")
    print("task created:", t.get("task") or t)
api("/api/plants/log/add", plant=pid, content="Template created: weekly watering, indirect light, 50% humidity.")
print("plant url: https://plants.example.com/plants/details/%s" % pid)
