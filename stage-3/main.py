"""Pocketful Wallet Service - Stage 3 (Statements & Payment Corrections)"""
import re
import os
import json
import uuid
import datetime
from typing import Any, Optional, List, Dict, Tuple
from flask import Flask, request, jsonify, make_response, redirect, render_template_string

app = Flask(__name__)
app.secret_key = 'pocketful-stage3-secret-key'

# In-Memory State Store

CURRENCIES = {"EUR": 2, "JPY": 0, "BHD": 3}


class Store:
    def __init__(self):
        self.reset_default()

    def reset_default(self, fixture: Optional[dict] = None):
        self._reset_time = datetime.datetime.now(datetime.timezone.utc)
        self.currency = "EUR"
        self.minor_units = 2
        self.users: Dict[str, dict] = {}           # uid -> dict
        self.users_by_handle: Dict[str, str] = {}  # handle -> uid
        self.users_by_email: Dict[str, str] = {}   # email -> uid
        self.tokens: Dict[str, str] = {}           # token -> uid
        self.payments: List[dict] = []             # newest first
        self.requests: Dict[str, dict] = {}        # rid -> dict
        self.authorizations: Dict[str, dict] = {}  # aid -> dict
        self.idempotency: Dict[tuple, tuple] = {}  # (key, path) -> (status, body)
        # Stage 3 additions
        self.revisions: Dict[str, List[dict]] = {}  # payment_id -> [rev1, rev2, ...]
        self.snapshots: Dict[str, dict] = {}        # token -> snapshot dict
        # Stage 4 additions
        self.correction_batches: Dict[str, dict] = {}  # batch_id -> batch dict

        if fixture:
            self.currency = fixture.get("currency", "EUR")
            self.minor_units = fixture.get("minor_units", CURRENCIES.get(self.currency, 2))
            for u in fixture.get("users", []):
                self._add_user(
                    uid=u.get("id") or f"u_{u['handle']}",
                    email=u["email"],
                    password=u.get("password", "correct horse"),
                    display_name=u.get("display_name", u["handle"].title()),
                    handle=u["handle"],
                    balance=int(u.get("balance", u.get("total", 0)))
                )
            for p in fixture.get("payments", []):
                ts_raw = p.get("created_at")
                if ts_raw:
                    try:
                        ts = datetime.datetime.fromisoformat(ts_raw)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=datetime.timezone.utc)
                    except Exception:
                        ts = self._reset_time
                else:
                    ts = self._reset_time
                ts_iso = ts.isoformat()
                pid = p.get("payment_id") or p.get("id") or f"pay_{uuid.uuid4().hex[:12]}"
                _uid_to_handle = {uid: u.get("handle", "") for uid, u in self.users.items()}
                _from_handle = p.get("from_handle") or _uid_to_handle.get(p.get("from_user_id", ""), "")
                _to_handle = p.get("to_handle") or _uid_to_handle.get(p.get("to_user_id", ""), "")
                # Start with ALL fields from the original payment (preserves exported fields)
                pay = dict(p)
                # Override/normalize required fields
                pay["payment_id"] = pid
                pay["from_handle"] = _from_handle
                pay["to_handle"] = _to_handle
                pay["amount"] = int(p.get("amount", 0))
                pay["note"] = p.get("note", "")
                pay["visibility"] = p.get("visibility", "public")
                pay["created_at"] = ts_iso
                pay.setdefault("currency", self.currency)
                pay.setdefault("from_user_id", p.get("from_user_id", ""))
                pay.setdefault("to_user_id", p.get("to_user_id", ""))
                pay.setdefault("request_id", None)
                pay.setdefault("authorization_id", None)
                pay.setdefault("settlement_id", None)
                pay.setdefault("refund_of", None)
                # Remove internal metadata keys
                pay.pop("_vis_explicit", None)
                pay.pop("id", None)  # normalize id → payment_id
                self.payments.append(pay)
                # Revision 1 for each seeded payment
                self.revisions[pid] = [{
                    "payment_id": pid,
                    "revision": p.get("revision", 1),
                    "amount": int(p.get("amount", 0)),
                    "effective_at": ts_iso,
                    "recorded_at": ts_iso,
                    "reason": "",
                }]
            # Sort: keep newest first for activity feed
            self.payments.sort(key=lambda x: (x["created_at"], x["payment_id"]), reverse=True)

            # Apply settlement_operator_ids
            for op_uid in fixture.get("settlement_operator_ids", []):
                if op_uid in self.users:
                    self.users[op_uid]["role"] = "settlement_operator"

            for r in fixture.get("requests", []):
                rid = r.get("request_id") or r.get("id") or f"req_{uuid.uuid4().hex[:8]}"
                # Convert id-based fields to handle-based fields
                req_uid_map = {uid: u.get("handle", "") for uid, u in self.users.items()}
                req_payer_handle = r.get("payer_handle") or req_uid_map.get(r.get("payer_id", ""), "")
                req_requester_handle = r.get("requester_handle") or req_uid_map.get(r.get("requester_id", ""), "")
                normalized_req = {
                    "request_id": rid,
                    "requester_handle": req_requester_handle,
                    "payer_handle": req_payer_handle,
                    "amount": int(r.get("amount", 0)),
                    "currency": self.currency,
                    "note": r.get("note", ""),
                    "status": r.get("status", "pending"),
                    "payment_id": r.get("payment_id"),
                    "created_at": r.get("created_at", self._reset_time.isoformat()),
                }
                self.requests[rid] = normalized_req

            for a in fixture.get("authorizations", []):
                aid = a.get("authorization_id") or a.get("id") or f"auth_{uuid.uuid4().hex[:8]}"
                self.authorizations[aid] = a
        else:
            self._add_user("u_ada", "ada@example.com", "correct horse", "Ada", "ada", 10000)
            self._add_user("u_bob", "bob@example.com", "correct horse", "Bob", "bob", 2500)
            self._add_user("u_cy",  "cy@example.com",  "correct horse", "Cy",  "cy",  500)

    def _add_user(self, uid: str, email: str, password: str, display_name: str, handle: str, balance: int):
        user = {
            "id": uid,
            "email": email.lower(),
            "password": password,
            "display_name": display_name,
            "handle": handle.lower(),
            "total": balance,
            "held": 0,
        }
        self.users[uid] = user
        self.users_by_handle[user["handle"]] = uid
        self.users_by_email[user["email"]] = uid
        return user

    def export_state(self) -> dict:
        return {
            "currency": self.currency,
            "minor_units": self.minor_units,
            "users": list(self.users.values()),
            "payments": list(self.payments),
            "requests": list(self.requests.values()),
            "authorizations": list(self.authorizations.values()),
        }

    def import_state(self, data: dict):
        # Preserve active tokens so in-flight sessions survive import
        existing_tokens = dict(self.tokens)
        self.reset_default(data)
        # Re-add preserved tokens for users that still exist in imported state
        for token, uid in existing_tokens.items():
            if uid in self.users:
                self.tokens[token] = uid

    def _selected_revision(self, pid: str, known_at_dt: Optional[datetime.datetime]) -> Optional[dict]:
        """Get the active revision for a payment given known_at filter."""
        revs = self.revisions.get(pid, [])
        if not revs:
            return None
        if known_at_dt is None:
            return revs[-1]
        eligible = [r for r in revs
                    if datetime.datetime.fromisoformat(r["recorded_at"]) <= known_at_dt]
        if not eligible:
            return None
        return eligible[-1]

    def get_balance_at(self, user_handle: str,
                       as_of_dt: Optional[datetime.datetime] = None,
                       known_at_dt: Optional[datetime.datetime] = None) -> int:
        """
        Compute user balance at as_of instant, using revisions known at known_at.
        known_at=None means everything known now.
        as_of=None means now (use latest effective_at <= now).
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        effective_cutoff = as_of_dt if as_of_dt is not None else now

        uid = self.users_by_handle.get(user_handle)
        if not uid:
            return 0
        user = self.users[uid]

        # Compute running effect of ALL payments (to find zero_balance)
        all_effect = 0
        for p in self.payments:
            if p["from_handle"] != user_handle and p["to_handle"] != user_handle:
                continue
            rev = self._selected_revision(p["payment_id"], None)  # no known_at filter for "current"
            if rev is None:
                continue
            if p["from_handle"] == user_handle:
                all_effect -= rev["amount"]
            else:
                all_effect += rev["amount"]
        zero_balance = user["total"] - all_effect

        # Compute effect of payments at or before as_of, with known_at filter
        running = zero_balance
        for p in self.payments:
            if p["from_handle"] != user_handle and p["to_handle"] != user_handle:
                continue
            rev = self._selected_revision(p["payment_id"], known_at_dt)
            if rev is None:
                continue
            eff_dt = datetime.datetime.fromisoformat(rev["effective_at"])
            if eff_dt > effective_cutoff:
                continue
            if p["from_handle"] == user_handle:
                running -= rev["amount"]
            else:
                running += rev["amount"]
        return running

    def build_statement(self, user_handle: str,
                        from_dt: Optional[datetime.datetime],
                        to_dt: Optional[datetime.datetime],
                        known_at_dt: Optional[datetime.datetime] = None) -> Tuple[List[dict], int, int]:
        """
        Build full ordered statement entry list for user in half-open window [from_dt, to_dt).
        Returns (entries, opening_balance, closing_balance).
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        window_to = to_dt if to_dt is not None else now

        uid = self.users_by_handle.get(user_handle)
        if not uid:
            return [], 0, 0
        user = self.users[uid]

        # Compute zero_balance (balance before any payments)
        all_effect = 0
        for p in self.payments:
            if p["from_handle"] != user_handle and p["to_handle"] != user_handle:
                continue
            rev = self._selected_revision(p["payment_id"], None)
            if rev is None:
                continue
            if p["from_handle"] == user_handle:
                all_effect -= rev["amount"]
            else:
                all_effect += rev["amount"]
        zero_balance = user["total"] - all_effect

        # Select all entries with their active revision
        selected: List[Tuple[datetime.datetime, str, dict, dict]] = []
        for p in self.payments:
            if p["from_handle"] != user_handle and p["to_handle"] != user_handle:
                continue
            rev = self._selected_revision(p["payment_id"], known_at_dt)
            if rev is None:
                continue
            eff_dt = datetime.datetime.fromisoformat(rev["effective_at"])
            selected.append((eff_dt, p["payment_id"], p, rev))

        # Sort by effective_at asc, then payment_id asc (spec requirement)
        selected.sort(key=lambda x: (x[0], x[1]))

        # Opening balance = zero_balance + effect of all entries before from_dt
        running = zero_balance
        for (eff_dt, pid, p, rev) in selected:
            if from_dt is not None and eff_dt < from_dt:
                delta = rev["amount"] if p["to_handle"] == user_handle else -rev["amount"]
                running += delta
        opening_balance = running

        # Build window entries [from_dt, to_dt)
        entries = []
        bal = opening_balance
        for (eff_dt, pid, p, rev) in selected:
            if from_dt is not None and eff_dt < from_dt:
                continue
            if eff_dt >= window_to:
                continue
            delta = rev["amount"] if p["to_handle"] == user_handle else -rev["amount"]
            bal += delta
            pay_snap = dict(p)
            pay_snap["amount"] = rev["amount"]
            entry = {
                "payment": pay_snap,
                "delta": delta,
                "balance_after": bal,
                "revision": rev["revision"],
                "effective_at": rev["effective_at"],
                "recorded_at": rev["recorded_at"],
            }
            entries.append(entry)

        # Closing balance = zero_balance + effect of all entries before to_dt
        closing = zero_balance
        for (eff_dt, pid, p, rev) in selected:
            if eff_dt >= window_to:
                continue
            delta = rev["amount"] if p["to_handle"] == user_handle else -rev["amount"]
            closing += delta

        return entries, opening_balance, closing


