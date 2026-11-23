"""Interactive helper script to generate GOOGLE_REFRESH_TOKEN for Personal Gmail & Google Calendar.

Run this script locally:
    python scripts/get_google_oauth_token.py
"""

import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def main():
    print("=" * 65)
    print(" Google OAuth Refresh Token Generator (Gmail + Calendar) ")
    print("=" * 65)

    client_id = input("\nEnter your GOOGLE_CLIENT_ID: ").strip()
    client_secret = input("Enter your GOOGLE_CLIENT_SECRET: ").strip()

    if not client_id or not client_secret:
        print("\n[!] Error: Client ID and Client Secret are required.")
        return

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"],
        }
    }

    print("\nOpening your browser for one-time Google Account authorization...")
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

    print("\n" + "=" * 65)
    print(" AUTHORIZATION SUCCESSFUL! ADD THESE TO YOUR .env FILE: ")
    print("=" * 65)
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
