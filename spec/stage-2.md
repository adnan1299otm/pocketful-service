# Pocketful — Stage 2: wallet screens and payment authorizations

The stage-1 requirements continue to apply, with the additions below. Numbered section
references such as §5 and §7 refer to `stage-1.md`.

Users can manage payments, requests and bill splits in a browser. They can also reserve
money for a recipient to collect later, in one or more captures.

The following screens must be reachable by URL. Other screens must be reachable through
the UI. Server-side and client-side rendering are both permitted.

| Route | Screen |
|---|---|
| `/` | Balance, pay form, request form and the activity feed |
| `/requests` | Incoming and outgoing requests, with pay, decline and cancel |
| `/split` | Split form |
| `/signup` | Signup |
| `/login` | Login |

The browser and the API share `/requests`. Return the UI for `Accept: text/html`; API requests
without that header receive JSON.

The UI must expose the `data-testid` attributes listed below for integration testing.
Additional elements are permitted, and the visual implementation is the team's choice subject
to the product-quality requirements below.

## Product and visual direction

The browser experience must feel like a coherent, presentation-ready consumer finance product,
not a test harness with controls attached. Aim for a calm, trustworthy character. Available funds
must be the clearest monetary value once holds exist, with total and held funds visibly secondary.
Payments, requests, splits and authorisations should be easy to scan, and status, direction,
privacy and money movement should be understandable without interpreting raw API data.

Use a consistent visual system for typography, spacing, colour, controls and feedback. Primary
actions must be easy to identify. Available, held, pending, loading, successful, refused and
uncertain states must be visually distinct as well as satisfying the behavioural requirements
below. Format people, amounts and timestamps for people first; expose technical identifiers only
where they help the user.

The required flows must remain clear and usable at a 375 CSS-pixel viewport and at conventional
desktop widths, without horizontal page scrolling. Inputs need visible labels, keyboard focus must
be apparent, and text and controls need sufficient contrast. Provide considered empty, loading and
error states, and keep navigation consistent across the required routes. A custom illustration,
brand asset or exact visual match to a reference is not required.

## Signup and login

| `data-testid` | Element |
|---|---|
| `signup-email`, `signup-password`, `signup-display-name` | Inputs |
| `signup-submit` | Button |
| `login-email`, `login-password`, `login-submit` | Inputs and button |
| `auth-error` | Error message. Present only when there is one |
| `current-user` | Visible on every screen when signed in. Text contains the display name |
| `current-handle` | Text is exactly the caller's handle, with no `@` and no surrounding words |
| `logout-button` | Button |

## Balance and pay — `/`

| `data-testid` | Element |
|---|---|
| `wallet-balance` | Text is exactly the formatted amount. Carries `data-amount="{minor units}"` |
| `pay-handle`, `pay-amount`, `pay-note` | Inputs. `pay-amount` is a **decimal** string as a person would type it, e.g. `15.00` |
| `pay-visibility` | Selects `public` or `private`. Option values are those two strings |
| `pay-submit` | Button |
| `pay-error` | Error message, when the payment is refused — including insufficient funds |
| `request-handle`, `request-amount`, `request-note`, `request-submit` | The request form |
| `request-error` | Error message, when the request is refused |

Keep the pay form's values after success. Submitting it again without changing a field
must not send another payment: `wallet-balance` falls once, the feed contains one payment
and `pay-error` is absent. Changing a field makes the next submission a new payment request.
Retries follow §7.

**Formatted amount.** `wallet-balance` is the decimal with exactly `minor_units` decimal places, a
single space, then the currency code: `100.00 EUR`. For a `minor_units` of `0` there is no decimal
point at all: `1200 JPY`. Balances are never negative, so there is no sign.

The form accepts decimal amounts and submits minor units to the API. With `minor_units: 2`,
`15.00` and `15` both submit `1500`; `15.5` submits `1550`. Nonnumeric input or more than
`minor_units` decimal places must show the form's error element without sending a request.
For example, `15.005` is rejected rather than rounded.

## Activity feed — `/`

| `data-testid` | Element |
|---|---|
| `activity-list` | Container. Its children are newest first in the DOM |
| `activity-item-{payment_id}` | One per visible payment. Carries `data-visibility="public"` or `data-visibility="private"` |
| `activity-parties-{payment_id}` | Text contains both handles |
| `activity-amount-{payment_id}` | Text is exactly the formatted amount |
| `activity-note-{payment_id}` | Text is exactly the note. Present even when the note is empty |
| `empty-activity` | Shown instead of the list when nothing is visible |

