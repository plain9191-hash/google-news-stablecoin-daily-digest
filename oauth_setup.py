#!/usr/bin/env python3
"""Generate Gmail OAuth refresh token for stablecoin digest project."""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> None:
    load_dotenv()

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first.")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPE,
    )

    creds = flow.run_local_server(port=0)
    output = {
        "refresh_token": creds.refresh_token,
        "token": creds.token,
        "scopes": creds.scopes,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    with open("oauth_token.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("Saved oauth_token.json")
    print("Use refresh_token value in GOOGLE_REFRESH_TOKEN")


if __name__ == "__main__":
    main()