store = Store()


# Helper functions

def error_json(code: str, message: str, status: int = 400):
    return jsonify({"error": {"code": code, "message": message}}), status

# Alias used by stage-4 specific endpoints
_err = error_json


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fmt_payment(p: dict) -> dict:
    """Return payment dict for API response (identity for in-memory store)."""
    return p


def format_money(minor: int, minor_units: int, currency: str) -> str:
    if minor_units == 0:
        return f"{minor} {currency}"
    text = str(minor).rjust(minor_units + 1, "0")
    return f"{text[:-minor_units]}.{text[-minor_units:]} {currency}"


def get_current_user_from_req() -> Optional[dict]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        uid = store.tokens.get(token)
        if uid and uid in store.users:
            return store.users[uid]
    token = request.cookies.get("pocketful_token")
    if token and token in store.tokens:
        uid = store.tokens[token]
        if uid and uid in store.users:
            return store.users[uid]
    return None


def _require_auth():
    """Returns uid string if authenticated, or (response, status) tuple if not."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        uid = store.tokens.get(token)
        if uid and uid in store.users:
            return uid
    return (jsonify({"error": {"code": "unauthorized", "message": "Authentication required"}}), 401)


def parse_decimal_to_minor(val_str: str, minor_units: int) -> Optional[int]:
    val_str = val_str.strip()
    if not val_str:
        return None
    if not re.match(r"^\d+(\.\d+)?$", val_str):
        return None
    parts = val_str.split(".")
    whole = int(parts[0])
    if len(parts) == 1:
        return whole * (10 ** minor_units)
    decimals = parts[1]
    if len(decimals) > minor_units:
        return None
    decimals_padded = decimals.ljust(minor_units, "0")
    return whole * (10 ** minor_units) + int(decimals_padded)


def parse_rfc3339(val: Optional[str]) -> Optional[datetime.datetime]:
    """Parse RFC 3339 instant; returns None if invalid or missing timezone."""
    if val is None or not val.strip():
        return None
    try:
        dt = datetime.datetime.fromisoformat(val.strip())
        if dt.tzinfo is None:
            return None  # Must have timezone offset
        return dt
    except Exception:
        return None


# Test Harness Endpoints

@app.route("/_test/reset", methods=["POST"])
def test_reset():
    fixture = request.get_json(silent=True) or {}
    # Validate no negative balances
    for u in fixture.get("users", []):
        if int(u.get("balance", u.get("total", 0))) < 0:
            return error_json("validation_failed", "Negative balance in fixture", 422)
    # Validate seeded payment timestamps
    for p in fixture.get("payments", []):
        ts_raw = p.get("created_at")
        if ts_raw:
            try:
                ts = datetime.datetime.fromisoformat(ts_raw)
                if ts.tzinfo is None:
                    return error_json("validation_failed",
                                      "Seeded payment created_at must include timezone offset", 422)
                if ts > datetime.datetime.now(datetime.timezone.utc):
                    return error_json("validation_failed",
                                      "Seeded payment created_at must not be in the future", 422)
            except Exception:
                return error_json("validation_failed", "Invalid created_at in seeded payment", 422)
    store.reset_default(fixture)
    return "", 204


@app.route("/_test/export", methods=["GET"])
def test_export():
    return jsonify(store.export_state()), 200


@app.route("/_test/import", methods=["POST"])
def test_import():
    data = request.get_json(silent=True) or {}
    store.import_state(data)
    return "", 204


# Auth Endpoints

@app.route("/auth/signup", methods=["POST"])
def auth_signup_api():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()

    if not email or not password or not display_name:
        return error_json("validation_failed", "Missing required fields", 422)
    if email in store.users_by_email:
        return error_json("email_taken", "Email already in use", 409)
    # Check handle uniqueness
    raw_handle = re.sub(r"[^a-z0-9_]", "_", re.sub(r"[^a-z0-9_@]", "", email.split("@")[0].lower()))[:20]
    _base_handle = raw_handle
    if _base_handle in store.users_by_handle:
        return error_json("handle_taken", f"Handle @{_base_handle} is already taken", 409)

    raw_handle = re.sub(r"[^a-zA-Z0-9_]", "_", email.split("@")[0].lower())[:20]
    handle = raw_handle if raw_handle not in store.users_by_handle \
        else f"{raw_handle[:15]}_{uuid.uuid4().hex[:4]}"
    uid = f"u_{handle}"
    user = store._add_user(uid, email, password, display_name, handle, balance=0)
    token = f"tok_{uuid.uuid4().hex}"
    store.tokens[token] = uid
    return jsonify({"token": token, "user": user}), 201


@app.route("/auth/login", methods=["POST"])
def auth_login_api():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    uid = store.users_by_email.get(email)
    if not uid or store.users[uid]["password"] != password:
        return error_json("unauthenticated", "Invalid credentials", 401)
    token = f"tok_{uuid.uuid4().hex}"
    store.tokens[token] = uid
    return jsonify({"token": token, "user": store.users[uid]}), 200


# GET /me  -  with optional as_of and known_at (Stage 3)

@app.route("/me", methods=["GET"])
def get_me():
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    as_of_raw = request.args.get("as_of")
    known_at_raw = request.args.get("known_at")

    as_of_dt = None
    known_at_dt = None

    if as_of_raw is not None:
        as_of_dt = parse_rfc3339(as_of_raw)
        if as_of_dt is None:
            return error_json("validation_failed",
                              "as_of must be an RFC 3339 instant with timezone offset", 422)

    if known_at_raw is not None:
        known_at_dt = parse_rfc3339(known_at_raw)
        if known_at_dt is None:
            return error_json("validation_failed",
                              "known_at must be an RFC 3339 instant with timezone offset", 422)

    total = user["total"]
    held = user["held"]

    if as_of_dt is not None or known_at_dt is not None:
        balance = store.get_balance_at(user["handle"], as_of_dt=as_of_dt, known_at_dt=known_at_dt)
        available = max(0, balance - held)
        resp = {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "handle": user["handle"],
            "balance": balance,
            "total": balance,
            "available": available,
            "held": held,
        }
        if as_of_raw is not None:
            resp["as_of"] = as_of_raw
        if known_at_raw is not None:
            resp["known_at"] = known_at_raw
        resp["currency"] = store.currency
        resp["minor_units"] = store.minor_units
        return jsonify(resp), 200

    available = max(0, total - held)
    return jsonify({
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "handle": user["handle"],
        "balance": total,
        "total": total,
        "available": available,
        "held": held,
        "currency": store.currency,
        "minor_units": store.minor_units,
    }), 200


# Payments Endpoints

@app.route("/payments", methods=["POST"])
def create_payment():
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_json("missing_idempotency_key", "Idempotency-Key header is required", 400)
    if len(key) > 255:
        return error_json("validation_failed", "Idempotency-Key must be 1-255 characters", 422)

    idem_tuple = (user["id"], key, "/payments")
    if idem_tuple in store.idempotency:
        _, body = store.idempotency[idem_tuple]
        # Check for key reuse with different params
        _req = request.get_json(silent=True) or {}
        _req_handle = (_req.get("to_handle") or "").strip()
        if _req_handle and body.get("to_handle") != _req_handle.lower():
            return _err("idempotency_key_reuse", "Idempotency key reused with different request", 409)
        _req_amt = _req.get("amount")
        if isinstance(_req_amt, float) and _req_amt == int(_req_amt):
            _req_amt = int(_req_amt)
        if _req_amt is not None and body.get("amount") != _req_amt:
            return _err("idempotency_key_reuse", "Idempotency key reused with different request", 409)
        _req_vis = _req.get("visibility")
        _orig_vis_explicit = body.get("_vis_explicit", False)
        _new_vis_explicit = "visibility" in _req
        if _orig_vis_explicit != _new_vis_explicit:
            return _err("idempotency_key_reuse", "Idempotency key reused with different request", 409)
        if _new_vis_explicit and body.get("visibility") != _req_vis:
            return _err("idempotency_key_reuse", "Idempotency key reused with different request", 409)
        _replay_body = {k: v for k, v in body.items() if not k.startswith("_")}
        return jsonify(_replay_body), 200  # replay always 200

    _raw = request.get_data(as_text=True)
    if _raw and _raw.strip():
        try:
            data = request.get_json(force=True, silent=False)
            if data is None:
                return _err("malformed_request", "Request body is not valid JSON", 400)
        except Exception:
            return _err("malformed_request", "Request body is not valid JSON", 400)
    else:
        data = {}
    # Do NOT lowercase to_handle before validation - preserve original for regex check
    to_handle_raw = (data.get("to_handle") or "").strip()
    amount = data.get("amount")
    note = data.get("note", "")
    visibility = data.get("visibility", "public")

    if not to_handle_raw or amount is None:
        return error_json("validation_failed", "Missing required fields", 422)
    # Accept float that is exactly an integer
    if isinstance(amount, float) and amount == int(amount):
        amount = int(amount)
    if not isinstance(amount, int) or amount <= 0 or amount > 1_000_000_000:
        return error_json("validation_failed", "Invalid amount", 422)
    if note is None:
        return _err("validation_failed", "Note must be a string, not null", 422)
    note = str(note) if note else ""
    if len(note) > 200:
        return error_json("validation_failed", "Note exceeds 200 characters", 422)
    if visibility not in ("public", "private"):
        return error_json("validation_failed", "Invalid visibility", 422)
    # Validate handle format (must be lowercase - reject uppercase)
    if not re.match(r"^[a-z0-9_]{1,20}$", to_handle_raw):
        return error_json("not_found", f"Unknown handle: {to_handle_raw}", 404)
    to_handle = to_handle_raw  # already validated as lowercase

    recipient_uid = store.users_by_handle.get(to_handle)
    if not recipient_uid:
        return error_json("not_found", f"Unknown handle: {to_handle}", 404)

    # self_payment check
    if recipient_uid == user["id"]:
        return error_json("self_payment", "Cannot pay yourself", 422)

    recipient = store.users[recipient_uid]
    available = max(0, user["total"] - user["held"])
    if available < amount:
        return error_json("insufficient_funds", "Insufficient available funds", 409)

    user["total"] -= amount
    recipient["total"] += amount

    pid = f"pay_{uuid.uuid4().hex[:12]}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payment = {
        "payment_id": pid,
        "from_handle": user["handle"],
        "to_handle": recipient["handle"],
        "from_user_id": user["id"],
        "to_user_id": recipient_uid,
        "amount": amount,
        "currency": store.currency,
        "note": note,
        "visibility": visibility,
        "created_at": created_at,
        "refund_of": None,
        "request_id": None,
        "authorization_id": None,
        "settlement_id": None,
    }
    store.payments.insert(0, payment)

    # Revision 1
    store.revisions[pid] = [{
        "payment_id": pid,
        "revision": 1,
        "amount": amount,
        "effective_at": created_at,
        "recorded_at": created_at,
        "reason": "",
    }]

    # Store idempotency copy with vis_explicit marker (don't pollute store.payments)
    _idem_copy = dict(payment)
    _idem_copy["_vis_explicit"] = "visibility" in data
    store.idempotency[idem_tuple] = (201, _idem_copy)
    return jsonify(payment), 201


@app.route("/activity", methods=["GET"])
@app.route("/payments", methods=["GET"])
def get_activity():
    user = get_current_user_from_req()  # Optional auth

    # Validate paging params (applies regardless of auth)
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        return error_json("validation_failed", "limit and offset must be integers", 422)
    if not (1 <= limit <= 200):
        return error_json("validation_failed", "limit must be 1-200", 422)
    if offset < 0:
        return error_json("validation_failed", "offset must be >= 0", 422)

    visible = []
    for p in store.payments:
        if p["visibility"] == "public":
            visible.append(p)
        elif user and (p["from_handle"] == user["handle"] or p["to_handle"] == user["handle"]):
            visible.append(p)
    total_v = len(visible)
    paged_v = visible[offset:offset + limit]
    has_more_v = (offset + limit) < total_v
    return jsonify({"payments": paged_v, "has_more": has_more_v}), 200


# Payment Corrections  (Stage 3)

@app.route("/payments/<payment_id>/corrections", methods=["POST"])
def create_correction(payment_id):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_json("validation_failed", "Idempotency-Key header is required", 422)

    # Find payment
    payment = None
    for p in store.payments:
        if p["payment_id"] == payment_id:
            payment = p
            break
    if payment is None:
        return error_json("not_found", "Payment not found", 404)

    # Only original sender can correct
    if payment["from_handle"] != user["handle"]:
        return error_json("forbidden", "Only the original sender can correct this payment", 403)

    # Reject linked payments (settlement members, captures)
    if payment.get("settlement_id") or payment.get("refund_of"):
        return error_json("linked_payment_immutable", "Cannot correct a linked payment", 422)

    idem_path = f"/payments/{payment_id}/corrections"
    idem_tuple = (key, idem_path)
    if idem_tuple in store.idempotency:
        stored_status, stored_body = store.idempotency[idem_tuple]
        return jsonify(stored_body), stored_status

    data = request.get_json(silent=True) or {}
    expected_revision = data.get("expected_revision")
    new_amount = data.get("amount")
    effective_at_raw = data.get("effective_at")
    reason = data.get("reason")

    if expected_revision is None or new_amount is None or not effective_at_raw or not reason:
        return error_json("validation_failed",
                          "All fields required: expected_revision, amount, effective_at, reason", 422)
    if not isinstance(expected_revision, int) or expected_revision < 1:
        return error_json("validation_failed", "expected_revision must be a positive integer", 422)
    if not isinstance(new_amount, int) or new_amount < 0 or new_amount > 1_000_000_000:
        return error_json("validation_failed", "amount must be an integer 0..1000000000", 422)
    if not isinstance(reason, str) or len(reason) < 1 or len(reason) > 200:
        return error_json("validation_failed", "reason must be 1..200 characters", 422)

    effective_at_dt = parse_rfc3339(effective_at_raw)
    if effective_at_dt is None:
        return error_json("validation_failed",
                          "effective_at must be an RFC 3339 instant with timezone offset", 422)
    now = datetime.datetime.now(datetime.timezone.utc)
    if effective_at_dt > now:
        return error_json("validation_failed", "effective_at must not be later than now", 422)

    revs = store.revisions.get(payment_id, [])
    current_revision = len(revs)

    # Stale revision check
    if expected_revision != current_revision:
        return error_json("stale_revision",
                          f"Expected revision {expected_revision} but current is {current_revision}", 409)

    old_amount = revs[-1]["amount"] if revs else 0
    delta = new_amount - old_amount  # positive = sender pays more

    sender_uid = store.users_by_handle.get(payment["from_handle"])
    receiver_uid = store.users_by_handle.get(payment["to_handle"])
    if not sender_uid or not receiver_uid:
        return error_json("not_found", "Payment parties not found", 404)

    sender = store.users[sender_uid]
    receiver = store.users[receiver_uid]

    # Check current funds before historical check
    if delta > 0:
        sender_avail = max(0, sender["total"] - sender["held"])
        if sender_avail < delta:
            return error_json("insufficient_funds", "Sender has insufficient available funds", 409)
    elif delta < 0:
        receiver_avail = max(0, receiver["total"] - receiver["held"])
        if receiver_avail < abs(delta):
            return error_json("insufficient_funds", "Receiver has insufficient available funds", 409)

    # Apply correction to current balances
    sender["total"] -= delta
    receiver["total"] += delta

    # Add the new revision (temporarily, for historical overdraft check)
    new_recorded_at = now.isoformat()
    new_revision_num = current_revision + 1
    new_rev = {
        "payment_id": payment_id,
        "revision": new_revision_num,
        "amount": new_amount,
        "effective_at": effective_at_raw,
        "recorded_at": new_recorded_at,
        "reason": reason,
    }
    store.revisions[payment_id].append(new_rev)

    # Historical overdraft check: compute balance at each effective-time boundary
    all_effective_times: set = set()
    for p in store.payments:
        for rv in store.revisions.get(p["payment_id"], []):
            t = parse_rfc3339(rv["effective_at"])
            if t:
                all_effective_times.add(t)
    all_effective_times.add(effective_at_dt)

    overdraft = False
    for t_dt in sorted(all_effective_times):
        for uid, u in store.users.items():
            bal = store.get_balance_at(u["handle"], as_of_dt=t_dt)
            if bal < 0:
                overdraft = True
                break
        if overdraft:
            break

    if overdraft:
        # Rollback
        store.revisions[payment_id].pop()
        sender["total"] += delta
        receiver["total"] -= delta
        return error_json("historical_overdraft",
                          "Correction would cause a historical overdraft", 409)

    result = {
        "payment_id": payment_id,
        "revision": new_revision_num,
        "amount": new_amount,
        "effective_at": effective_at_raw,
        "recorded_at": new_recorded_at,
        "reason": reason,
    }
    store.idempotency[idem_tuple] = (201, result)
    return jsonify(result), 201


@app.route("/payments/<payment_id>/revisions", methods=["GET"])
def get_payment_revisions(payment_id):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    payment = None
    for p in store.payments:
        if p["payment_id"] == payment_id:
            payment = p
            break

    if payment is None:
        return error_json("not_found", "Payment not found", 404)

    # Only parties can read revisions; third party gets 404 even for public payments
    if payment["from_handle"] != user["handle"] and payment["to_handle"] != user["handle"]:
        return error_json("not_found", "Payment not found", 404)

    revs = store.revisions.get(payment_id, [])
    return jsonify({"revisions": revs}), 200


# Statement  (Stage 3) - with snapshot pagination

@app.route("/statement", methods=["GET"])
def get_statement():
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    snapshot_token = request.args.get("snapshot")

    if snapshot_token:
        # Paging a frozen snapshot - only limit and offset allowed
        if request.args.get("from") or request.args.get("to") or request.args.get("known_at"):
            return error_json("validation_failed",
                              "Cannot use from, to, or known_at with snapshot", 422)
        snap = store.snapshots.get(snapshot_token)
        if snap is None or snap["uid"] != user["id"] or snap.get("reset_time") != store._reset_time:
            return error_json("not_found", "Snapshot not found or expired", 404)

        limit = request.args.get("limit", type=int, default=50)
        offset = request.args.get("offset", type=int, default=0)
        all_entries = snap["entries"]
        page = all_entries[offset:offset + limit]
        has_more = (offset + limit) < len(all_entries)
        return jsonify({
            "opening_balance": snap["opening_balance"],
            "entries": page,
            "closing_balance": snap["closing_balance"],
            "has_more": has_more,
            "snapshot": snapshot_token,
        }), 200

    # Normal statement query
    from_raw = request.args.get("from")
    to_raw = request.args.get("to")
    known_at_raw = request.args.get("known_at")

    from_dt = None
    to_dt = None
    known_at_dt = None

    if from_raw is not None:
        from_dt = parse_rfc3339(from_raw)
        if from_dt is None:
            return error_json("validation_failed",
                              "from must be an RFC 3339 instant with timezone offset", 422)
    if to_raw is not None:
        to_dt = parse_rfc3339(to_raw)
        if to_dt is None:
            return error_json("validation_failed",
                              "to must be an RFC 3339 instant with timezone offset", 422)
    if known_at_raw is not None:
        known_at_dt = parse_rfc3339(known_at_raw)
        if known_at_dt is None:
            return error_json("validation_failed",
                              "known_at must be an RFC 3339 instant with timezone offset", 422)

    limit = request.args.get("limit", type=int, default=50)
    offset = request.args.get("offset", type=int, default=0)

    all_entries, opening_balance, closing_balance = store.build_statement(
        user["handle"], from_dt, to_dt, known_at_dt
    )

    # Create immutable snapshot
    token = f"snap_{uuid.uuid4().hex}"
    store.snapshots[token] = {
        "uid": user["id"],
        "entries": all_entries,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "reset_time": store._reset_time,
    }

    page = all_entries[offset:offset + limit]
    has_more = (offset + limit) < len(all_entries)

    resp: dict = {
        "opening_balance": opening_balance,
        "entries": page,
        "closing_balance": closing_balance,
        "has_more": has_more,
        "snapshot": token,
    }
    if known_at_raw is not None:
        resp["known_at"] = known_at_raw

    return jsonify(resp), 200


# Requests Endpoints

@app.route("/requests", methods=["GET", "POST"])
def handle_requests():
    user = get_current_user_from_req()

    if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
        return render_requests_ui(user)

    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    if request.method == "POST":
        key = request.headers.get("Idempotency-Key")
        if not key:
            return error_json("missing_idempotency_key", "Idempotency-Key required", 400)
        if len(key) > 255:
            return error_json("validation_failed", "Idempotency-Key must be 1-255 characters", 422)

        idem_tuple = (user["id"], key, "/requests")
        if idem_tuple in store.idempotency:
            status_code, body = store.idempotency[idem_tuple]
            return jsonify(body), status_code

        data = request.get_json(silent=True) or {}
        payer_handle = (data.get("payer_handle") or "").strip().lower()
        amount = data.get("amount")
        note = data.get("note", "") or ""

        if not payer_handle or amount is None:
            return error_json("validation_failed", "payer_handle and amount required", 422)
        if not isinstance(amount, int) or amount <= 0 or amount > 1_000_000_000:
            return error_json("validation_failed", "Invalid amount", 422)
        if len(note) > 200:
            return error_json("validation_failed", "Note too long", 422)

        payer_uid = store.users_by_handle.get(payer_handle)
        if not payer_uid:
            return error_json("not_found", f"User not found: {payer_handle}", 404)
        if payer_uid == user["id"]:
            return error_json("self_request", "Cannot request money from yourself", 422)

        rid = f"req_{uuid.uuid4().hex[:12]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        req_obj = {
            "request_id": rid,
            "requester_handle": user["handle"],
            "payer_handle": payer_handle,
            "amount": amount,
            "currency": store.currency,
            "note": note,
            "status": "pending",
            "payment_id": None,
            "created_at": created_at,
        }
        store.requests[rid] = req_obj
        store.idempotency[idem_tuple] = (201, req_obj)
        return jsonify(req_obj), 201

    # GET /requests - validate query params
    direction = request.args.get("direction", "all").lower()
    status_filter = request.args.get("status", "all").lower()
    _valid_dir = ("all", "incoming", "outgoing")
    if direction not in _valid_dir:
        return error_json("validation_failed", f"direction must be one of {_valid_dir}", 422)
    _valid_status = ("all", "pending", "paid", "declined", "cancelled")
    if status_filter not in _valid_status:
        return error_json("validation_failed", f"status must be one of {_valid_status}", 422)
    try:
        _limit = int(request.args.get("limit", 200))
        _offset = int(request.args.get("offset", 0))
        if not (1 <= _limit <= 200):
            return error_json("validation_failed", "limit must be 1-200", 422)
        if _offset < 0:
            return error_json("validation_failed", "offset must be >= 0", 422)
    except (ValueError, TypeError):
        return error_json("validation_failed", "limit and offset must be integers", 422)

    incoming = [r for r in store.requests.values() if r["payer_handle"] == user["handle"]]
    outgoing = [r for r in store.requests.values() if r["requester_handle"] == user["handle"]]

    if direction == "incoming":
        results = incoming
    elif direction == "outgoing":
        results = outgoing
    else:
        results = list({r["request_id"]: r for r in incoming + outgoing}.values())

    if status_filter != "all":
        results = [r for r in results if r.get("status") == status_filter]

    total_results = len(results)
    paged = results[_offset:_offset + _limit]
    has_more = (_offset + _limit) < total_results
    return jsonify({"requests": paged, "incoming": incoming, "outgoing": outgoing, "has_more": has_more}), 200


@app.route("/requests/<rid>/pay", methods=["POST"])
def pay_request(rid: str):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_json("missing_idempotency_key", "Idempotency-Key header is required", 400)

    idem_tuple = (user["id"], key, f"/requests/{rid}/pay")
    if idem_tuple in store.idempotency:
        _, body = store.idempotency[idem_tuple]
        # Idempotency key reuse check for visibility change
        _req_data = request.get_json(silent=True) or {}
        _orig_vis_explicit = body.get("_vis_explicit", False)
        _new_vis_explicit = "visibility" in _req_data
        _new_vis = _req_data.get("visibility", "public")
        if _orig_vis_explicit != _new_vis_explicit:
            return _err("idempotency_key_reuse", "Idempotency key reused with different request", 409)
        if _new_vis_explicit and body.get("visibility") != _new_vis:
            return _err("idempotency_key_reuse", "Idempotency key reused with different request", 409)
        _replay = {k: v for k, v in body.items() if not k.startswith("_")}
        return jsonify(_replay), 200  # replay = 200

    req_obj = store.requests.get(rid)
    if not req_obj:
        return error_json("not_found", "Request not found", 404)
    if req_obj["payer_handle"] != user["handle"]:
        return error_json("forbidden", "Only payer can pay request", 403)
    if req_obj["status"] != "pending":
        return error_json("request_not_pending", f"Request is {req_obj['status']}, not pending", 409)

    amount = req_obj["amount"]
    available = max(0, user["total"] - user["held"])
    if available < amount:
        return error_json("insufficient_funds", "Insufficient funds", 409)

    requester_uid = store.users_by_handle[req_obj["requester_handle"]]
    requester = store.users[requester_uid]
    user["total"] -= amount
    requester["total"] += amount
    req_obj["status"] = "paid"
    _req_data2 = request.get_json(silent=True) or {}
    _req_vis_explicit = "visibility" in _req_data2
    _req_vis = _req_data2.get("visibility", "public")
    if _req_vis not in ("public", "private"):
        _req_vis = "public"
        _req_vis_explicit = False

    pid = f"pay_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payment = {
        "payment_id": pid,
        "from_handle": user["handle"],
        "to_handle": requester["handle"],
        "from_user_id": user["id"],
        "to_user_id": requester_uid,
        "amount": amount,
        "currency": store.currency,
        "note": req_obj.get("note", ""),
        "visibility": _req_vis,
        "created_at": now,
        "refund_of": None,
        "request_id": rid,
        "authorization_id": None,
        "settlement_id": None,
    }
    req_obj["payment_id"] = pid
    store.payments.insert(0, payment)
    store.revisions[pid] = [{
        "payment_id": pid, "revision": 1, "amount": amount,
        "effective_at": now, "recorded_at": now, "reason": "",
    }]
    # Store idempotency copy with vis_explicit marker (don't pollute store.payments)
    _idem_copy = dict(payment)
    _idem_copy["_vis_explicit"] = _req_vis_explicit
    store.idempotency[idem_tuple] = (201, _idem_copy)
    return jsonify(payment), 201


@app.route("/requests/<rid>/decline", methods=["POST"])
def decline_request(rid: str):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)
    req_obj = store.requests.get(rid)
    if not req_obj:
        return error_json("not_found", "Request not found", 404)
    if req_obj["payer_handle"] != user["handle"]:
        return error_json("forbidden", "Only payer can decline", 403)
    # Idempotent: already declined is OK
    if req_obj["status"] == "declined":
        return jsonify(req_obj), 200
    if req_obj["status"] != "pending":
        return error_json("request_not_pending", f"Request is {req_obj['status']}, not pending", 409)
    req_obj["status"] = "declined"
    return jsonify(req_obj), 200


@app.route("/requests/<rid>/cancel", methods=["POST"])
def cancel_request(rid: str):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)
    req_obj = store.requests.get(rid)
    if not req_obj:
        return error_json("not_found", "Request not found", 404)
    if req_obj["requester_handle"] != user["handle"]:
        return error_json("forbidden", "Only requester can cancel", 403)
    # Idempotent: already cancelled is OK
    if req_obj["status"] == "cancelled":
        return jsonify(req_obj), 200
    if req_obj["status"] != "pending":
        return error_json("request_not_pending", f"Request is {req_obj['status']}, not pending", 409)
    req_obj["status"] = "cancelled"
    return jsonify(req_obj), 200


# Splits

@app.route("/splits", methods=["POST"])
def create_split():
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_json("missing_idempotency_key", "Idempotency-Key header is required", 400)

    idem_tuple = (user["id"], key, "/splits")
    if idem_tuple in store.idempotency:
        _, body = store.idempotency[idem_tuple]
        return jsonify(body), 200

    data = request.get_json(silent=True) or {}
    amount = data.get("amount")
    handles = data.get("participant_handles", [])
    note = data.get("note", "") or ""
    visibility = data.get("visibility", "public")

    if amount is None or not isinstance(amount, int) or amount <= 0 or amount > 1_000_000_000:
        return error_json("validation_failed", "Invalid amount", 422)
    if not handles or not isinstance(handles, list):
        return error_json("validation_failed", "participant_handles required", 422)
    if len(handles) < 1:
        return error_json("validation_failed", "At least 1 participant required", 422)
    if len(handles) > 200:
        return error_json("validation_failed", "Too many participants", 422)
    if len(set(h.lower() for h in handles)) != len(handles):
        return error_json("validation_failed", "Duplicate participant handles", 422)

    # Validate all handles exist
    for h in handles:
        if not store.users_by_handle.get(h.lower()):
            return error_json("not_found", f"Unknown handle: {h}", 404)

    n = len(handles)
    base = amount // n
    remainder = amount - base * n
    shares = [{"handle": h.lower(), "amount": base + (1 if i < remainder else 0)}
              for i, h in enumerate(handles)]

    # Create requests for all participants except the caller
    requests_out = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for share in shares:
        if share["handle"] == user["handle"]:
            continue
        rid = f"req_{uuid.uuid4().hex[:12]}"
        req_obj = {
            "request_id": rid,
            "requester_handle": user["handle"],
            "payer_handle": share["handle"],
            "amount": share["amount"],
            "note": note,
            "status": "pending",
            "payment_id": None,
            "currency": store.currency,
            "created_at": now,
        }
        store.requests[rid] = req_obj
        requests_out.append(req_obj)

    body = {"shares": shares, "requests": requests_out}
    store.idempotency[idem_tuple] = (201, body)
    return jsonify(body), 201


# Settlements

@app.route("/settlements", methods=["POST"])
def create_settlement():
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    if user.get("role") not in ("settlement_operator", "operator"):
        return error_json("forbidden", "Only settlement operators may submit settlements", 403)

    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_json("missing_idempotency_key", "Idempotency-Key header is required", 400)

    idem_tuple = (key, "/settlements")
    if idem_tuple in store.idempotency:
        _, body = store.idempotency[idem_tuple]
        return jsonify(body), 200

    data = request.get_json(silent=True) or {}
    transfers = data.get("transfers", [])
    if not transfers:
        return error_json("validation_failed", "transfers required", 422)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sid = f"set_{uuid.uuid4().hex[:12]}"
    payments_out = []

    for t in transfers:
        from_h = (t.get("from_handle") or "").lower()
        to_h = (t.get("to_handle") or "").lower()
        amt = t.get("amount")
        if not from_h or not to_h or amt is None:
            return error_json("validation_failed", "Each transfer needs from_handle, to_handle, amount", 422)
        from_uid = store.users_by_handle.get(from_h)
        to_uid = store.users_by_handle.get(to_h)
        if not from_uid:
            return error_json("not_found", f"Unknown handle: {from_h}", 404)
        if not to_uid:
            return error_json("not_found", f"Unknown handle: {to_h}", 404)
        store.users[from_uid]["total"] -= amt
        store.users[to_uid]["total"] += amt
        pid = f"pay_{uuid.uuid4().hex[:12]}"
        payment = {
            "payment_id": pid,
            "from_handle": from_h,
            "to_handle": to_h,
            "from_user_id": from_uid,
            "to_user_id": to_uid,
            "amount": amt,
            "currency": store.currency,
            "note": "",
            "visibility": "private",
            "created_at": now,
            "refund_of": None,
            "request_id": None,
            "authorization_id": None,
            "settlement_id": sid,
        }
        store.payments.insert(0, payment)
        payments_out.append(payment)

    body = {"settlement_id": sid, "payments": payments_out, "recorded_at": now}
    store.idempotency[idem_tuple] = (201, body)
    return jsonify(body), 201


# Authorizations & Holds (Stage 2, extended for Stage 3 history)

@app.route("/authorizations", methods=["POST"])
def create_authorization():
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    key = request.headers.get("Idempotency-Key")
    if not key:
        return error_json("validation_failed", "Idempotency-Key required", 422)

    data = request.get_json(silent=True) or {}
    recipient_handle = (data.get("to_handle") or data.get("recipient_handle") or "").strip().lower()
    amount = data.get("amount")
    note = data.get("note", "")

    if not recipient_handle or amount is None or not isinstance(amount, int) or amount <= 0:
        return error_json("validation_failed", "Invalid authorization parameters", 422)
    if recipient_handle not in store.users_by_handle:
        return error_json("not_found", "Recipient not found", 404)

    available = max(0, user["total"] - user["held"])
    if available < amount:
        return error_json("insufficient_funds", "Insufficient available funds for hold", 409)

    user["held"] += amount
    aid = f"auth_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    auth_obj = {
        "authorization_id": aid,
        "payer_handle": user["handle"],
        "recipient_handle": recipient_handle,
        "amount": amount,
        "captured_amount": 0,
        "status": "open",
        "note": note,
        "created_at": now,
        "closed_at": None,
    }
    store.authorizations[aid] = auth_obj
    return jsonify(auth_obj), 201


@app.route("/authorizations/<aid>/captures", methods=["POST"])
def capture_authorization(aid: str):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)

    auth_obj = store.authorizations.get(aid)
    if not auth_obj:
        return error_json("not_found", "Authorization not found", 404)
    if auth_obj["status"] != "open":
        return error_json("conflict", f"Authorization is {auth_obj['status']}", 409)

    data = request.get_json(silent=True) or {}
    amount = data.get("amount")
    is_final = bool(data.get("final", True))

    remaining_held = auth_obj["amount"] - auth_obj["captured_amount"]
    if amount is None or not isinstance(amount, int) or amount <= 0 or amount > remaining_held:
        return error_json("validation_failed", "Invalid capture amount", 422)

    payer = store.users[store.users_by_handle[auth_obj["payer_handle"]]]
    recipient = store.users[store.users_by_handle[auth_obj["recipient_handle"]]]

    payer["total"] -= amount
    payer["held"] -= amount
    recipient["total"] += amount
    auth_obj["captured_amount"] += amount

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if is_final or auth_obj["captured_amount"] == auth_obj["amount"]:
        auth_obj["status"] = "captured"
        auth_obj["closed_at"] = now
        unreleased = auth_obj["amount"] - auth_obj["captured_amount"]
        if unreleased > 0:
            payer["held"] -= unreleased

    # Record capture as payment with revision
    pid = f"pay_{uuid.uuid4().hex[:12]}"
    pay = {
        "payment_id": pid,
        "from_handle": auth_obj["payer_handle"],
        "to_handle": auth_obj["recipient_handle"],
        "amount": amount,
        "note": f"Capture of {aid}",
        "visibility": "private",
        "created_at": now,
        "refund_of": None,
        "authorization_id": aid,
    }
    store.payments.insert(0, pay)
    store.revisions[pid] = [{
        "payment_id": pid, "revision": 1, "amount": amount,
        "effective_at": now, "recorded_at": now, "reason": "",
    }]
    return jsonify(auth_obj), 201


@app.route("/authorizations/<aid>/void", methods=["POST"])
def void_authorization(aid: str):
    user = get_current_user_from_req()
    if not user:
        return error_json("unauthorized", "Authentication required", 401)
    auth_obj = store.authorizations.get(aid)
    if not auth_obj:
        return error_json("not_found", "Authorization not found", 404)
    if auth_obj["status"] != "active":
        return error_json("conflict", f"Authorization is {auth_obj['status']}", 409)
    payer = store.users[store.users_by_handle[auth_obj["payer_handle"]]]
    unreleased = auth_obj["amount"] - auth_obj["captured_amount"]
    if unreleased > 0:
        payer["held"] -= unreleased
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    auth_obj["status"] = "voided"
    auth_obj["closed_at"] = now
    return jsonify(auth_obj), 200


# UI Pages (Browser HTML - carried from stage-2)

@app.route("/login", methods=["GET", "POST"])
def page_login():
    error_msg = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        uid = store.users_by_email.get(email)
        if uid and store.users[uid]["password"] == password:
            token = f"tok_{uuid.uuid4().hex}"
            store.tokens[token] = uid
            resp = make_response(redirect("/"))
            resp.set_cookie("pocketful_token", token)
            return resp
        else:
            error_msg = "Invalid email or password"
    return render_template_string(LOGIN_TEMPLATE, error_msg=error_msg)


@app.route("/signup", methods=["GET", "POST"])
def page_signup():
    error_msg = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        display_name = (request.form.get("display_name") or "").strip()
        if not email or not password or not display_name:
            error_msg = "All fields are required"
        elif email in store.users_by_email:
            error_msg = "Email already registered"
        else:
            raw_handle = re.sub(r"[^a-zA-Z0-9_]", "_", email.split("@")[0].lower())[:20]
            handle = raw_handle if raw_handle not in store.users_by_handle \
                else f"{raw_handle[:15]}_{uuid.uuid4().hex[:4]}"
            uid = f"u_{handle}"
            store._add_user(uid, email, password, display_name, handle, balance=0)
            token = f"tok_{uuid.uuid4().hex}"
            store.tokens[token] = uid
            resp = make_response(redirect("/"))
            resp.set_cookie("pocketful_token", token)
            return resp
    return render_template_string(SIGNUP_TEMPLATE, error_msg=error_msg)


@app.route("/logout", methods=["GET", "POST"])
def page_logout():
    token = request.cookies.get("pocketful_token")
    if token and token in store.tokens:
        del store.tokens[token]
    resp = make_response(redirect("/login"))
    resp.delete_cookie("pocketful_token")
    return resp


@app.route("/", methods=["GET", "POST"])
def page_home():
    user = get_current_user_from_req()
    if not user:
        return redirect("/login")

    pay_error = ""
    request_error = ""
    # Generate a fresh key for GET; on POST re-use submitted key so second submit is a replay
    form_key = uuid.uuid4().hex
    # Form field values echoed back to preserve them after submit
    form_handle = ""
    form_amount = ""
    form_note = ""
    form_visibility = "public"

    if request.method == "POST":
        action = request.form.get("action")
        if action == "pay":
            handle = (request.form.get("handle") or "").strip().lower()
            amt_str = request.form.get("amount") or ""
            note = request.form.get("note", "")
            visibility = request.form.get("visibility", "public")
            idem_key = request.form.get("idem_key") or uuid.uuid4().hex
            form_key = idem_key  # re-embed same key so second click = replay
            # Echo back submitted values so form is pre-filled
            form_handle = handle
            form_amount = amt_str
            form_note = note
            form_visibility = visibility

            minor = parse_decimal_to_minor(amt_str, store.minor_units)

            if minor is None:
                pay_error = "Invalid amount or too many decimal places"
            elif handle not in store.users_by_handle:
                pay_error = "Unknown handle"
            elif max(0, user["total"] - user["held"]) < minor:
                pay_error = "Insufficient funds"
            else:
                # Check idempotency: same user + key = replay (no double charge)
                ui_idem_tuple = (user["id"], idem_key, "/__ui_pay")
                if ui_idem_tuple not in store.idempotency:
                    rec_uid = store.users_by_handle[handle]
                    user["total"] -= minor
                    store.users[rec_uid]["total"] += minor
                    pid = f"pay_{uuid.uuid4().hex[:12]}"
                    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    payment = {
                        "payment_id": pid,
                        "from_handle": user["handle"],
                        "to_handle": handle,
                        "from_user_id": user["id"],
                        "to_user_id": rec_uid,
                        "amount": minor,
                        "currency": store.currency,
                        "note": note,
                        "visibility": visibility,
                        "created_at": now,
                        "refund_of": None,
                        "request_id": None,
                        "authorization_id": None,
                        "settlement_id": None,
                    }
                    store.payments.insert(0, payment)
                    store.revisions[pid] = [{
                        "payment_id": pid, "revision": 1, "amount": minor,
                        "effective_at": now, "recorded_at": now, "reason": "",
                    }]
                    store.idempotency[ui_idem_tuple] = (201, payment)
                # If replay: balance already updated, just re-render with current balance

    formatted_balance = format_money(user["total"], store.minor_units, store.currency)
    visible_payments = [p for p in store.payments
                        if p["visibility"] == "public"
                        or p["from_handle"] == user["handle"]
                        or p["to_handle"] == user["handle"]]

    return render_template_string(
        HOME_TEMPLATE,
        user=user,
        formatted_balance=formatted_balance,
        minor_units=store.minor_units,
        currency=store.currency,
        payments=visible_payments,
        pay_error=pay_error,
        request_error=request_error,
        format_money=format_money,
        form_key=form_key,
        form_handle=form_handle,
        form_amount=form_amount,
        form_note=form_note,
        form_visibility=form_visibility,
    )


def render_requests_ui(user: Optional[dict]):
    if not user:
        return redirect("/login")
    incoming = [r for r in store.requests.values() if r["payer_handle"] == user["handle"]]
    outgoing = [r for r in store.requests.values() if r["requester_handle"] == user["handle"]]
    return render_template_string(
        REQUESTS_TEMPLATE, user=user,
        incoming=incoming, outgoing=outgoing,
        minor_units=store.minor_units, currency=store.currency,
        format_money=format_money,
    )


@app.route("/split", methods=["GET", "POST"])
def page_split():
    user = get_current_user_from_req()
    if not user:
        return redirect("/login")
    return render_template_string(
        SPLIT_TEMPLATE, user=user,
        minor_units=store.minor_units, currency=store.currency,
        format_money=format_money,
    )


# Health

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "stage": 3}), 200


# HTML Templates (carried from stage-2)

LOGIN_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Pocketful Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4">
    <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-2xl p-8 shadow-2xl">
        <h1 class="text-2xl font-bold mb-6 text-center text-emerald-400">Sign in to Pocketful</h1>
        {% if error_msg %}
        <div data-testid="auth-error" class="mb-4 p-3 bg-rose-950 border border-rose-800 text-rose-300 rounded-xl text-sm font-semibold">
            {{ error_msg }}
        </div>
        {% endif %}
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-xs font-medium text-slate-300 mb-1">Email</label>
                <input type="email" name="email" data-testid="login-email" required
                    class="w-full px-4 py-2.5 bg-slate-900 border border-slate-700 rounded-xl text-sm focus:border-emerald-500 focus:outline-none">
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-300 mb-1">Password</label>
                <input type="password" name="password" data-testid="login-password" required
                    class="w-full px-4 py-2.5 bg-slate-900 border border-slate-700 rounded-xl text-sm focus:border-emerald-500 focus:outline-none">
            </div>
            <button type="submit" data-testid="login-submit"
                class="w-full py-3 bg-emerald-600 hover:bg-emerald-500 font-bold text-slate-950 rounded-xl shadow-lg transition-all">
                Sign In
            </button>
        </form>
        <p class="text-xs text-center text-slate-400 mt-6">
            Don't have an account- <a href="/signup" class="text-emerald-400 hover:underline">Sign up</a>
        </p>
    </div>
</body>
</html>"""

