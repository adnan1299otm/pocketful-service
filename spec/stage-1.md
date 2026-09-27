# Pocketful — Stage 1: payments and settlements

This stage defines the initial service and its API.

Build from the supplied requirements. Source code, API documentation and schemas from
existing products in this domain must not be used.

## 1. Scope

Users can send money by handle, request money and split bills. Payments appear in an
activity feed with public or private visibility. Authorized operators can submit groups
of transfers as settlements. Only the HTTP API is required.

The following apply to all operations, including concurrent requests and retries:

1. The sum of wallet balances always equals the total seeded by the last `POST /_test/reset`.
2. No wallet balance may be negative, including transiently.
3. A payment request may move money at most once.

All amounts are exact integer counts of minor units. Deposits, top-ups, withdrawals,
cards and bank integrations are out of scope. Money moves only between existing wallets.

## 2. Delivery and deployment

Deliver an HTTP service, a `Dockerfile` and a `RUN.md` with a command that builds and
starts the service without manual setup. Language, framework and storage are unrestricted.
A `docker-compose.yml` is optional.

The submission is a containerized HTTP service, not a Python package. Python is not
required in the implementation. TypeScript/JavaScript, Go, Rust, Java, Python and any
other language are equally valid. The harness builds the submitted `Dockerfile`, starts
the resulting image and tests only its HTTP behavior; it does not import or execute the
submission's source files on the judge host.

The image must run on its own with `-e PORT=<port>` and a port mapping. Runtime networking
has no outbound access. All runtime dependencies, initialization and seed data must work
within that single container. Compose configuration is not used to start the service.

### Resource limits

The service must operate within these limits:

| Limit | Value |
|---|---|
| CPU | 2 vCPU |
| Memory | 2 GiB |
| Start to first healthy response | 60 s |
| Concurrent requests | up to 50 in flight |
| Per-request timeout | 5 s (10 s for `POST /_test/reset`) |
| Outbound network | available during `docker build`, **none at run time** |
| Disk | ephemeral; state need not survive a container restart |

Runtime assets and dependencies must be included in the image. This includes fonts,
scripts and stylesheets; external services are unavailable at runtime.

## 3. Runtime contract

### 3.1 Listening

Listen on `0.0.0.0` using the `PORT` environment variable, default `8080`.

### 3.2 Health

```http
GET /health  ->  200  {"status": "ok"}
```

Return 200 once the service and its data store can serve requests, within 60 seconds
of container start. Non-200 responses are permitted before the service is ready.

### 3.3 Reset and seed

```http
POST /_test/reset
Content-Type: application/json

{ ...fixture... }

->  204 No Content
```

Replace all service state with the fixture in the request body (§4). When reset returns
204, subsequent requests must see only that fixture. Repeated resets are supported.
This test endpoint must be enabled in the delivered image and requires no authentication.

### 3.4 Conventions

- Requests and responses are `application/json; charset=utf-8`.
- Timestamps in responses are RFC 3339 with an explicit offset, e.g. `2026-09-24T19:00:00+02:00`.
- Unknown fields in a request body are ignored, never an error.
- Unknown query parameters are ignored.
- IDs are opaque strings of at most 64 characters. Their format is yours.

## 4. Model

The service has **one currency**, declared in the fixture. Every amount in the API is an integer
count of its minor units: `1000` in a `minor_units: 2` service is €10.00, and `1000` in a
`minor_units: 0` service is ¥1000.

API amounts must have an integral numeric value: JSON `1000`, `1000.0` and `1e3` all represent the
same valid minor-unit amount. Booleans and strings are not numbers here.

### Users and handles

Every user has a **handle**: unique across the service, matching `^[a-z0-9_]{1,20}$`, and never
changing once set. Users identify recipients by handle. Directory and user-search
endpoints are out of scope.

Seeded users take their handle from the fixture. A user created through `POST /auth/signup`
(§6 — there is no `handle` field in the signup body) has one **derived** from their email: take the
local part, lowercase it, replace every character outside `[a-z0-9_]` with `_`, and truncate to 20
characters. If that handle is already taken the signup fails; see the signup table in §6.

New users start with a balance of `0`. They can receive money and be asked for money immediately.

### Payments and requests

A **payment** moves money from one wallet to another, immediately and atomically. It is either sent
directly or created by paying a request.

