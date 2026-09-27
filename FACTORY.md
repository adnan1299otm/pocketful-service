# Factory Architecture & Operations

## 1. Seats & Mandates

The factory is staffed by five distinct autonomous agent seats running on the Band SDK:

| Seat | Role | Harness | Model |
|---|---|---|---|
| `architect` | System planning, contracts, stage gates | Custom (Band SDK) | `Qwen/Qwen2.5-Coder-32B-Instruct` |
| `backend` | Data layer, business logic, API implementation | Custom (Band SDK) | `Qwen/Qwen2.5-Coder-32B-Instruct` |
| `frontend` | User interface, client screens, test attributes | Custom (Band SDK) | `Qwen/Qwen2.5-Coder-32B-Instruct` |
| `auditor` | Automated verification, test execution, gate reporting | Custom (Band SDK) | `Qwen/Qwen2.5-Coder-32B-Instruct` |
| `integrator` | Failure diagnosis, cross-component bug fixes | Custom (Band SDK) | `Qwen/Qwen2.5-Coder-32B-Instruct` |

## 2. Design Choices & Handoff Protocol

### Sequential Delegation Pipeline
To prevent concurrent race conditions, token wastage, and cascading chatter, the factory uses a single-lane sequential handoff pipeline:
1. **Architect** receives the stage task, defines the plan and data contracts, and hands off to `@backend`.
2. **Backend** implements persistence, business logic, endpoints, and local validations, then hands off to `@frontend` (or `@auditor`).
3. **Frontend** implements the UI screens and attaches exact test attributes, then hands off to `@auditor`.
4. **Auditor** executes the automated test suites using local shell execution.
   - If all tests pass: reports `PASS` to `@architect`.
   - If any test fails: reports concrete failures to `@integrator`.
5. **Integrator** diagnoses root causes, writes fixes directly to source files, and hands back to `@auditor` for re-audit.

### Silence Protocol
Each agent obeys a strict silence protocol: if a message in the room is not addressed to that specific seat or does not contain an actionable task, the agent completes the turn silently without posting into the room. This prevents ambient room chatter and infinite ping-pong loops.

### Autonomous Tool Execution
Agents operate directly on the target repository using native tools:
- `write_file`: creates or updates source code, migrations, and configurations.
- `read_file`: inspects code and test definitions.
- `list_directory`: explores file trees and generated artifacts.
- `run_shell`: executes commands (linting, test suites, builds).

## 3. Failure Handling & Recovery
When verification fails, the Auditor generates an evidence-based report isolating the failing check. The Integrator diagnoses whether the failure is a data schema mismatch, constraint error, or missing test attribute. Fixes are applied iteratively until all checks pass before the stage gate is cleared.