SIGNUP_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Pocketful Signup</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4">
    <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-2xl p-8 shadow-2xl">
        <h1 class="text-2xl font-bold mb-6 text-center text-emerald-400">Create an Account</h1>
        {% if error_msg %}
        <div data-testid="auth-error" class="mb-4 p-3 bg-rose-950 border border-rose-800 text-rose-300 rounded-xl text-sm font-semibold">
            {{ error_msg }}
        </div>
        {% endif %}
        <form method="POST" class="space-y-4">
            <div>
                <label class="block text-xs font-medium text-slate-300 mb-1">Display Name</label>
                <input type="text" name="display_name" data-testid="signup-display-name" required
                    class="w-full px-4 py-2.5 bg-slate-900 border border-slate-700 rounded-xl text-sm focus:border-emerald-500 focus:outline-none">
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-300 mb-1">Email</label>
                <input type="email" name="email" data-testid="signup-email" required
                    class="w-full px-4 py-2.5 bg-slate-900 border border-slate-700 rounded-xl text-sm focus:border-emerald-500 focus:outline-none">
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-300 mb-1">Password</label>
                <input type="password" name="password" data-testid="signup-password" required
                    class="w-full px-4 py-2.5 bg-slate-900 border border-slate-700 rounded-xl text-sm focus:border-emerald-500 focus:outline-none">
            </div>
            <button type="submit" data-testid="signup-submit"
                class="w-full py-3 bg-emerald-600 hover:bg-emerald-500 font-bold text-slate-950 rounded-xl shadow-lg transition-all">
                Create Account
            </button>
        </form>
    </div>