A **request** asks someone for money. The `requester` will receive; the `payer` is being asked. A
request is `pending`, and then exactly one of `paid`, `declined` or `cancelled`. Only the payer may
pay or decline it; only the requester may cancel it.

**A request may exceed the payer's balance.** That is a legal state, not an error at creation time:
the request stays `pending` until it is paid, declined or cancelled, and an attempt to pay it while
short is `409 insufficient_funds` and changes nothing. Money can arrive later and the same request
then becomes payable.

**Visibility belongs to the payment, not the request.** The payer chooses it when the money moves.
A request carries no visibility of its own and never appears in anyone else's feed.

### The feed contract

`GET /activity` returns payments only. A payment appears for a caller **if and only if** its
`visibility` is `public`, **or** the caller is its sender or its receiver. There is no other rule,
no follow graph and no mute list. Requests never appear in the activity feed; they are read through
`GET /requests`, which returns only requests where the caller is the requester or the payer.

A split is not a feed item. The requests it creates are visible to their own two parties, and the
payments that eventually fulfil them follow the rule above.

Visibility is **one value on the payment**, seen identically by both parties and by everyone else.
A `private` payment is hidden from third parties, not from its own receiver.

### Arithmetic range

`amount` is at most `1000000000` on any single request, and no operation produces a balance outside
±2⁵³. Monetary arithmetic must preserve exact minor-unit values without rounding error.

### Fixture format

```json
{
  "currency": "EUR",
  "minor_units": 2,
  "users": [
    { "id": "u_ada", "email": "ada@example.com", "password": "correct horse",
      "display_name": "Ada", "handle": "ada", "balance": 10000 },
    { "id": "u_bob", "email": "bob@example.com", "password": "correct horse",
      "display_name": "Bob", "handle": "bob", "balance": 2500 }
  ],
  "payments": [
    { "id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
      "amount": 500, "note": "coffee", "visibility": "public" }
  ],
  "requests": [
    { "id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada",
      "amount": 1200, "note": "taxi", "status": "pending" }
  ]
}
```

- Seeded users must be able to log in with the given password immediately.
- `balance` is the wallet balance **after** every seeded payment has been applied. Seeded
  numbers are consistent; you do not replay seeded payments against balances.
- A `balance` below zero in a fixture is a reset error: return `422 validation_failed` from
  `POST /_test/reset` and change nothing.
- `minor_units` is `0`, `2` or `3`. Fixtures use `EUR` (2), `JPY` (0) and `BHD` (3).

An administrative balance endpoint is out of scope.

## 5. Errors

Every 4xx and 5xx response carries this body:

```json
{ "error": { "code": "insufficient_funds", "message": "human readable, any wording" } }
```

Use the specified HTTP status and `code`. The human-readable `message` may use any wording.
Endpoint-specific errors are listed with each endpoint.

| Status | `code` | When |
|---|---|---|
| 400 | `malformed_request` | Unparseable body, or a field of the wrong JSON type |
| 400 | `missing_idempotency_key` | Required `Idempotency-Key` header absent or empty |
| 401 | `unauthenticated` | Missing, malformed or unknown bearer token |
| 403 | `forbidden` | Authenticated, but not permitted to touch this resource |
| 404 | `not_found` | No such resource, or not visible to this caller |
| 409 | `idempotency_key_reuse` | Key already used by this caller with a different request body |
| 422 | `validation_failed` | A required field or query parameter is missing, or a stated rule is violated with no more specific code |

A field of the correct JSON type with an invalid format or out-of-range value gives
422 `validation_failed`, unless an endpoint specifies a different error. This includes
invalid dates, negative counts and values exceeding a stated maximum or length. In addition:

- Endpoint-specific field rules take precedence: invalid `amount` values (including strings and
  booleans), non-string `note` values (including `null`), and any `visibility` other than
  `public` or `private` are 422 `validation_failed`. Omission alone selects the optional-field
  defaults. Other wrong JSON types follow the rule below.
- An integer-valued **query parameter** is written as plain decimal digits: `1e9`, `4.0` and `+4`
  are 422 `validation_failed` whatever their numeric value.
- Reserve 400 `malformed_request` for a body that does not parse or a field of the wrong type.

Shared ranges, enforced on every endpoint that takes them:

| Field | Valid | Otherwise |
|---|---|---|
| `Idempotency-Key` | 1 to 255 characters | 422 `validation_failed` |
| `limit` | integer 1 to 200 | 422 `validation_failed` |
| `offset` | integer 0 or more | 422 `validation_failed` |

