# Pocketful — Stage 4: refunds and batch corrections

Recipients can refund payments. Settlement operators can correct several payments in
one request, including payments that belong to a settlement. Existing receipts and saved
statements must remain available in their original form.

All requirements from stages 1–3 continue to apply. There are ten idempotent write paths:
stage 1's five, authorizations and captures from stage 2, corrections from stage 3, and refunds and
correction batches in this stage.

## Refunds and corrected history

`POST /payments/{payment_id}/refunds`, body `{"amount": 200}`, requires an idempotency key.
Only the original receiver may refund, else 403 `forbidden`; unknown payment is 404. The
target may be a direct payment, request payment or capture, but never a refund. Invalid amount
is 422 `validation_failed`. Refunds cumulatively may not exceed the payment's current corrected
amount: 422 `refund_exceeds_payment`. Refunds of refunds give 422 `invalid_refund_target`.

A refund is a new payment in the opposite direction, with `refund_of` naming the target,
`request_id: null`, `authorization_id: null`, and the original note/visibility. Return 201
with that payment; replay returns 200 with the original body. It moves existing money from
the receiver's **available** funds, or fails 409 `insufficient_funds`, atomically. Refunds
never reopen a request or authorization or restore a released hold. Other payments have
`refund_of: null`.

Stage-3 corrections remain available for ordinary direct/request payments. Captures and
refund payments cannot themselves be corrected: 422 `linked_payment_immutable`. A correction
cannot reduce a payment below its already-refunded amount: 422 `refund_exceeds_payment`.
Correction debits are checked against available funds.

## Batch corrections

`POST /correction-batches` requires a settlement operator and an idempotency key, with the
same 401/403 rules as settlements. Body:

```json
{"corrections": [{"payment_id": "p_a", "expected_revision": 1, "amount": 0,
                  "effective_at": "2026-09-20T12:00:00+00:00", "reason": "reversal"},
                 {"payment_id": "p_b", "expected_revision": 1, "amount": 0,
                  "effective_at": "2026-09-20T12:00:00+00:00", "reason": "reversal"}]}
```

corrections contains 1..32 objects with distinct payment_ids, else 422 `validation_failed`.
Every item has the ordinary correction fields and validation. Unknown payment is 404;
a stale expected revision is 409 `stale_revision`. The operator may correct ordinary,
request and settlement payments, but captures and refunds remain immutable. Correcting any
settlement member requires including every member of that settlement, else 422
`incomplete_settlement`. Members of one settlement must have identical effective instants
(offset spellings may differ), else 422 `validation_failed`. Ordinary single-payment
corrections remain available for nonmembers. Unknown fields are ignored.

Error precedence is: item errors in input order, settlement completeness, resulting
current available funds, then historical total and available funds at every effective/event
boundary. The existing codes apply: `linked_payment_immutable`, `refund_exceeds_payment`,
`insufficient_funds`, `historical_overdraft`. Affordability is determined by the combined
effect of all proposed revisions. A rejected batch leaves history, balances and idempotency
records unchanged.

Return 201 with `correction_batch_id`, `recorded_at` and `revisions` in input order. All new
revisions share recorded_at, strictly later than the previous recorded_at of every member;
each revision also exposes correction_batch_id. Effective times cannot be later than now.
Original payments and receipts never change. Original payment and settlement retries return
their original bodies. New statements reflect the new revisions; earlier snapshot tokens
continue to page their frozen entries. Replays return the original batch response with 200.
This adds one idempotent write path.

A settlement payment may be refunded under the existing refund rules, but refunds never
change settlement membership. Concurrent corrections sharing any expected payment revision
cannot
both succeed. A stage-4 service must accept exports produced by the same team's stages 1–3,
retaining settlement membership, corrections and snapshots.
