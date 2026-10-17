# Run on CT 109 with T=<paperless token>: counts of paperless-gpt tags + the latest processed documents
import json, os, subprocess, sys, urllib.request
ip=subprocess.check_output(["docker","inspect","paperless","--format","{{.NetworkSettings.Networks.proxy.IPAddress}}"]).decode().strip(); B=f"http://{ip}:8000/api"
H={"Authorization":"Token "+os.environ["T"],"Accept":"application/json; version=9"}
get=lambda p: json.load(urllib.request.urlopen(urllib.request.Request(B+p, headers=H), timeout=60))
tags={t["name"]:t["id"] for t in get("/tags/?page_size=500")["results"]}
counts={n:get(f"/documents/?tags__id__all={tags[n]}&page_size=1")["count"] for n in ("paperless-gpt-auto","paperless-gpt-auto-complete","paperless-gpt-failed")}
print(json.dumps(counts))
if len(sys.argv)>1:
    corr={c["id"]:c["name"] for c in get("/correspondents/?page_size=500")["results"]}; dt={d["id"]:d["name"] for d in get("/document_types/?page_size=500")["results"]}; tn={v:k for k,v in tags.items()}
    for d in get(f"/documents/?tags__id__all={tags['paperless-gpt-auto-complete']}&ordering=-modified&page_size={sys.argv[1]}")["results"]:
        print("-", d["title"][:60], "|", corr.get(d["correspondent"]), "|", dt.get(d["document_type"]), "|", d["created"], "|", [tn[t] for t in d["tags"] if not tn[t].startswith("paperless-gpt")][:5])