Two payments with equal timestamps may appear in either order.

## Requests — `/requests`

| `data-testid` | Element |
|---|---|
| `incoming-list`, `outgoing-list` | Containers |
| `request-item-{request_id}` | One per request. Carries `data-status="{status}"` |
| `request-amount-{request_id}` | Text is exactly the formatted amount |
| `request-pay-{request_id}` | Button. Present only on a `pending` incoming request |
| `request-decline-{request_id}` | Button. Present only on a `pending` incoming request |
| `request-cancel-{request_id}` | Button. Present only on a `pending` outgoing request |
| `request-error` | Shown when a pay, decline or cancel is refused |
| `empty-requests` | Shown when both lists are empty |

## Split — `/split`

| `data-testid` | Element |
|---|---|
| `split-amount` | Decimal input, same rule as `pay-amount` |
| `split-handles` | Text input: handles separated by commas, in order |
| `split-note`, `split-submit` | Input and button |
| `split-preview` | Shows the computed shares before submitting. Contains one `split-share-{handle}` per participant |
| `split-share-{handle}` | Text is exactly the formatted share amount |
| `split-error` | Error message, when the split is refused |

`split-preview` must show the shares the server would compute, by the rule in `stage-1.md`
§9, before anything is posted. The preview and submitted split must have identical shares.

After any successful action, the balance, the feed and the request lists on the same page must
show the new state without a manual reload. Navigation must wait for the write to succeed before
it refreshes the data. Any mechanism is fine, including a full navigation. **There is no
live-update requirement here** — another client may change state, but this browser need only
refresh after its own action or an explicit refresh.

## Competing clients and uncertain outcomes

- Add `wallet-refresh`, a button on `/` that refreshes the balance and feed without clearing
  the pay form. **Latest refresh wins:** a delayed earlier read must not overwrite a later
  refresh, including when responses arrive out of order.
- Another client may spend the balance after this browser reads it. A refused payment shows
  `pay-error`, refreshes the balance/feed, and preserves all pay inputs. A request cancelled
  elsewhere while its pay button is visible must show `request-error` when payment is refused
  and refresh the request list so the stale pay button disappears.
- If a payment response is lost, including after `POST /payments` commits, show `pay-uncertain`
  (nonempty text), not `pay-error`. Keep the unchanged form retryable with the **same key and
  body**. Successful retry removes both error/uncertainty elements, refreshes the balance and
  feed, and moves money exactly once. Unknown outcomes are not confirmed rejections.

No background polling, live synchronization, or recovery across page reloads is required.
The same balance refresh rules apply to the available and held amounts introduced below.

## Existing clients after an upgrade

A stage-2 service must accept an export produced by the same team's stage-1 service. A
browser signed in before that export/import upgrade must remain signed in afterwards.
Existing pending requests remain payable through the request screen. A payment whose response
was lost before export remains retryable after import with the same body and key; the UI
must recover the original payment and refresh the imported balance. These requirements
apply when import completes between browser requests; migration during an in-flight request
is not required. No page reload or new screen is required. The form and pending retry
identity must survive the upgrade.

## Authorizations and captures

A payment may be **authorised** now and **captured** later, for the full amount or less. An
authorisation places a *hold* on the payer's wallet: it reserves money without moving it. Capturing
moves the money; a final capture also releases whatever was not captured. Nonfinal captures
keep the remainder held. An open authorisation expires and releases its remainder on its own.

1. The sum of all wallet `total` values always equals the total seeded by the last reset.
   A hold moves no money; payments, settlements and captures transfer money between wallets.
2. `available = total − held` must never be negative. Held funds cannot fund new payments,
   authorizations or settlement net debits. Captures may spend the money reserved for them.
3. Cumulative captures must not exceed the authorized amount. Each idempotent capture moves
   money once. A closed hold cannot be captured again.

The existing API changes as follows:

- `GET /me` keeps `balance`, and `balance` **equals `total`**. `available` and `held` are new
  fields beside it. With no open holds, `balance`, `total` and `available` agree and `held` is
  zero, and every earlier behaviour is unchanged.
- `POST /payments` remains an immediate transfer. It must not leave an intermediate hold
  or require a separate capture.
