"""Local twin simulator - a drop-in stand-in for the Arga twins.

Why this exists
---------------
The Arga free plan allows 10 validation runs per month and one twin per run.
That budget was spent verifying the real twins' behaviour, so this module
re-serves the exact request/response shapes that were observed against the
live Stripe and HubSpot twins on 2026-09-13.

It is NOT a general-purpose mock: it implements only the surfaces this agent
actually calls, and it deliberately reproduces the real twins' quirks, e.g.
  * HubSpot returns only a minimal property set unless `?properties=` is given
  * the association PUT requires a body (the real gateway 411s without one)
  * Stripe invoices move draft -> open -> paid / uncollectible

All four services share one port because their path prefixes never collide:
    /v1/...    Stripe          /crm/...   HubSpot
    /api/...   Slack           /gmail/... Gmail
So every client's base URL is simply http://127.0.0.1:<port>.
"""
from __future__ import annotations

import json
import pathlib
import random
import re
import string
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LOCK = threading.RLock()


class _SimServer(ThreadingHTTPServer):
    """Simulator HTTP server.

    allow_reuse_address MUST stay False. HTTPServer defaults it to True, and on
    Windows SO_REUSEADDR lets a second process bind a port that is already held
    - so two processes both believe they own the simulator and requests land on
    whichever socket wins the race. That silently corrupts any eval that
    reseeds, because reset() clears one store while the other serves stale data.
    With this False, the second process gets OSError and correctly falls back to
    using the first one over HTTP.
    """

    allow_reuse_address = False
    daemon_threads = True


def _uid(prefix: str, n: int = 24) -> str:
    body = "".join(random.choices(string.ascii_letters + string.digits, k=n))
    return f"{prefix}{body}"


def _num_id() -> str:
    return str(random.randint(10**9, 10**10 - 1))


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        with _LOCK:
            # Stripe
            self.customers: dict[str, dict] = {}
            self.invoices: dict[str, dict] = {}
            self.invoice_items: dict[str, dict] = {}
            self.pending_items: dict[str, list[str]] = {}   # customer -> item ids
            self.products: dict[str, dict] = {}
            self.prices: dict[str, dict] = {}
            self.subscriptions: dict[str, dict] = {}
            self.payment_intents: dict[str, dict] = {}
            self.charges: dict[str, dict] = {}
            self.refunds: dict[str, dict] = {}
            # HubSpot
            self.companies: dict[str, dict] = {}
            self.deals: dict[str, dict] = {}
            self.contacts: dict[str, dict] = {}
            self.notes: dict[str, dict] = {}
            self.tasks: dict[str, dict] = {}
            self.assoc: dict[str, set[str]] = {}            # deal id -> company ids
            # Slack
            self.messages: list[dict] = []
            self.reactions: dict[str, list[str]] = {}       # ts -> emoji names
            # Gmail
            self.drafts: dict[str, dict] = {}


    # -- persistence -------------------------------------------------
    _FIELDS = ("customers", "invoices", "invoice_items", "pending_items", "products",
               "prices", "subscriptions", "payment_intents", "charges", "refunds",
               "companies", "deals", "contacts", "notes", "tasks", "messages",
               "reactions", "drafts")

    def to_dict(self) -> dict:
        with _LOCK:
            data = {name: getattr(self, name) for name in self._FIELDS}
            data["assoc"] = {k: sorted(v) for k, v in self.assoc.items()}
            return data

    def load_dict(self, data: dict) -> None:
        with _LOCK:
            for name in self._FIELDS:
                if name in data:
                    setattr(self, name, data[name])
            self.assoc = {k: set(v) for k, v in (data.get("assoc") or {}).items()}


STORE = Store()

STATE_FILE = pathlib.Path(__file__).resolve().parent.parent / "runs" / "sim_state.json"


def save_state() -> None:
    """Persist state so `main.py seed` and `main.py run` can be separate processes."""
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        STATE_FILE.write_text(json.dumps(STORE.to_dict(), default=str), encoding="utf-8")
    except OSError:
        pass


