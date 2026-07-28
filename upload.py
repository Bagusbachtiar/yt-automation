#!/usr/bin/env python3
"""
Upload output.mp4 to YouTube as a Short using script.json for title/description.

Run: python upload.py
     python upload.py --file videos_output/octopus.mp4  (custom file)

First run opens a browser for OAuth. Token saved to yt_token.json for reuse.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_JSON    = Path("script.json")
CLIENT_SECRETS = Path("client_secrets.json")
TOKEN_FILE     = Path("yt_token.json")
DEFAULT_VIDEO  = Path("output.mp4")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

CATEGORY_ID  = "15"   # Pets & Animals
PRIVACY      = "public"


def get_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds


def build_metadata(script: dict) -> dict:
    title = script.get("title", "FaunaWorks")

    base_desc = script.get("yt_description", " ".join(l["text"] for l in script.get("lines", [])))
    description = base_desc + "\n\n#Shorts #AnimalFacts #Wildlife #NatureFacts #FaunaWorks #Science #Animals #LearnOnYouTube"

    tags = script.get("yt_tags", ["animals", "wildlife", "nature", "shorts", "faunaworks"])
    tags = [t for t in tags if t]

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }


def upload(video_path: Path, metadata: dict, creds):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=creds)

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=4 * 1024 * 1024,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=metadata,
        media_body=media,
    )

    print("Uploading", end="", flush=True)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"\rUploading {pct}%...", end="", flush=True)

    print(f"\rUploaded!")
    video_id = response["id"]
    print(f"URL: https://www.youtube.com/shorts/{video_id}")
    return video_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--script", type=Path, default=SCRIPT_JSON)
    args = parser.parse_args()

    if not args.file.exists():
        sys.exit(f"[ERROR] Video not found: {args.file}")
    if not CLIENT_SECRETS.exists():
        sys.exit("[ERROR] client_secrets.json not found.")
    if not args.script.exists():
        sys.exit(f"[ERROR] {args.script} not found.")

    script   = json.loads(args.script.read_text(encoding="utf-8"))
    metadata = build_metadata(script)

    print(f"Title:   {metadata['snippet']['title']}")
    print(f"File:    {args.file}  ({args.file.stat().st_size // 1024 // 1024} MB)")
    print(f"Privacy: {PRIVACY}\n")

    creds = get_credentials()
    upload(args.file, metadata, creds)


if __name__ == "__main__":
    main()
