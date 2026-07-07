"""Authenticate Gmail API access and make a small test call.

Place a Google OAuth Desktop client file at ./credentials.json first. On the
first successful run this writes ./token.json for future Gmail API calls.
"""

from pathlib import Path
import sys

from googleapiclient.errors import HttpError

from jason import config
from jason.gmail_client import SCOPES, get_service


def main() -> int:
    credentials_path = Path(config.GMAIL_CREDENTIALS_FILE)
    if not credentials_path.exists():
        print(
            f"Missing {credentials_path}. Download an OAuth Desktop app JSON "
            "from Google Cloud Console and save it here as credentials.json."
        )
        return 1

    try:
        service = get_service()
        profile = service.users().getProfile(userId="me").execute()
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
    except HttpError as exc:
        print(f"Gmail API error: {exc}")
        return 1

    print(f"Authenticated Gmail account: {profile.get('emailAddress', 'unknown')}")
    print(f"OAuth scopes: {', '.join(SCOPES)}")
    print("Labels:")
    for label in labels[:10]:
        print(f"- {label['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