Requests must not produce 5xx responses, including under concurrent load.

## 6. Authentication

Authentication supports signup and login. Email verification, password reset, refresh
tokens and role-management endpoints are out of scope. Permissions specified elsewhere
in these requirements still apply.

```http
POST /auth/signup
{ "email": "a@example.com", "password": "correct horse", "display_name": "Ada" }

->  201  { "user_id": "u_1", "display_name": "Ada", "token": "..." }
```

```http
POST /auth/login
{ "email": "a@example.com", "password": "correct horse" }

->  200  { "user_id": "u_1", "display_name": "Ada", "token": "..." }
```

| Case | Response |
|---|---|
| Email already registered | 409 `email_taken` |
| Password shorter than 8 characters | 422 `validation_failed` |
| `email` not of the form `local@domain` | 422 `validation_failed` |
| Wrong password or unknown email on login | 401 `unauthenticated` |
| The handle derived from the email (§4) is already taken | 409 `handle_taken`, and no account is created |

Every other endpoint requires a bearer token, except `/health`, `/_test/reset` and the two above.
Wallet API endpoints require authentication.

```http
Authorization: Bearer <token>
```

Tokens do not expire. An account may have multiple valid tokens and concurrent sessions.

Passwords must be stored using a password-hashing function such as bcrypt, scrypt or
Argon2, or an equivalent. Plaintext password storage is not permitted.

## 7. Idempotency

Five write paths require an idempotency key (§8 and §11): **`POST /payments`**, **`POST /requests`**,
**`POST /requests/{id}/pay`**, **`POST /splits`** and **`POST /settlements`**. Everything below applies to each of them
independently.

```http
Idempotency-Key: <client-chosen string, 1..255 characters>
```

The key is scoped to **the authenticated user**. Two different users may use the same key string
with no interaction between them.

A replay means the same user sending the **same method, the same path and the same body**. The
same key with the same body on a different path is a different request, not a replay, and must
succeed normally.

| Situation | Response |
|---|---|
| Header absent or empty | 400 `missing_idempotency_key` |
| First use of the key | The normal response, **201** |
| Replay: same key, same body | **200**, body identical to the original response as a JSON value |
| Same key, different body | 409 `idempotency_key_reuse` |
| Key reused after the original request failed with 4xx | Treated as a first use |

"Same body" means the same JSON value after parsing — key order and whitespace do not matter.

For concurrent identical requests with an unused key, exactly one returns 201.
The others return 200 with the same body. The operation takes effect only once.

A successful replay returns the original response, even after the resource changes or
is cancelled. It makes no further state changes.

After the body has parsed as a JSON object and the caller is authenticated, an already
claimed key is resolved before endpoint field validation or current-resource checks. Thus
changing a successful request to an invalid body with the same key still returns
`409 idempotency_key_reuse`.

## 8. API

### `GET /me`

```json
{ "user_id": "u_ada", "display_name": "Ada", "handle": "ada",
  "balance": 10000, "currency": "EUR", "minor_units": 2 }
```

### `POST /payments`

**An idempotent write path.** `Idempotency-Key` is required; see §7.

```http
POST /payments
Authorization: Bearer <token>
Idempotency-Key: 2f9c1a...

{ "to_handle": "bob", "amount": 1500, "note": "dinner", "visibility": "public" }
```

`note` is optional and defaults to `""`. `visibility` is optional and defaults to `"public"`.

```json
201
{
  "payment_id": "p_7",
  "from_user_id": "u_ada",
  "from_handle": "ada",
  "to_user_id": "u_bob",
  "to_handle": "bob",
  "amount": 1500,
  "currency": "EUR",
  "note": "dinner",
  "visibility": "public",
  "request_id": null,
  "created_at": "2026-09-24T11:04:03+00:00"
}
```

| Case | Response |
|---|---|
| The caller's balance is below `amount` | 409 `insufficient_funds` |
| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |
| `to_handle` is the caller's own handle | 422 `self_payment` |
| `note` longer than 200 characters | 422 `validation_failed` |
| `visibility` is neither `public` nor `private` | 422 `validation_failed` |
| No user has that handle | 404 `not_found` |

The debit and the credit are one atomic step. A payment is never visible in one wallet and not the
other, and a failed payment leaves no trace in either.

`note` is stored and returned verbatim: no trimming, no escaping, no normalisation. Unicode and
emoji survive a round trip byte for byte.