</body>
</html>"""

HOME_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Pocketful Wallet</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-6">
    <div class="max-w-4xl mx-auto space-y-6">
        <header class="flex justify-between items-center bg-slate-800 p-4 rounded-2xl border border-slate-700">
            <div>
                <span data-testid="current-user" class="font-bold text-lg text-emerald-400">{{ user.display_name }}</span>
                <span class="text-xs text-slate-400 ml-2">(@<span data-testid="current-handle">{{ user.handle }}</span>)</span>
            </div>
            <div class="flex items-center space-x-4">
                <a href="/requests" class="text-xs font-medium text-slate-300 hover:text-white">Requests</a>
                <a href="/split" class="text-xs font-medium text-slate-300 hover:text-white">Split</a>
                <form action="/logout" method="POST" class="inline">
                    <button type="submit" data-testid="logout-button" class="text-xs bg-slate-700 px-3 py-1.5 rounded-lg text-rose-300 hover:bg-slate-600">
                        Sign Out
                    </button>
                </form>
            </div>
        </header>

        <div class="bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl flex justify-between items-center">
            <div>
                <p class="text-xs text-slate-400 uppercase tracking-wider mb-1">Wallet Balance</p>
                <h1 data-testid="wallet-balance" data-amount="{{ user.total }}" class="text-4xl font-extrabold text-white">
                    {{ formatted_balance }}
                </h1>
            </div>
            <button id="wallet-refresh" data-testid="wallet-refresh" onclick="location.reload()"
                class="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-xs rounded-xl font-semibold text-emerald-400 border border-emerald-500/20">
                Refresh
            </button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div class="bg-slate-800 p-6 rounded-2xl border border-slate-700">
                <h2 class="text-lg font-bold mb-4 text-white">Send Payment</h2>
                {% if pay_error %}
                <div data-testid="pay-error" class="mb-4 p-3 bg-rose-950 border border-rose-800 text-rose-300 rounded-xl text-xs font-semibold">
                    {{ pay_error }}
                </div>
                {% endif %}
                <form id="pay-form" method="POST" class="space-y-3">
                    <input type="hidden" name="action" value="pay">
                    <input type="hidden" name="idem_key" id="idem_key" value="{{ form_key }}">
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Recipient Handle</label>
                        <input type="text" name="handle" data-testid="pay-handle" id="pay-handle" required
                            value="{{ form_handle }}"
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Amount</label>
                        <input type="text" name="amount" data-testid="pay-amount" id="pay-amount" required
                            value="{{ form_amount }}"
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Note</label>
                        <input type="text" name="note" data-testid="pay-note" id="pay-note"
                            value="{{ form_note }}"
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Visibility</label>
                        <select name="visibility" data-testid="pay-visibility" id="pay-visibility"
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
                            <option value="public" {% if form_visibility == 'public' %}selected{% endif %}>public</option>
                            <option value="private" {% if form_visibility == 'private' %}selected{% endif %}>private</option>
                        </select>
                    </div>
                    <button type="submit" data-testid="pay-submit" id="pay-submit"
                        class="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 font-bold text-slate-950 rounded-lg text-sm transition-all">
                        Pay
                    </button>
                </form>
            </div>

            <div class="bg-slate-800 p-6 rounded-2xl border border-slate-700 flex flex-col justify-between">
                <div>
                    <h2 class="text-lg font-bold mb-4 text-white">Activity Feed</h2>
                    {% if payments %}
                    <div data-testid="activity-list" class="space-y-3 max-h-72 overflow-y-auto pr-1">
                        {% for p in payments %}
                        <div data-testid="activity-item-{{ p.payment_id }}" data-visibility="{{ p.visibility }}"
                            class="p-3 bg-slate-900 rounded-xl border border-slate-700/60 flex justify-between items-center text-xs">
                            <div>
                                <p data-testid="activity-parties-{{ p.payment_id }}" class="font-semibold text-white">
                                    {{ p.from_handle }} - {{ p.to_handle }}
                                </p>
                                <p data-testid="activity-note-{{ p.payment_id }}" class="text-slate-400 text-[11px]">{{ p.note }}</p>
                            </div>
                            <span data-testid="activity-amount-{{ p.payment_id }}" class="font-mono font-bold text-emerald-400">
                                {{ format_money(p.amount, minor_units, currency) }}
                            </span>
                        </div>
                        {% endfor %}
                    </div>
                    {% else %}
                    <div data-testid="empty-activity" class="p-8 text-center text-slate-500 text-xs">
                        No activity yet
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>
    <script>
        // Regenerate idempotency key when any pay field changes (so editing = new payment)
        const idemInput = document.getElementById('idem_key');
        ['pay-handle', 'pay-amount', 'pay-note', 'pay-visibility'].forEach(function(tid) {
            const el = document.getElementById(tid);
            if (el) {
                el.addEventListener('input', function() { idemInput.value = Math.random().toString(36).slice(2) + Date.now().toString(36); });
                el.addEventListener('change', function() { idemInput.value = Math.random().toString(36).slice(2) + Date.now().toString(36); });
            }
        });
    </script>
</body>
</html>"""