- Every `409 insufficient_funds` in stage 1 — on `POST /payments`,
  `POST /requests/{id}/pay` and settlements — is now evaluated against `available`.
  With no open holds, the result is unchanged.
- Paying a request remains immediate. Authorizing a request is out of scope.
- `POST /splits` is unchanged.
- There are now seven idempotent write paths: stage 1's five, authorizations and captures.
  The same replay rules apply independently to each.

## Model

The fixture gains a service-wide default lifetime and an `authorizations` array.

```json
{
  "currency": "EUR",
  "minor_units": 2,
  "authorization_ttl_seconds": 600,
  "users": [ { "id": "u_ada", "handle": "ada", "balance": 10000, "...": "..." } ],
  "authorizations": [
    { "id": "a_1", "from_user_id": "u_ada", "to_user_id": "u_bob",
      "amount": 2000, "note": "deposit", "visibility": "public",
      "status": "open", "expires_at": "2026-09-24T13:20:00+00:00" }
  ]
}
```

- `authorization_ttl_seconds` applies to every authorisation created through the API. It defaults
  to 600 when omitted. If supplied, it must be a positive integer number of seconds.
  Seeded authorisations carry their own absolute `expires_at` instead.
- A user's seeded `balance` is still `total`. **`available` is derived, never seeded** — the service
  subtracts the seeded open holds itself.
- A sum of seeded unexpired open holds larger than that user's `balance` is a reset error:
  `422 validation_failed` from `POST /_test/reset`, changing nothing, exactly like a negative
  seeded balance.
- Seeded `status` is `open`, `captured`, `voided` or `expired`. Only `open` holds anything.
- An earlier fixture may omit `authorizations` altogether; omission means an empty list.

An authorization whose `expires_at` is at or before now is `expired` and holds no funds.
Reads and writes must reflect expiry even if no request occurred at the deadline.
`GET /authorizations` must show `status: "expired"`, and
`GET /me` must include the released remainder in `available`. Seeded expiry times are at
least an hour from reset time, in the past or future; newly created authorizations may
have shorter lifetimes.

## API

### `GET /me`

```json
{ "user_id": "u_ada", "display_name": "Ada", "handle": "ada",
  "balance": 10000, "total": 10000, "available": 8000, "held": 2000,
  "currency": "EUR", "minor_units": 2 }
```

`balance` and `total` are always equal. `held` is the sum of open holds, and `available` is
`total − held`, never negative.

### `POST /authorizations`

`Idempotency-Key` is required. The caller is the payer.

```json
{ "to_handle": "bob", "amount": 2000, "note": "deposit", "visibility": "private" }
```

`note` and `visibility` are optional with the same defaults as `POST /payments`.

```json
201
{
  "authorization_id": "a_4",
  "from_user_id": "u_ada", "from_handle": "ada",
  "to_user_id": "u_bob", "to_handle": "bob",
  "amount": 2000,
  "captured_amount": 0,
  "currency": "EUR",
  "note": "deposit",
  "visibility": "private",
  "status": "open",
  "expires_at": "2026-09-24T13:20:00+00:00",
  "payment_id": null,
  "created_at": "2026-09-24T13:10:00+00:00"
}
```

`expires_at` is `created_at` plus `authorization_ttl_seconds`.

| Case | Response |
|---|---|
| The caller's `available` is below `amount` | 409 `insufficient_funds` |
| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |
| `to_handle` is the caller's own handle | 422 `self_payment` |
| `note` over 200 characters, or `visibility` neither `public` nor `private` | 422 `validation_failed` |
| No user has that handle | 404 `not_found` |

An open authorisation is **not** a feed item and never appears in `GET /activity`.

### `POST /authorizations/{id}/capture`

`Idempotency-Key` is required. Only the receiver (the `to` party) may capture.

```json
{ "amount": 1500 }
```

`amount` is optional and defaults to the authorisation's remaining amount. As on
`POST /requests/{id}/pay`, **a replay must send the identical body** — `{}` and `{"amount": 2000}`
are different JSON values even when they mean the same capture, so reusing a key across the two is
409 `idempotency_key_reuse` per `stage-1.md` §7.

Returns `201` with the created **payment**, in exactly the shape `POST /payments` returns, with
`authorization_id` set to this authorisation and `request_id: null`. The payment's `amount` is the
captured amount; its `note` and `visibility` are copied from the authorisation; it appears in the
activity feed by the ordinary visibility rule. Payments created without an authorisation
carry `authorization_id: null`; their existing `request_id` semantics are unchanged.

