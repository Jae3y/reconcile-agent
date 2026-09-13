"""Thin, explicit adapters over the Arga twins.

Design note (learned the hard way, 2026-09-13): `hubspot-api-client` rebuilds
its sub-clients lazily, so a `configuration.host` override set on
`crm.companies.basic_api` is GONE the next time you touch `crm.companies`, and
the call silently goes to the real `api.hubapi.com`. CLAUDE.md forbids ever
pointing at a production base URL, so HubSpot uses an explicit adapter that
passes the twin base URL on every single request. Stripe and Slack both accept
a real per-client override, so they use their official SDKs.
"""
from __future__ import annotations

from typing import Any

import requests
import stripe as stripe_sdk
from slack_sdk import WebClient

from .config import REAL_BASE, REPO_ROOT, SETTINGS, TwinSession, is_real

TIMEOUT = 30


# --------------------------------------------------------------------------
# Stripe — official SDK, per-client base address (no global mutation)
# --------------------------------------------------------------------------
def stripe_client(session: TwinSession) -> stripe_sdk.StripeClient:
    """Real Stripe when STRIPE_MODE=real, else the twin/simulator."""
    if is_real("stripe"):
        # No base_addresses override -> the SDK's own api.stripe.com.
        return stripe_sdk.StripeClient(SETTINGS.stripe_api_key)
    return stripe_sdk.StripeClient(
        SETTINGS.stripe_api_key,
        base_addresses={"api": session.base_url("stripe")},
    )