REQUESTS_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Requests</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white min-h-screen p-6">
    <div class="max-w-3xl mx-auto space-y-6">
        <header class="flex justify-between items-center border-b border-slate-800 pb-4">
            <h1 class="text-2xl font-bold text-emerald-400">Payment Requests</h1>
            <a href="/" class="text-xs text-slate-400 hover:text-white">- Back to Wallet</a>
        </header>

        <div data-testid="request-error" id="request-error" class="hidden p-3 bg-rose-950 border border-rose-800 text-rose-300 rounded-xl text-sm font-semibold"></div>

        {% if not incoming and not outgoing %}
        <div data-testid="empty-requests" class="p-12 text-center text-slate-500 text-sm">
            No incoming or outgoing requests
        </div>
        {% endif %}
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div class="bg-slate-800 p-5 rounded-2xl border border-slate-700">
                <h2 class="text-sm font-semibold mb-3 text-slate-300">Incoming Requests</h2>
                <div data-testid="incoming-list" class="space-y-3">
                    {% for r in incoming %}
                    <div data-testid="request-item-{{ r.request_id }}" data-status="{{ r.status }}"
                        class="p-3 bg-slate-900 rounded-xl border border-slate-700 text-xs space-y-2">
                        <div class="flex justify-between font-semibold">
                            <span>From: {{ r.requester_handle }}</span>
                            <span data-testid="request-amount-{{ r.request_id }}" class="text-emerald-400">
                                {{ format_money(r.amount, minor_units, currency) }}
                            </span>
                        </div>
                        <p class="text-[11px] text-slate-400">{{ r.note }}</p>
                        {% if r.status == 'pending' %}
                        <div class="flex space-x-2 pt-1">
                            <button data-testid="request-pay-{{ r.request_id }}" onclick="payReq('{{ r.request_id }}')"
                                class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-bold rounded-lg text-xs">Pay</button>
                            <button data-testid="request-decline-{{ r.request_id }}" onclick="declineReq('{{ r.request_id }}')"
                                class="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-rose-300 font-semibold rounded-lg text-xs">Decline</button>
                        </div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            </div>
            <div class="bg-slate-800 p-5 rounded-2xl border border-slate-700">
                <h2 class="text-sm font-semibold mb-3 text-slate-300">Outgoing Requests</h2>
                <div data-testid="outgoing-list" class="space-y-3">
                    {% for r in outgoing %}
                    <div data-testid="request-item-{{ r.request_id }}" data-status="{{ r.status }}"
                        class="p-3 bg-slate-900 rounded-xl border border-slate-700 text-xs space-y-2">
                        <div class="flex justify-between font-semibold">
                            <span>To: {{ r.payer_handle }}</span>
                            <span data-testid="request-amount-{{ r.request_id }}" class="text-emerald-400">
                                {{ format_money(r.amount, minor_units, currency) }}
                            </span>
                        </div>
                        <p class="text-[11px] text-slate-400">{{ r.note }}</p>
                        {% if r.status == 'pending' %}
                        <button data-testid="request-cancel-{{ r.request_id }}" onclick="cancelReq('{{ r.request_id }}')"
                            class="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-amber-300 font-semibold rounded-lg text-xs">Cancel</button>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>
    <script>
        function showReqError(msg) {
            const el = document.getElementById('request-error');
            if (el) { el.textContent = msg || 'Payment failed'; el.classList.remove('hidden'); }
        }
        async function payReq(rid) {
            const res = await fetch(`/requests/${rid}/pay`, { method: 'POST', headers: { 'Idempotency-Key': Date.now().toString() } });
            if (res.ok) location.reload();
            else { const d = await res.json().catch(()=>{}); showReqError((d&&d.error&&d.error.message)||'Payment failed'); }
        }
        async function declineReq(rid) {
            const res = await fetch(`/requests/${rid}/decline`, { method: 'POST' });
            if (res.ok) location.reload();
        }
        async function cancelReq(rid) {
            const res = await fetch(`/requests/${rid}/cancel`, { method: 'POST' });
            if (res.ok) location.reload();
        }
    </script>
