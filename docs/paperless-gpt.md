# AI filing for Paperless on a CPU (paperless-gpt + Ollama)

New documents get a real title, correspondent and date from a small local model. No GPU, no cloud.

## Does a CPU cope?

Measured on a 4-core/8-thread Xeon W-2123 (AVX-512, no VNNI), 6 threads, one ~1,600-token OCR'd utility bill asking for
title / correspondent / type / tags / date as JSON ([`apps/paperless-gpt/cpu-benchmark.sh`](../apps/paperless-gpt/cpu-benchmark.sh)):

| Model | Per document (cold) | Read | Write | Quality |
|---|---|---|---|---|
| qwen3:1.7b | 19 s | 129 tok/s | 14 tok/s | missed the date, weak title |
| **llama3.2:3b** | **28 s** | 71 tok/s | 10 tok/s | all fields right |
| qwen2.5:3b | 35 s | 70 tok/s | 11 tok/s | good, wordy |
| gemma3:4b | 42 s | 59 tok/s | 9 tok/s | best tags |
| qwen3:4b | 50 s | 47 tok/s | 7 tok/s | good, slowest |

Warm-run prompt speeds are cache hits and meaningless. In paperless-gpt each field is a separate model call, so real
documents take ~30–60 s. Vision-model OCR is not viable on this class of CPU.

## What it does (and doesn't)

- A Paperless workflow ("AI filing (paperless-gpt)", trigger *Document added*) tags every new document `paperless-gpt-auto`;
  paperless-gpt swaps it for `paperless-gpt-auto-complete` (failures → `paperless-gpt-failed`).
- **Title, correspondent and created date only.** A 3B model over-tags (a park reservation came back as *Bank Statement,
  Invoice, Legal & Contract…*) and guesses document types badly, so tags and types stay with Paperless's own matching rules
  (`AUTO_GENERATE_TAGS=false`, `AUTO_GENERATE_DOCUMENT_TYPE=false`). Undated old books can get the current year.
- Reverting what it changed: `/api/documents/<id>/history/` lists every change by the `paperless-gpt` user.

Kit: `50-app.sh paperless-gpt` (service user + token, stack) and `63-post-install.py paperless-gpt` (model pull, workflow,
queue existing documents). Progress: [`apps/paperless-gpt/status.py`](../apps/paperless-gpt/status.py).