# --------------------------------------------------------------------------
# HubSpot — explicit adapter; base URL supplied on every request
# --------------------------------------------------------------------------
class HubSpotTwin:
    def __init__(self, session: TwinSession):
        if is_real("hubspot"):
            self.base = REAL_BASE["hubspot"]
            token = SETTINGS.hubspot_token
        else:
            self.base = session.base_url("hubspot")
            token = session.token("hubspot", SETTINGS.hubspot_token)
        self.real = is_real("hubspot")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, **kw) -> Any:
        resp = requests.request(
            method, f"{self.base}{path}", headers=self.headers, timeout=TIMEOUT, **kw
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # --- reads ---
    def list_companies(self, properties: list[str] | None = None) -> list[dict]:
        props = ",".join(properties or ["name", "domain", "lifecyclestage", "description"])
        return self._paged("/crm/v3/objects/companies", props)

    def list_deals(self, properties: list[str] | None = None) -> list[dict]:
        props = ",".join(properties or ["dealname", "amount", "dealstage", "pipeline"])
        return self._paged("/crm/v3/objects/deals", props, associations="companies")

    def _paged(self, path: str, props: str, associations: str | None = None) -> list[dict]:
        out: list[dict] = []
        after: str | None = None
        while True:
            q = f"?limit=100&properties={props}"
            if associations:
                q += f"&associations={associations}"
            if after:
                q += f"&after={after}"
            page = self._req("GET", path + q)
            out.extend(page.get("results", []))
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return out

    def get_deal(self, deal_id: str, properties: list[str] | None = None) -> dict:
        # NOTE: HubSpot returns only a minimal property set unless asked explicitly.
        props = ",".join(properties or ["dealname", "amount", "dealstage", "pipeline"])
        return self._req("GET", f"/crm/v3/objects/deals/{deal_id}?properties={props}")

    def get_company(self, company_id: str, properties: list[str] | None = None) -> dict:
        props = ",".join(properties or ["name", "domain", "lifecyclestage", "description"])
        return self._req("GET", f"/crm/v3/objects/companies/{company_id}?properties={props}")

    # --- writes ---
    def create_company(self, properties: dict) -> dict:
        return self._req("POST", "/crm/v3/objects/companies", json={"properties": properties})

    def create_contact(self, properties: dict) -> dict:
        return self._req("POST", "/crm/v3/objects/contacts", json={"properties": properties})

    def list_contacts(self) -> list[dict]:
        return self._paged("/crm/v3/objects/contacts", "email,firstname,lastname,company")

    def create_deal(self, properties: dict) -> dict:
        return self._req("POST", "/crm/v3/objects/deals", json={"properties": properties})

    def update_deal(self, deal_id: str, properties: dict) -> dict:
        return self._req("PATCH", f"/crm/v3/objects/deals/{deal_id}", json={"properties": properties})

    def update_company(self, company_id: str, properties: dict) -> dict:
        return self._req("PATCH", f"/crm/v3/objects/companies/{company_id}", json={"properties": properties})

    def associate_deal_company(self, deal_id: str, company_id: str) -> dict:
        # An empty JSON body is required; without it the gateway returns 411.
        return self._req(
            "PUT",
            f"/crm/v4/objects/deals/{deal_id}/associations/default/companies/{company_id}",
            json={},
        )

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def create_note(self, body: str, company_id: str | None = None) -> dict:
        # hs_timestamp is REQUIRED by the real HubSpot engagements API (the twin
        # tolerated its absence); sending it always keeps both paths identical.
        return self._req("POST", "/crm/v3/objects/notes",
                         json={"properties": {"hs_note_body": body,
                                              "hs_timestamp": self._now_iso()}})

    def create_task(self, subject: str, body: str = "") -> dict:
        return self._req(
            "POST",
            "/crm/v3/objects/tasks",
            json={"properties": {"hs_task_subject": subject, "hs_task_body": body,
                                 "hs_task_status": "NOT_STARTED",
                                 "hs_timestamp": self._now_iso()}},
        )


# --------------------------------------------------------------------------
# Slack — official SDK, per-client base_url
# --------------------------------------------------------------------------
def slack_client(session: TwinSession, use_twin: bool = True) -> WebClient:
    """Real Slack when SLACK_MODE=real, else the twin/simulator."""
    if is_real("slack"):
        return WebClient(token=SETTINGS.slack_token)
    if use_twin and "slack" in session.twins:
        return WebClient(token=session.token("slack", SETTINGS.slack_token) or "xoxb-twin",
                         base_url=session.base_url("slack") + "/api/")
    return WebClient(token=SETTINGS.slack_token)


# --------------------------------------------------------------------------
# Gmail — DRAFT ONLY. Never send. (CLAUDE.md "Never" tier)
# --------------------------------------------------------------------------
class GmailTwin:
    """Creates drafts against the Gmail twin. There is deliberately no send()."""

    def __init__(self, session: TwinSession):
        self.base = session.base_url("gmail")
        self.headers = {"Authorization": "Bearer gmail-twin-token", "Content-Type": "application/json"}

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        import base64

        raw = base64.urlsafe_b64encode(
            f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}".encode("utf-8")
        ).decode("ascii")
        resp = requests.post(
            f"{self.base}/gmail/v1/users/me/drafts",
            headers=self.headers,
            json={"message": {"raw": raw}},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def list_drafts(self) -> list[dict]:
        resp = requests.get(
            f"{self.base}/gmail/v1/users/me/drafts", headers=self.headers, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json().get("drafts", []) or []


class GmailReal:
    """Drafts into the real, OAuth-authenticated Gmail account.

    Enabled with GMAIL_MODE=real. Uses the gmail.compose scope, which is the
    narrowest scope that can create a draft. There is deliberately NO send()
    method on this class - sending is a Never-tier action (CLAUDE.md), so the
    capability simply does not exist in the codebase.

    The first run opens a browser consent screen and caches a token at
    GMAIL_TOKEN_PATH; subsequent runs are non-interactive.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

    def __init__(self, session: TwinSession | None = None):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        token_path = REPO_ROOT / SETTINGS.gmail_token_path
        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(REPO_ROOT / SETTINGS.gmail_credentials_path), self.SCOPES)
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        import base64

        raw = base64.urlsafe_b64encode(
            f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}".encode("utf-8")
        ).decode("ascii")
        return (self.service.users().drafts()
                .create(userId="me", body={"message": {"raw": raw}}).execute())

    def list_drafts(self) -> list[dict]:
        got = self.service.users().drafts().list(userId="me").execute()
        return got.get("drafts", []) or []


def gmail_client(session: TwinSession):
    """Real mailbox when GMAIL_MODE=real (draft-only), else the twin/simulator."""
    if is_real("gmail") and SETTINGS.gmail_credentials_path:
        return GmailReal(session)
    return GmailTwin(session)