### `POST /requests`

**An idempotent write path.**

```http
POST /requests
Idempotency-Key: 9b1f04...

{ "payer_handle": "ada", "amount": 1200, "note": "taxi" }
```

The caller is the requester.

```json
201
{
  "request_id": "rq_4",
  "requester_id": "u_bob",
  "requester_handle": "bob",
  "payer_id": "u_ada",
  "payer_handle": "ada",
  "amount": 1200,
  "currency": "EUR",
  "note": "taxi",
  "status": "pending",
  "payment_id": null,
  "created_at": "2026-09-24T11:06:10+00:00"
}
```

| Case | Response |
|---|---|
| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |
| `payer_handle` is the caller's own handle | 422 `self_request` |
| `note` longer than 200 characters | 422 `validation_failed` |
| No user has that handle | 404 `not_found` |

**The payer's balance is not checked here.** A request for more than the payer holds is created
normally and sits `pending`.

### `POST /requests/{id}/pay`

**An idempotent write path.** Only the payer may call it.

```http
POST /requests/rq_4/pay
Idempotency-Key: c41d88...

{ "visibility": "private" }
```

The body carries `visibility` only, optional, default `"public"`. It is the payer's choice, not the
requester's. **A replay must send the identical body** — `{}` and `{"visibility": "public"}` are
different JSON values, so reusing a key across the two is `409 idempotency_key_reuse`, per §7.

Returns `201` with the created **payment**, exactly as `POST /payments` returns one, with
`request_id` set to this request. The request becomes `paid` and carries the new `payment_id`.

| Case | Response |
|---|---|
| The request is not `pending` | 409 `request_not_pending` |
| The payer's balance is below `amount` | 409 `insufficient_funds` |
| The caller is not the request's payer | 403 `forbidden` |
| Unknown request | 404 `not_found` |

Replaying a successful payment returns 200 with its original payment body, including
when the request is already `paid`. It moves no additional money and must not return
`409 request_not_pending`.

### `POST /requests/{id}/decline`

Only the payer. No idempotency key. Returns `200` with the request, `status: "declined"`. Declining
an already-declined request is `200` with the current state — declining twice is not an error.
A `paid` or `cancelled` request is `409 request_not_pending`. Not the payer is `403 forbidden`.

### `POST /requests/{id}/cancel`

Only the requester. No idempotency key. Returns `200` with the request, `status: "cancelled"`.
Cancelling an already-cancelled request is `200`. A `paid` or `declined` request is
`409 request_not_pending`. Not the requester is `403 forbidden`.

### `GET /requests`

```http
GET /requests?direction=incoming&status=pending&limit=50&offset=0
```

Requests where the caller is the requester or the payer, and no others. Newest first by
`created_at`.

- `direction` is `incoming` (the caller is the payer), `outgoing` (the caller is the requester) or
  absent for both.
- `status` is one of the four statuses, or absent for all.
- `limit` defaults to 50, range 1 to 200. `offset` defaults to 0 and must be 0 or more. Outside
  either range is 422 `validation_failed`. An unknown `direction` or `status` value is also 422.
- `has_more` is true when items exist beyond the last one returned.

```json
{ "requests": [ { ...request... } ], "has_more": false }
```

### `POST /splits`

**An idempotent write path.** Splits an amount the caller already paid, and asks each of the other
participants for their share by creating one `pending` request each.

```http
POST /splits
Idempotency-Key: 7a3e52...

{ "amount": 3000, "participant_handles": ["ada", "bob", "cy"], "note": "dinner" }
```

The caller may be included in `participant_handles` or omitted. Shares follow the equal-split
rule in §9, in the order the handles are given. **A request is created for every participant
except the caller**, each for that participant's share, with the caller as requester.

```json
201
{
  "split_id": "sp_2",
  "amount": 3000,
  "currency": "EUR",
  "note": "dinner",
  "shares": [ { "handle": "ada", "amount": 1000 },
              { "handle": "bob", "amount": 1000 },
              { "handle": "cy",  "amount": 1000 } ],
  "requests": [ { ...request for bob... }, { ...request for cy... } ],
  "created_at": "2026-09-24T11:11:00+00:00"
}
```

`shares` covers every participant including the caller, in the order given, and always sums to
`amount`. `requests` covers every participant except the caller, in the same order.