def load_state() -> bool:
    if not STATE_FILE.exists():
        return False
    try:
        STORE.load_dict(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        return True
    except (OSError, json.JSONDecodeError):
        return False


def clear_state() -> None:
    reset()          # clears the SERVING store, wherever it lives
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Stripe form-encoding -> nested dict  (items[0][price]=x  ->  {items:[{price:x}]})
# --------------------------------------------------------------------------
_KEY_RE = re.compile(r"\[([^\]]*)\]")


def parse_form(raw: str) -> dict:
    out: dict = {}
    for key, value in urllib.parse.parse_qsl(raw, keep_blank_values=True):
        head = key.split("[", 1)[0]
        parts = [head] + _KEY_RE.findall(key)
        node = out
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            nxt = parts[i + 1] if not last else None
            if last:
                node[part] = value
            else:
                default = [] if (nxt or "").isdigit() else {}
                if isinstance(node, list):
                    idx = int(part)
                    while len(node) <= idx:
                        node.append({})
                    node = node[idx]
                    continue
                node = node.setdefault(part, default)
        # normalise list-shaped dicts written via numeric keys
    return _fix_numeric(out)


def _fix_numeric(node):
    if isinstance(node, dict):
        keys = list(node.keys())
        if keys and all(k.isdigit() for k in keys):
            return [_fix_numeric(node[k]) for k in sorted(keys, key=int)]
        return {k: _fix_numeric(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_fix_numeric(v) for v in node]
    return node


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _slist(data: list, url: str) -> dict:
    return {"object": "list", "url": url, "has_more": False, "data": data}


# --------------------------------------------------------------------------
# Stripe handlers
# --------------------------------------------------------------------------
def stripe_route(method: str, path: str, body: dict, query: dict):
    s = STORE

    # ---- customers ----
    if path == "/v1/customers" and method == "POST":
        cid = _uid("cus_")
        obj = {"id": cid, "object": "customer", "name": body.get("name"),
               "email": body.get("email"), "metadata": body.get("metadata") or {},
               "created": int(time.time())}
        s.customers[cid] = obj
        return 200, obj
    if path == "/v1/customers" and method == "GET":
        return 200, _slist(list(s.customers.values()), "/v1/customers")
    m = re.fullmatch(r"/v1/customers/(\w+)", path)
    if m and method == "GET":
        return _one(s.customers, m.group(1))

    # ---- invoice items ----
    if path in ("/v1/invoice_items", "/v1/invoiceitems") and method == "POST":
        iid = _uid("ii_")
        obj = {"id": iid, "object": "invoiceitem", "customer": body.get("customer"),
               "amount": _int(body.get("amount")), "currency": body.get("currency", "usd"),
               "description": body.get("description")}
        s.invoice_items[iid] = obj
        s.pending_items.setdefault(obj["customer"], []).append(iid)
        return 200, obj

    # ---- invoices ----
    if path == "/v1/invoices" and method == "POST":
        cust = body.get("customer")
        items = [s.invoice_items[i] for i in s.pending_items.pop(cust, [])]
        total = sum(i["amount"] for i in items)
        inv_id = _uid("in_")
        obj = {"id": inv_id, "object": "invoice", "customer": cust, "status": "draft",
               "amount_due": total, "amount_paid": 0, "amount_remaining": total,
               "total": total, "subtotal": total,
               "currency": body.get("currency", "usd"), "created": int(time.time()),
               "attempt_count": 0, "paid": False, "charge": None,
               "collection_method": body.get("collection_method", "charge_automatically"),
               "lines": _slist(items, f"/v1/invoices/{inv_id}/lines")}
        s.invoices[inv_id] = obj
        return 200, obj
    if path == "/v1/invoices" and method == "GET":
        return 200, _slist(list(s.invoices.values()), "/v1/invoices")
    m = re.fullmatch(r"/v1/invoices/(\w+)", path)
    if m and method == "GET":
        return _one(s.invoices, m.group(1))
    m = re.fullmatch(r"/v1/invoices/(\w+)/finalize", path)
    if m and method == "POST":
        inv = s.invoices.get(m.group(1))
        if not inv:
            return _missing("invoice", m.group(1))
        inv["status"] = "open"
        return 200, inv
    m = re.fullmatch(r"/v1/invoices/(\w+)/pay", path)
    if m and method == "POST":
        inv = s.invoices.get(m.group(1))
        if not inv:
            return _missing("invoice", m.group(1))
        if "declined" in str(body.get("payment_method", "")).lower():
            # Real Stripe: the charge fails, attempt_count increments, invoice stays open.
            inv["attempt_count"] = int(inv.get("attempt_count", 0)) + 1
            inv["status"] = "open"
            return 402, {"error": {"type": "card_error", "code": "card_declined",
                                   "message": "Your card was declined.",
                                   "decline_code": "generic_decline"}}
        inv.update(status="paid", paid=True, amount_paid=inv["amount_due"], amount_remaining=0)
        return 200, inv
    m = re.fullmatch(r"/v1/invoices/(\w+)/mark_uncollectible", path)
    if m and method == "POST":
        inv = s.invoices.get(m.group(1))
        if not inv:
            return _missing("invoice", m.group(1))
        inv["status"] = "uncollectible"
        return 200, inv

    # ---- products / prices ----
    if path == "/v1/products" and method == "POST":
        pid = _uid("prod_")
        obj = {"id": pid, "object": "product", "name": body.get("name"), "active": True}
        s.products[pid] = obj
        return 200, obj
    if path == "/v1/prices" and method == "POST":
        pid = _uid("price_")
        rec = body.get("recurring") or {}
        obj = {"id": pid, "object": "price", "product": body.get("product"),
               "unit_amount": _int(body.get("unit_amount")),
               "currency": body.get("currency", "usd"),
               "recurring": {"interval": rec.get("interval", "month"),
                             "interval_count": _int(rec.get("interval_count"), 1)}}
        s.prices[pid] = obj
        return 200, obj

    # ---- subscriptions ----
    if path == "/v1/subscriptions" and method == "POST":
        sid = _uid("sub_")
        raw_items = body.get("items") or []
        if isinstance(raw_items, dict):
            raw_items = list(raw_items.values())
        data = []
        for it in raw_items:
            price = s.prices.get(it.get("price"), {})
            data.append({"id": _uid("si_"), "object": "subscription_item",
                         "price": price, "quantity": _int(it.get("quantity"), 1)})
        obj = {"id": sid, "object": "subscription", "customer": body.get("customer"),
               "status": "active", "currency": "usd",
               "items": _slist(data, f"/v1/subscriptions/{sid}/items")}
        s.subscriptions[sid] = obj
        return 200, obj
    if path == "/v1/subscriptions" and method == "GET":
        want = query.get("status", ["all"])[0]
        rows = list(s.subscriptions.values())
        if want not in ("all", None):
            rows = [r for r in rows if r["status"] == want]
        return 200, _slist(rows, "/v1/subscriptions")
    m = re.fullmatch(r"/v1/subscriptions/(\w+)", path)
    if m and method == "GET":
        return _one(s.subscriptions, m.group(1))
    if m and method == "DELETE":
        sub = s.subscriptions.get(m.group(1))
        if not sub:
            return _missing("subscription", m.group(1))
        sub["status"] = "canceled"
        return 200, sub

    # ---- payment intents / refunds ----
    if path == "/v1/payment_intents" and method == "POST":
        pid = _uid("pi_")
        ch_id = _uid("ch_")
        s.charges[ch_id] = {"id": ch_id, "object": "charge", "amount": _int(body.get("amount")),
                            "customer": body.get("customer"), "refunded": False, "paid": True}
        obj = {"id": pid, "object": "payment_intent", "amount": _int(body.get("amount")),
               "currency": body.get("currency", "usd"), "customer": body.get("customer"),
               "status": "succeeded", "latest_charge": ch_id}
        s.payment_intents[pid] = obj
        return 200, obj
    if path == "/v1/payment_intents" and method == "GET":
        return 200, _slist(list(s.payment_intents.values()), "/v1/payment_intents")
    if path == "/v1/refunds" and method == "POST":
        pi = s.payment_intents.get(body.get("payment_intent"), {})
        rid = _uid("re_")
        amount = _int(body.get("amount"), pi.get("amount", 0))
        obj = {"id": rid, "object": "refund", "payment_intent": pi.get("id"),
               "charge": pi.get("latest_charge"), "amount": amount, "status": "succeeded"}
        s.refunds[rid] = obj
        if pi.get("latest_charge") in s.charges:
            s.charges[pi["latest_charge"]]["refunded"] = True
        return 200, obj
    if path == "/v1/refunds" and method == "GET":
        return 200, _slist(list(s.refunds.values()), "/v1/refunds")

    return 404, {"error": {"type": "invalid_request_error",
                           "message": f"Unrecognized request URL ({method} {path})"}}


def _one(table: dict, key: str):
    obj = table.get(key)
    return (200, obj) if obj else _missing("resource", key)


def _missing(kind: str, key: str):
    return 404, {"error": {"type": "invalid_request_error",
                           "message": f"No such {kind}: {key}"}}


# --------------------------------------------------------------------------
# HubSpot handlers
# --------------------------------------------------------------------------
_HS_TABLES = {"companies": "companies", "deals": "deals",
              "contacts": "contacts", "notes": "notes", "tasks": "tasks"}

_DEAL_STAGES = [
    ("appointmentscheduled", "Appointment scheduled", "false"),
    ("qualifiedtobuy", "Qualified to buy", "false"),
    ("presentationscheduled", "Presentation scheduled", "false"),
    ("decisionmakerboughtin", "Decision maker bought-in", "false"),
    ("closedwon", "Closed won", "true"),
    ("closedlost", "Closed lost", "true"),
]


def _hs_shape(obj: dict, want: list[str] | None) -> dict:
    """Mirror the real twin: only requested properties come back."""
    props = obj["properties"]
    keep = {"createdate": obj["createdAt"], "hs_object_id": obj["id"],
            "lastmodifieddate": obj["updatedAt"]}
    if want:
        for k in want:
            if k in props:
                keep[k] = props[k]
    return {"id": obj["id"], "properties": keep,
            "createdAt": obj["createdAt"], "updatedAt": obj["updatedAt"], "archived": False}


def hubspot_route(method: str, path: str, body: dict, query: dict):
    s = STORE
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    if path == "/crm/v3/pipelines/deals" and method == "GET":
        return 200, {"results": [{
            "id": "default", "label": "Sales Pipeline", "displayOrder": 0, "archived": False,
            "stages": [{"id": i, "label": l, "displayOrder": n, "archived": False,
                        "metadata": {"isClosed": c, "probability": "0.5"}}
                       for n, (i, l, c) in enumerate(_DEAL_STAGES)]}]}

    m = re.fullmatch(r"/crm/v3/objects/(\w+)", path)
    if m and m.group(1) in _HS_TABLES:
        table = getattr(s, _HS_TABLES[m.group(1)])
        if method == "POST":
            oid = _num_id()
            obj = {"id": oid, "properties": dict(body.get("properties") or {}),
                   "createdAt": now, "updatedAt": now}
            table[oid] = obj
            return 200, _hs_shape(obj, list(obj["properties"].keys()))
        if method == "GET":
            want = (query.get("properties", [""])[0] or "").split(",")
            want = [w for w in want if w]
            assoc = (query.get("associations", [""])[0] or "")
            rows = []
            for obj in table.values():
                shaped = _hs_shape(obj, want)
                if "companies" in assoc and m.group(1) == "deals":
                    ids = sorted(s.assoc.get(obj["id"], []))
                    shaped["associations"] = {"companies": {
                        "results": [{"id": i, "type": "deal_to_company"} for i in ids],
                        "paging": {}}}
                rows.append(shaped)
            return 200, {"results": rows, "paging": {}}

    m = re.fullmatch(r"/crm/v3/objects/(\w+)/(\w+)", path)
    if m and m.group(1) in _HS_TABLES:
        table = getattr(s, _HS_TABLES[m.group(1)])
        obj = table.get(m.group(2))
        if not obj:
            return 404, {"status": "error", "message": "resource not found"}
        if method == "GET":
            want = (query.get("properties", [""])[0] or "").split(",")
            return 200, _hs_shape(obj, [w for w in want if w])
        if method == "PATCH":
            obj["properties"].update(body.get("properties") or {})
            obj["updatedAt"] = now
            return 200, _hs_shape(obj, list(obj["properties"].keys()))

    m = re.fullmatch(r"/crm/v4/objects/deals/(\w+)/associations/default/companies/(\w+)", path)
    if m and method == "PUT":
        s.assoc.setdefault(m.group(1), set()).add(m.group(2))
        return 200, {"fromObjectTypeId": "0-3", "fromObjectId": int(m.group(1)),
                     "toObjectTypeId": "0-2", "toObjectId": int(m.group(2)),
                     "labels": [{"category": "HUBSPOT_DEFINED", "typeId": 5, "label": None}]}

    return 404, {"status": "error", "message": f"no route {method} {path}"}


# --------------------------------------------------------------------------
# Slack + Gmail handlers
# --------------------------------------------------------------------------
def slack_route(method: str, path: str, body: dict, query: dict):
    s = STORE
    name = path.rsplit("/", 1)[-1]

    if name == "chat.postMessage":
        ts = f"{time.time():.6f}"
        msg = {"ts": ts, "channel": body.get("channel", ""), "text": body.get("text", ""),
               "blocks": body.get("blocks")}
        s.messages.append(msg)
        s.reactions.setdefault(ts, [])
        return 200, {"ok": True, "ts": ts, "channel": msg["channel"], "message": msg}

    if name == "reactions.get":
        ts = body.get("timestamp") or (query.get("timestamp", [""])[0])
        names = s.reactions.get(ts, [])
        return 200, {"ok": True, "type": "message", "message": {
            "ts": ts, "reactions": [{"name": n, "count": 1, "users": ["U_SIM"]} for n in names]}}

    if name == "reactions.add":
        ts = body.get("timestamp") or (query.get("timestamp", [""])[0])
        s.reactions.setdefault(ts, []).append(body.get("name", ""))
        return 200, {"ok": True}

    if name == "auth.test":
        return 200, {"ok": True, "user": "reconcile-agent", "team": "twin-sim"}

    if name == "conversations.list":
        return 200, {"ok": True, "channels": [{"id": "C_SIM", "name": "approvals"}]}

    return 200, {"ok": False, "error": f"unknown_method:{name}"}


def gmail_route(method: str, path: str, body: dict, query: dict):
    s = STORE
    if path == "/gmail/v1/users/me/drafts" and method == "POST":
        did = _uid("r", 12)
        msg_id = _uid("m", 12)
        s.drafts[did] = {"id": did, "message": {"id": msg_id,
                                                "raw": (body.get("message") or {}).get("raw", "")}}
        return 200, s.drafts[did]
    if path == "/gmail/v1/users/me/drafts" and method == "GET":
        return 200, {"drafts": [{"id": d["id"], "message": {"id": d["message"]["id"]}}
                                for d in s.drafts.values()]}
    return 404, {"error": {"code": 404, "message": "not found"}}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):      # keep the demo console clean
        pass

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        ctype = self.headers.get("Content-Type", "")

        if raw and "json" in ctype:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {}
        elif raw:
            body = parse_form(raw)
        else:
            body = dict(parse_form(parsed.query)) if parsed.query else {}

        with _LOCK:
            try:
                if path.startswith("/v1/"):
                    status, payload = stripe_route(method, path, body, query)
                elif path.startswith("/crm/"):
                    status, payload = hubspot_route(method, path, body, query)
                elif path.startswith("/api/"):
                    status, payload = slack_route(method, path, body, query)
                elif path.startswith("/gmail/"):
                    status, payload = gmail_route(method, path, body, query)
                elif path == "/sim/reset":
                    # Clear the persisted copy too: start() reloads state on
                    # every session, so an in-memory-only reset is undone by the
                    # very next request.
                    STORE.reset()
                    save_state()
                    status, payload = 200, {"ok": True, "reset": True}
                elif path == "/sim/health":
                    status, payload = 200, {"ok": True}
                else:
                    status, payload = 404, {"error": {"message": f"no route {path}"}}
            except Exception as exc:                      # never hang a client
                status, payload = 500, {"error": {"type": "sim_error",
                                                  "message": f"{type(exc).__name__}: {exc}"}}

        if method in ("POST", "PATCH", "PUT", "DELETE") and status < 400:
            save_state()
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


_SERVER: _SimServer | None = None
_PORT: int = 8787
_OWNED: bool = False          # True when this process bound the socket


def _already_serving(port: int) -> bool:
    """True if a simulator from another process is already on this port."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sim/health", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start(port: int = 8787) -> str:
    """Start the simulator in a daemon thread; returns its base URL.

    If another process already holds the port with a live simulator, reuse it
    rather than crashing - both processes then share one consistent state.
    """
    global _SERVER, _PORT, _OWNED
    _PORT = port
    load_state()
    if _SERVER is None:
        try:
            _SERVER = _SimServer(("127.0.0.1", port), Handler)
        except OSError:
            if _already_serving(port):
                # Another process owns the store; we are a client of it.
                _OWNED = False
                return f"http://127.0.0.1:{port}"
            raise
        _OWNED = True
        threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
        time.sleep(0.2)
    return f"http://127.0.0.1:{port}"


def _remote_reset() -> bool:
    """Reset a simulator owned by another process, over its own HTTP API."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{_PORT}/sim/reset", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def reset() -> None:
    """Clear simulator state.

    If another process owns the running simulator (the API server, say), a local
    STORE.reset() would clear the wrong object and leave the served data intact
    - which silently corrupts any eval that reseeds on top of it. Reset the
    serving process over HTTP instead.
    """
    if _SERVER is not None and _OWNED:
        STORE.reset()
        save_state()
    elif not _remote_reset():
        STORE.reset()
        save_state()


if __name__ == "__main__":
    url = start()
    print(f"twin simulator listening on {url}")
    threading.Event().wait()
