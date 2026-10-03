# Factory Architecture & Operations

## 1. Seats

Five autonomous agent seats collaborate inside a single Band Desktop room. Each seat has one mandate file named after it.

| Seat | Role | Harness | Model |
|---|---|---|---|
| `architect` | System planning, data contracts, stage gates | Custom (Band SDK) | `moonshotai/Kimi-K2-Instruct` |
| `backend` | Persistence layer, business logic, API endpoints | Custom (Band SDK) | `moonshotai/Kimi-K2-Instruct` |
| `frontend` | Browser screens, `data-testid` attributes, JS behaviour | Custom (Band SDK) | `moonshotai/Kimi-K2-Instruct` |
| `auditor` | Test execution, gate reporting, evidence collection | Custom (Band SDK) | `moonshotai/Kimi-K2-Instruct` |
| `integrator` | Root-cause diagnosis, targeted bug fixes, re-audit | Custom (Band SDK) | `moonshotai/Kimi-K2-Instruct` |

---

## 2. Agent Handoff Flow

```
┌─────────────┐
│  Architect  │  ← receives stage spec, produces numbered plan
└──────┬──────┘
       │ hands off to @backend with full data contract
       ▼
┌─────────────┐
│   Backend   │  ← implements store, endpoints, validations
└──────┬──────┘
       │ hands off to @frontend (UI stages) or @auditor (API-only stages)
       ▼
┌─────────────┐
│  Frontend   │  ← implements HTML screens, testids, JS behaviour
└──────┬──────┘
       │ hands off to @auditor
       ▼
┌─────────────┐        ┌─────────────┐
│   Auditor   │──FAIL──▶ Integrator  │
│             │        │             │
│  runs tests │        │ diagnoses   │
│             │◀─FIXED─│ & patches   │
└──────┬──────┘        └─────────────┘
       │ PASS
       ▼
┌─────────────┐
│  Architect  │  ← closes stage gate, starts next stage plan
└─────────────┘
```

The pipeline is **strictly sequential** — only one seat acts at a time. This eliminates race conditions, cascading chatter, and duplicate work.

---

## 3. Design Choices

### Single-file service per stage
Each stage is one `main.py` file using only Flask and the Python standard library. No ORM, no external database, no background threads. The entire service state lives in a single in-memory `Store` object. This makes every behaviour deterministic and easy to reason about under the harness's concurrent test load.

### Explicit stage progression
Each stage folder extends the previous one rather than replacing it. `stage-2/main.py` contains everything from Stage 1 plus the UI and hold logic. `stage-4/main.py` is the complete system. Judges can build and test any individual folder independently.

### Idempotency architecture
All write paths that the spec requires to be idempotent use a `(user_id, idempotency_key, path)` tuple as the store key. Replays return the exact original response body. The browser pay form uses a hidden JS-regenerated key that changes only when the user edits a field, satisfying the double-submit requirement.

### Atomic money movement
All balance changes happen in a single synchronous block with a held-funds check before committing. Python's GIL ensures atomicity for in-memory operations without explicit locking.

---

## 4. Failure Handling & Recovery

When the Auditor reports failures, the Integrator follows a strict diagnosis protocol:

1. Read the exact assertion error from the harness log.
2. Read the relevant test source to understand what the test expects.
3. Read the relevant endpoint code to find the mismatch.
4. Apply a minimal targeted fix — no speculative refactoring.
5. Hand back to the Auditor for re-verification.

Fixes are never guessed from test names alone. Every change is grounded in the actual test assertion and the actual server response.

---

## 5. Cost & Token Budget

The factory used a turn-timeout of 900 seconds per seat turn and a silence protocol — seats that are not addressed emit nothing. This kept the room log clean and avoided the token waste of ambient commentary.