| Case | Response |
|---|---|
| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |
| `participant_handles` empty, or containing a duplicate handle | 422 `validation_failed` |
| `note` longer than 200 characters | 422 `validation_failed` |
| Any handle is unknown | 404 `not_found` |

A split whose only participant is the caller is **valid**: it computes one share, creates zero
requests, and returns `"requests": []`. Nothing about a split checks anyone's balance.

### `GET /activity`

```http
GET /activity?limit=50&offset=0
```

Payments visible to the caller by the feed contract in §4, newest first by `created_at`.

```json
{ "payments": [ { ...payment... } ], "has_more": false }
```

- The relative order of two payments created within the same second is unspecified.
  Stable pagination during concurrent writes is not required for this endpoint.
- `limit` and `offset` behave exactly as in `GET /requests`.

## 9. Money and rounding

Shares must be whole minor units, sum exactly to `amount` and differ by at most one
minor unit. When the amount does not divide evenly, the larger shares go to the first
participants in `participant_handles` order.

| `amount` | `n` | Shares |
|---|---|---|
| 1000 | 3 | 334, 333, 333 |
| 1 | 3 | 1, 0, 0 |
| 10 | 3 | 4, 3, 3 |
| 999 | 3 | 333, 333, 333 |
| 5 | 5 | 1, 1, 1, 1, 1 |

Splitting the same amount among the same people in a different
`participant_handles` order gives the extra unit to a different person. A share of `0` is legal and
still produces a request for that participant.

Each split's shares are independent of previous splits. After any number of splits have
been paid in full, wallet balances must still sum exactly to the seeded total.

## 10. Export and import

The service must support `GET /_test/export` and `POST /_test/import`. Like reset, these
are unauthenticated test endpoints.
Exports may contain credentials and session tokens; handle them as private test artifacts.
Return 200 from export with a JSON object containing `track: "pocketful"`,
`format_version: 1` and `state` (an implementation-defined JSON object). The state format
is opaque to the caller and must be accepted unchanged by import.

Import takes that entire object and atomically replaces the service's state, returning
204. It must accept an unchanged export produced by this service. No dependency on the
source process, files, volume, port or network address is allowed. Import is replacement,
not merge; repeating it restores the exported state without duplicating anything. Invalid
JSON follows §5; missing fields, wrong track/version or an invalid state give 422
`validation_failed` without changing the destination. Test control calls have a 10-second
timeout. Export is an atomic, read-only snapshot; subsequent source writes do not change it.

Preserve accounts and hashed-password login, existing bearer tokens, currency, balances,
payments, requests, permissions, all completed idempotent request bodies and original
responses. Identities, timestamps and monetary records must not be regenerated or replayed
against an already-net balance. Failed request keys remain reusable. Existing receipts,
tokens and retries must remain valid after import; replacing the state with a fresh fixture
does not satisfy this requirement. Import removes all previous destination data and
credentials. Reset clears all state, including imported state. State need not survive an
abrupt container restart.

## 11. Atomic net settlements

The reset fixture may include `settlement_operator_ids`, an array of user ids, default [].
An operator may execute a settlement across any wallets. This permission does not grant
access to another user's requests or private activity items.

`POST /settlements` requires an operator and an idempotency key. No token gives 401;
authenticated non-operator gives 403 `forbidden`. Body:

```json
{"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100},
               {"from_handle": "bob", "to_handle": "cy", "amount": 50}]}
```

transfers contains 1..32 objects. Each uses ordinary payment amount, note and visibility
rules (defaults: empty note, public). Unknown handle is 404; self-transfer is 422
`self_payment`; malformed batch shape is 422 `validation_failed`. Entry errors take precedence
in input order, before insufficient funds. Unknown fields are ignored.

A settlement is affordable when every wallet's balance after all incoming and outgoing
transfers is nonnegative. Insufficient collective funds gives 409 `insufficient_funds`.
Either all movements commit together or none do; failed
validation claims no idempotency key and creates no payment or revision.

Return 201 with `settlement_id`, `committed_at` and `payments` in input order. Every member is
an ordinary payment with `settlement_id` linking the batch; nonmembers expose null for that
field. Members have null request_id and the same server-assigned created_at, equal to
committed_at.

Constituents follow ordinary activity-feed visibility. The settlement response contains every
member's receipt. Replays return
200 with the original complete response. This is the fifth idempotent write path in stage 1.
A reset/import must preserve settlement operator permissions, original payments, requests,
settlement membership and retry responses.
