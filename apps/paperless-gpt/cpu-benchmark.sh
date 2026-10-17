#!/usr/bin/env bash
# Run ON CT 109: temporary CPU-only Ollama benchmark for paperless-gpt-style tasks. Removes itself with: bench.sh --cleanup
set -euo pipefail
if [ "${1:-}" = --cleanup ]; then docker rm -f llm-bench >/dev/null 2>&1; docker volume rm llm-bench-models >/dev/null 2>&1; docker image rm ollama/ollama:latest >/dev/null 2>&1; echo cleaned; exit 0; fi
docker run -d --name llm-bench --cpus 6 -v llm-bench-models:/root/.ollama -p 127.0.0.1:11434:11434 -e OLLAMA_NUM_PARALLEL=1 ollama/ollama:latest >/dev/null
until curl -sf http://127.0.0.1:11434/api/version >/dev/null; do sleep 2; done
MODELS="${MODELS:-qwen3:1.7b llama3.2:3b qwen2.5:3b gemma3:4b qwen3:4b}"
for m in $MODELS; do docker exec llm-bench ollama pull $m >/dev/null 2>&1 && echo "pulled $m"; done
python3 - <<'PY'
import json, os, time, urllib.request
doc = """EXAMPLE ENERGY CO — Statement Date 09/28/2026 — Account 0000-0000-000
Service address: 123 Example St, Springfield ZZ 00000. Billing period 08/27/2026 to 09/26/2026 (31 days).
Previous balance $80.00, payment received 09/02/2026 thank you -$80.00. Current natural gas charges:
Customer charge $10.00; Distribution 40 therms x $0.20000 = $8.00; Gas cost 40 therms x $0.40000 = $16.00;
Gas affordability program $0.50; Conservation improvement $1.00; Interim rate adjustment $1.25; City franchise fee $1.75;
State sales tax $2.00. Total current charges $40.50. Amount due $40.50 by 10/20/2026. Autopay is ON: we will draft
your checking account ending 0000 on 10/20/2026. Usage history: Sep 40 therms, Aug 40, Jul 35, Jun 45, May 95, Apr 180,
Mar 340, Feb 450, Jan 500, Dec 470, Nov 290, Oct 120. Average daily temperature 64F. Meter #00000000 read 09/26 actual.
""" * 4
prompt = ("You are a document filing assistant. From the OCR text below return ONLY compact JSON with keys "
          "title (max 8 words), correspondent, document_type, tags (3-5 lowercase), created_date (YYYY-MM-DD).\n\nOCR TEXT:\n" + doc)
for m in os.environ.get("MODELS", "qwen3:1.7b llama3.2:3b qwen2.5:3b gemma3:4b qwen3:4b").split():
    body = {"model": m, "prompt": prompt, "stream": False, "format": "json", "think": False,
            "options": {"num_ctx": 4096, "num_thread": 6, "temperature": 0}}
    for run in ("cold", "warm"):
        t = time.time()
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/generate", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=900))
        except Exception as e:
            print(f"{m:<12} {run}: ERROR {e}"); break
        wall = time.time() - t
        pp = r["prompt_eval_count"] / (r["prompt_eval_duration"] / 1e9) if r.get("prompt_eval_duration") else 0
        tg = r["eval_count"] / (r["eval_duration"] / 1e9) if r.get("eval_duration") else 0
        print(f"{m:<12} {run}: wall {wall:6.1f}s  prompt {r.get('prompt_eval_count',0):5d} tok @ {pp:6.1f} tok/s  gen {r.get('eval_count',0):4d} tok @ {tg:5.1f} tok/s")
        if run == "warm": print("             ->", r["response"].strip().replace("\n", " ")[:220])
PY