By default the authorisation becomes `captured`, carries `captured_amount` and `payment_id`, and **releases the
uncaptured remainder immediately**: capturing 1500 of 2000 returns 500 to the payer's `available` in
the same step.

**Default: one final capture per authorisation.** A second capture after a final capture is
`409 authorization_not_open`.

**Extended capture mode.** To keep the remainder held, send `{"amount": 700, "final": false}`.
`final` is boolean, default `true`, so earlier single-capture requests retain their behavior.
With `final: false` and an uncaptured remainder, status stays `open`; further captures are
allowed up to that remainder. Capturing the entire remainder closes it even with `final: false`.
A final capture closes it and releases any remainder. `capture_exceeds_authorization` compares
with the **remaining** amount; omitted amount defaults to that remainder. `captured_amount` is
cumulative; `payment_id` is the latest capture; `payment_ids` lists every capture in order.
Every authorization response adds `remaining_amount`: the amount still held, zero when closed.
Void and expiry can close a partially captured authorization, release only the remainder,
and preserve all capture records. New fields do not change idempotency body equality.

| Case | Response |
|---|---|
| The authorisation is not `open` | 409 `authorization_not_open` |
| `expires_at` is at or before now | 409 `authorization_expired` |
| `amount` above the authorisation's uncaptured remainder | 422 `capture_exceeds_authorization` |
| `amount` below 1, or not an integer | 422 `validation_failed` |
| The caller is not the receiver | 403 `forbidden` |
| Unknown authorisation | 404 `not_found` |

### `POST /authorizations/{id}/void`

**Only the payer may void** — the `from` party releasing their own hold. No idempotency key, like
decline and cancel.

`200` with the authorisation, `status: "voided"`, the hold released. Voiding an already-voided
authorisation is `200` with the current state. A `captured` or `expired` one is
`409 authorization_not_open`.

For an existing authorization, capture and void return 403 `forbidden` when the caller
is not the permitted party, including callers who are neither party. `GET /authorizations`
returns only authorizations involving the caller.

### `GET /authorizations`

```http
GET /authorizations?direction=outgoing&status=open&limit=50&offset=0
```

Authorisations where the caller is the payer or the receiver, and no others. Newest first by
`created_at`.

- `direction` is `outgoing` (the caller is the payer), `incoming` (the caller is the receiver), or
  absent for both.
- `status` is one of the four statuses, or absent for all. An authorisation expired by the clock
  matches `expired`, never `open`.
- `limit`, `offset` and `has_more` behave exactly as on `GET /requests`.

## UI

A new route `/authorizations`, and the wallet gains two numbers. The UI and the API share
`/authorizations`: serve HTML for `Accept: text/html` and JSON otherwise, as for `/requests`.

| `data-testid` | Element |
|---|---|
| `wallet-balance` | Formatted `total`, retaining the existing display and `data-amount` |
| `wallet-available` | Formatted `available`, with `data-amount`. **Present this as the headline number** — it is what the user can actually spend |
| `wallet-held` | Formatted `held`, with `data-amount`. Absent when `held` is zero |
| `authorize-handle`, `authorize-amount`, `authorize-note`, `authorize-visibility`, `authorize-submit` | The authorise form. Same input rules as the pay form |
| `authorize-error` | Shown when the authorisation is refused, including insufficient available funds |
| `authorization-list` | Container on `/authorizations`. Children newest first in the DOM |
| `authorization-item-{authorization_id}` | Carries `data-status="{status}"` |
| `authorization-amount-{id}` | Text is exactly the formatted authorised amount |
| `authorization-captured-{id}` | Formatted captured amount. Present only when `status` is `captured` |
| `authorization-expires-{id}` | Text is the RFC 3339 `expires_at` |
| `authorization-capture-amount-{id}` | Decimal input, pre-filled with the remaining amount. Present only on an incoming `open` authorisation |
| `authorization-capture-{id}` | Button. Present only on an incoming `open` authorisation |
| `authorization-void-{id}` | Button. Present only on an outgoing `open` authorisation |
| `authorization-error` | Shown when a capture or a void is refused |
| `empty-authorizations` | Shown when the list is empty |

The UI must reflect seeded and newly created holds. Show available funds as the user's
spending balance, including immediately after reset with open holds.

## Concurrent operations

Concurrent requests must produce the same results as executing them one at a time in some
order, and the requirements above hold at every read.
