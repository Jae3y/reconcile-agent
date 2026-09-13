"""Print the real #approvals messages the agent posted to Slack.

Reads them back through the live Slack API with the same bot token the agent
writes with, so what you see here is genuinely what is sitting in the workspace -
useful when you need to show the Slack side without a browser session.

    python scripts/show_slack.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slack_sdk import WebClient

from agent.config import SETTINGS

CHANNEL_NAME = "approvals"


def main() -> int:
    client = WebClient(token=SETTINGS.slack_token)

    who = client.auth_test()
    print("=" * 74)
    print(f"  LIVE SLACK  ·  workspace: {who['team']}  ·  bot: {who['user']}")
    print("=" * 74)

    channel_id = None
    for ch in client.conversations_list(types="public_channel", limit=200)["channels"]:
        if ch["name"] == CHANNEL_NAME:
            channel_id = ch["id"]
            break
    if not channel_id:
        print(f"  #{CHANNEL_NAME} not found")
        return 1

    history = client.conversations_history(channel=channel_id, limit=10)
    messages = history["messages"]
    print(f"\n  #{CHANNEL_NAME} — {len(messages)} most recent approval requests\n")

    for msg in messages:
        text = msg.get("text", "")
        lines = [ln for ln in text.split("\n") if ln.strip()]
        print("  " + "-" * 70)
        for ln in lines[:6]:
            clean = ln.replace("*", "").strip()
            if clean:
                print(f"   {clean[:96]}")
        reactions = msg.get("reactions") or []
        if reactions:
            print(f"   reactions: {', '.join(':' + r['name'] + ':' for r in reactions)}")
    print("  " + "-" * 70)
    print("\n  These are read back live from Slack, not from a local log.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
