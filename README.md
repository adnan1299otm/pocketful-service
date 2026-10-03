# Pocketful Service — Autonomous Dark Factory

**Track:** `pocketful` · **Hackathon:** WeAreDevelopers World Congress 2026

A five-seat autonomous AI factory built on Band Desktop that designed, implemented, tested, and iterated on a full-stack wallet and payments service — all four stages — without human code contributions.

---

## Team & Track

| Field | Value |
|---|---|
| Track | `pocketful` — wallet, payments, and settlements |
| Factory Platform | Band Desktop (Band SDK) |
| Models | `Qwen/Qwen2.5-Coder-32B-Instruct` (primary code generation) · `moonshotai/Kimi-K2-Instruct` (planning & architecture) via Featherless AI |
| Stages Completed | **4 / 4** |
| Test Score | **193 / 193 (100%)** |

---

## What Was Built

A production-quality Flask service that passes all four stage suites of the Pocketful specification:

| Stage | What It Delivers | Tests |
|---|---|---|
| **Stage 1** | JSON API — payments, requests, splits, settlements, idempotent writes, atomic transfers, export/import | 147 / 147 |
| **Stage 2** | Browser wallet UI (Playwright-tested), payment authorizations and holds, double-submit prevention | 35 / 35 |
| **Stage 3** | Historical balance queries, paginated statements, effective-dated payment corrections, snapshot pagination | 6 / 6 |
| **Stage 4** | Receiver refunds, operator batch corrections, full history integrity | 5 / 5 |

---

## Repository Layout

```
pocketful-service/
  README.md          ← this file
  FACTORY.md         ← factory seats, design, failure handling
  mandates/          ← one .md per seat (architect, backend, frontend, auditor, integrator)
  room.json          ← full Band Desktop session log
  stage-1/           ← Dockerfile · RUN.md · main.py  (Stage 1 API)
  stage-2/           ← Dockerfile · RUN.md · main.py  (Stage 1 + UI + Holds)
  stage-3/           ← Dockerfile · RUN.md · main.py  (Stage 2 + History)
  stage-4/           ← Dockerfile · RUN.md · main.py  (Stage 3 + Refunds + Batches)
```

Each stage folder is an independently buildable service. Run any stage with:

```bash
docker build -t pocketful-service:stage-N .
docker run -p 8080:8080 -e PORT=8080 pocketful-service:stage-N
```

---

## How to Read This Repository

1. **`FACTORY.md`** — start here to understand how the factory is structured, how seats hand off work, and how failures are recovered.
2. **`mandates/`** — each file defines one seat's role, harness, model, and operating rules.
3. **`room.json`** — the complete Band Desktop room event log showing real inter-seat communication.
4. **`stage-N/main.py`** — the service source. Each stage extends the previous one; `stage-4` is the complete system.