</body>
</html>"""

SPLIT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Split Bill</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white min-h-screen p-6">
    <div class="max-w-md mx-auto bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl space-y-4">
        <h1 class="text-xl font-bold text-emerald-400">Split a Bill</h1>
        <div data-testid="split-error" id="split-error" class="hidden p-3 bg-rose-950 border border-rose-800 text-rose-300 rounded-xl text-xs font-semibold"></div>
        <form class="space-y-4">
            <div>
                <label class="block text-xs text-slate-400 mb-1">Total Amount</label>
                <input type="text" id="split-amount" data-testid="split-amount" required
                    class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
            </div>
            <div>
                <label class="block text-xs text-slate-400 mb-1">Handles (comma-separated)</label>
                <input type="text" id="split-handles" data-testid="split-handles" placeholder="e.g. bob, cy" required
                    class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
            </div>
            <div>
                <label class="block text-xs text-slate-400 mb-1">Note</label>
                <input type="text" id="split-note" data-testid="split-note"
                    class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none">
            </div>
            <div data-testid="split-preview" id="split-preview" class="p-3 bg-slate-900/80 rounded-xl border border-slate-700/60 space-y-1 text-xs"></div>
            <button type="button" data-testid="split-submit" id="split-submit"
                class="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 font-bold text-slate-950 rounded-lg text-sm transition-all">
                Submit Split
            </button>
        </form>
        <a href="/" class="block text-center text-xs text-slate-400 hover:text-white pt-2">- Back to Wallet</a>
    </div>
    <script>
        const minorUnits = {{ minor_units }};
        const currency = "{{ currency }}";
        function formatMoney(minor) {
            if (minorUnits === 0) return minor + ' ' + currency;
            const s = String(minor).padStart(minorUnits + 1, '0');
            return s.slice(0, s.length - minorUnits) + '.' + s.slice(-minorUnits) + ' ' + currency;
        }
        function parseToMinor(s) {
            s = s.trim();
            if (!s) return null;
            const parts = s.split('.');
            if (parts.length > 2) return null;
            const whole = parseInt(parts[0] || '0', 10);
            if (isNaN(whole)) return null;
            let minor = whole * Math.pow(10, minorUnits);
            if (parts.length === 2) {
                const dec = parts[1].padEnd(minorUnits, '0').slice(0, minorUnits);
                if (dec.length > minorUnits) return null;
                minor += parseInt(dec, 10);
            }
            return minor;
        }
        function computeShares(total, n) {
            const base = Math.floor(total / n);
            const rem = total % n;
            return Array.from({length: n}, (_, i) => base + (i < rem ? 1 : 0));
        }
        function updatePreview() {
            const amtStr = document.getElementById('split-amount').value;
            const handlesRaw = document.getElementById('split-handles').value.split(',').map(h => h.trim().toLowerCase()).filter(h => h);
            const preview = document.getElementById('split-preview');
            preview.innerHTML = '';
            if (!amtStr || handlesRaw.length === 0) return;
            const minor = parseToMinor(amtStr);
            if (minor === null) return;
            const shares = computeShares(minor, handlesRaw.length);
            handlesRaw.forEach((h, i) => {
                const div = document.createElement('div');
                div.className = 'flex justify-between py-0.5';
                div.innerHTML = '<span>@' + h + '</span><span data-testid="split-share-' + h + '" class="font-mono text-emerald-400">' + formatMoney(shares[i]) + '</span>';
                preview.appendChild(div);
            });
        }
        document.getElementById('split-amount').addEventListener('input', updatePreview);
        document.getElementById('split-handles').addEventListener('input', updatePreview);
        document.getElementById('split-submit').addEventListener('click', async () => {
            const amtStr = document.getElementById('split-amount').value.trim();
            const handlesRaw = document.getElementById('split-handles').value.split(',').map(h => h.trim().toLowerCase()).filter(h => h);
            const note = document.getElementById('split-note').value.trim();
            const minor = parseToMinor(amtStr);
            const errEl = document.getElementById('split-error');
            if (minor === null || handlesRaw.length === 0) { errEl.textContent = 'Invalid input'; errEl.classList.remove('hidden'); return; }
            const body = { amount: minor, participant_handles: handlesRaw };
            if (note) body.note = note;
            const key = Date.now().toString() + Math.random();
            const res = await fetch('/splits', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(body) });
            if (res.ok) { window.location.href = '/requests'; }
            else { const d = await res.json().catch(()=>{}); errEl.textContent = (d&&d.error&&d.error.message)||'Split failed'; errEl.classList.remove('hidden'); }
        });
    </script>
</body>
</html>"""



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
