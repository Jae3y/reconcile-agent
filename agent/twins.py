"""Arga Twins provisioning.

Free-plan constraints discovered empirically (2026-09-13):
  * one twin per Twin Run  -> each service gets its own run
  * fixed 10-minute TTL    -> sessions are short; re-provision rather than extend
  * runs may be concurrent -> all four services provision in parallel

Base URL shape returned by the API:
    https://pub-r<run_id_without_dashes>--<twin>.sandbox.argalabs.com
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request

from .config import SETTINGS, TwinSession

PROVISION_PATH = "/validate/twins/provision"
DEFAULT_TWINS = ("stripe", "hubspot", "slack", "gmail")


class ArgaError(RuntimeError):
    pass


class ArgaQuotaLock(RuntimeError):
    """Raised instead of spending an Arga validation run."""


def _assert_provisioning_allowed(twins) -> None:
    """Hard safety interlock.

    Arga free-plan validation runs are a scarce monthly resource and this repo
    exhausted them once already. Provisioning is therefore OFF unless the
    operator explicitly sets ARGA_ALLOW_PROVISION=true in .env. Everything in
    the pipeline runs against the local simulator by default, which costs
    nothing, so this lock never blocks ordinary work.
    """
    import os

    if (os.getenv("ARGA_ALLOW_PROVISION", "false").strip().lower()
            not in {"1", "true", "yes", "on"}):
        raise ArgaQuotaLock(
            f"Refusing to provision {list(twins)}: ARGA_ALLOW_PROVISION is not enabled. "
            "Each provision consumes one of a limited monthly quota. "
            "Run against the simulator instead (default), or set "
            "ARGA_ALLOW_PROVISION=true in .env to deliberately spend a run."
        )


def _call(method: str, path: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SETTINGS.arga_api_base + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {SETTINGS.arga_api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ArgaError(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode('utf-8')[:300]}") from exc


def provision_one(twin: str, ttl_minutes: int = 10, on_event=None) -> dict:
    """Provision a single twin and block until it reports ready."""
    _assert_provisioning_allowed([twin])
    SETTINGS.require("arga_api_key")
    started = _call("POST", PROVISION_PATH, {"twins": [twin], "ttl_minutes": ttl_minutes, "public": True})
    run_id = started.get("run_id")
    if not run_id:
        raise ArgaError(f"{twin}: provision returned no run_id: {started}")

    deadline = time.time() + 240
    while time.time() < deadline:
        status = _call("GET", f"{PROVISION_PATH}/{run_id}/status")
        state = status.get("status")
        if state == "ready":
            entry = status["twins"][twin]
            record = {
                "run_id": run_id,
                "base_url": entry["base_url"],
                "admin_url": entry.get("admin_url"),
                "env_vars": entry.get("env_vars") or {},
                "expires_at": status.get("expires_at"),
                "dashboard_url": status.get("dashboard_url"),
            }
            if on_event:
                on_event(twin, "ready", record)
            return record
        if state in {"failed", "expired", "cancelled"}:
            raise ArgaError(f"{twin}: provisioning ended as '{state}' ({status.get('error')})")
        if on_event:
            on_event(twin, state or "provisioning", None)
        time.sleep(4)
    raise ArgaError(f"{twin}: timed out waiting for ready")


def provision(twins=DEFAULT_TWINS, ttl_minutes: int = 10, on_event=None) -> TwinSession:
    """Provision every requested twin in parallel (separate runs, free plan)."""
    _assert_provisioning_allowed(twins)
    session = TwinSession()
    with cf.ThreadPoolExecutor(max_workers=len(twins)) as pool:
        futures = {pool.submit(provision_one, t, ttl_minutes, on_event): t for t in twins}
        errors: list[str] = []
        for fut in cf.as_completed(futures):
            twin = futures[fut]
            try:
                session.twins[twin] = fut.result()
            except Exception as exc:  # surface, never swallow
                errors.append(f"{twin}: {exc}")
    if errors and not session.twins:
        raise ArgaError("all twins failed to provision -> " + " | ".join(errors))
    if errors:
        session.twins["_errors"] = {"base_url": "", "messages": errors}
    session.save()
    return session


def load_or_provision(twins=DEFAULT_TWINS, min_seconds: float = 60.0, on_event=None) -> TwinSession:
    """Reuse the saved session while it has life left, else provision fresh."""
    session = TwinSession.load()
    if session.is_live(min_seconds):
        return session
    return provision(twins, on_event=on_event)


def simulated_session(port: int = 8787) -> TwinSession:
    """A TwinSession backed by the local simulator.

    The clients take a base URL per request, so nothing downstream can tell the
    difference between this and a real Arga twin.
    """
    from sim.twin_sim import start

    url = start(port)
    expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    return TwinSession(twins={
        name: {"base_url": url, "admin_url": url, "env_vars": {},
               "expires_at": expires, "run_id": "sim", "dashboard_url": url}
        for name in DEFAULT_TWINS
    })
