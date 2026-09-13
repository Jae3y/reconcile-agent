"""Environment + twin-session configuration.

Every external base URL lives here so nothing downstream ever hardcodes a
production host (see CLAUDE.md: "Never hardcode a real production API base URL").
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = REPO_ROOT / ".arga-session.json"

load_dotenv(REPO_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


@dataclass
class Settings:
    arga_api_key: str = field(default_factory=lambda: _env("ARGA_LABS_API_KEY"))
    arga_api_base: str = field(default_factory=lambda: _env("ARGA_API_BASE", "https://api.argalabs.com"))

    stripe_api_key: str = field(default_factory=lambda: _env("STRIPE_API_KEY", "sk_test_arga_twin"))
    hubspot_token: str = field(default_factory=lambda: _env("HUBSPOT_ACCESS_TOKEN"))
    slack_token: str = field(default_factory=lambda: _env("SLACK_BOT_TOKEN"))
    slack_channel: str = field(default_factory=lambda: _env("SLACK_APPROVAL_CHANNEL", "#approvals"))

    lemma_api_key: str = field(default_factory=lambda: _env("LEMMA_API_KEY"))
    lemma_project_id: str = field(default_factory=lambda: _env("LEMMA_PROJECT_ID"))

    gmail_credentials_path: str = field(default_factory=lambda: _env("GMAIL_CREDENTIALS_PATH"))
    gmail_token_path: str = field(default_factory=lambda: _env("GMAIL_TOKEN_PATH", "gmail_token.json"))

    # Per-service routing: "real" hits the genuine vendor API, "twin" hits the
    # Arga twin or the local simulator standing in for it. Each service is
    # independent, so Slack can be real while Stripe is simulated.
    stripe_mode: str = field(default_factory=lambda: _env("STRIPE_MODE", "twin").lower())
    hubspot_mode: str = field(default_factory=lambda: _env("HUBSPOT_MODE", "twin").lower())
    slack_mode: str = field(default_factory=lambda: _env("SLACK_MODE", "twin").lower())
    gmail_mode: str = field(default_factory=lambda: _env("GMAIL_MODE", "twin").lower())

    agy_bin: str = field(default_factory=lambda: _env("AGY_BIN", "agy"))
    agy_model: str = field(default_factory=lambda: _env("AGY_MODEL", "claude-sonnet-4-6"))
    judge_concurrency: int = field(default_factory=lambda: int(_env("JUDGE_CONCURRENCY", "6") or 6))
    judge_timeout: int = field(default_factory=lambda: int(_env("JUDGE_TIMEOUT", "180") or 180))

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n, "")]
        if missing:
            raise RuntimeError(
                f"Missing required config: {', '.join(missing)}. Set them in {REPO_ROOT / '.env'}"
            )


REAL_BASE = {
    "stripe": "https://api.stripe.com",
    "hubspot": "https://api.hubapi.com",
    "slack": "https://slack.com/api/",
    "gmail": "https://gmail.googleapis.com",
}


SETTINGS = Settings()


def mode_for(service: str) -> str:
    """'real' or 'twin' for one service."""
    return getattr(SETTINGS, f"{service}_mode", "twin")


def is_real(service: str) -> bool:
    return mode_for(service) == "real"


@dataclass
class TwinSession:
    """Base URLs + tokens for the currently provisioned Arga twins.

    The free Arga plan allows one twin per run with a fixed 10-minute TTL, so
    each service is its own run and the session tracks the earliest expiry.
    """

    twins: dict[str, dict] = field(default_factory=dict)

    def base_url(self, name: str) -> str:
        entry = self.twins.get(name)
        if not entry:
            raise RuntimeError(
                f"No '{name}' twin provisioned. Run: python main.py provision"
            )
        return entry["base_url"].rstrip("/")

    def token(self, name: str, fallback: str = "") -> str:
        entry = self.twins.get(name) or {}
        return (entry.get("env_vars") or {}).get(_TOKEN_KEY.get(name, ""), "") or fallback

    def expires_at(self) -> datetime | None:
        stamps = [
            datetime.fromisoformat(e["expires_at"])
            for e in self.twins.values()
            if e.get("expires_at")
        ]
        return min(stamps) if stamps else None

    def seconds_left(self) -> float:
        exp = self.expires_at()
        if not exp:
            return 0.0
        return (exp - datetime.now(timezone.utc)).total_seconds()

    def is_live(self, min_seconds: float = 30.0) -> bool:
        return bool(self.twins) and self.seconds_left() > min_seconds

    def save(self, path: Path = SESSION_FILE) -> None:
        path.write_text(json.dumps({"twins": self.twins}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = SESSION_FILE) -> "TwinSession":
        if not path.exists():
            return cls()
        try:
            return cls(twins=json.loads(path.read_text(encoding="utf-8")).get("twins", {}))
        except (json.JSONDecodeError, OSError):
            return cls()


_TOKEN_KEY = {
    "hubspot": "HUBSPOT_ACCESS_TOKEN",
    "slack": "SLACK_BOT_TOKEN",
    "stripe": "STRIPE_API_KEY",
}
