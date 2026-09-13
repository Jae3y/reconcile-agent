"""One-time Google OAuth consent for draft-only Gmail access.

Run once. It opens a browser (and prints the URL in case it doesn't), you
approve with the Google account whose drafts the agent should write, and the
token is cached at GMAIL_TOKEN_PATH so later runs are non-interactive.

Scope is gmail.compose - the narrowest scope that can create a draft. The agent
has no send path anywhere in the codebase.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain script from anywhere in the repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow

from agent.config import REPO_ROOT, SETTINGS

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def main() -> int:
    creds_path = REPO_ROOT / SETTINGS.gmail_credentials_path
    token_path = REPO_ROOT / SETTINGS.gmail_token_path

    if not creds_path.exists():
        print(f"ERROR: client secret not found at {creds_path}")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)

    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print("=" * 78)
    print("OPEN THIS URL AND APPROVE (a browser should also open automatically):")
    print()
    print(auth_url)
    print()
    print("=" * 78)
    sys.stdout.flush()

    creds = flow.run_local_server(port=0, open_browser=True,
                                  authorization_prompt_message="")
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nOK - token cached at {token_path.name}")

    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    print(f"authenticated as: {profile.get('emailAddress')}")
    drafts = service.users().drafts().list(userId="me").execute().get("drafts", []) or []
    print(f"existing drafts: {len(drafts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
