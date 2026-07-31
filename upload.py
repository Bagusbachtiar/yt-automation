#!/usr/bin/env python3
"""
Upload output.mp4 to YouTube using Data API v3.

Usage:
  python upload.py --file output.mp4 --script script.json
  python upload.py --file output.mp4 --script script.json --channel faunaworks
  python upload.py --file output.mp4 --script script.json --publish-at "2026-08-01 14:00"
  python upload.py --info --channel faunaworks   # print channel info only

Multi-channel setup:
  channels.json maps key -> display name, e.g. {"faunaworks": "FaunaWorks"}
  Per channel: client_secrets_{key}.json  (OAuth Desktop app from Google Cloud Console)
               token_{key}.json           (auto-created on first auth)

First run per channel opens a browser for OAuth consent.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CHANNELS_JSON = Path("channels.json")
SCRIPT_JSON   = Path("script.json")
DEFAULT_VIDEO = Path("output.mp4")
CATEGORY_ID   = "15"   # Pets & Animals
SCOPES        = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def load_channels() -> dict:
    if CHANNELS_JSON.exists():
        try:
            return json.loads(CHANNELS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"faunaworks": "FaunaWorks"}


def _secrets_path(channel: str) -> Path:
    """client_secrets_{channel}.json, falls back to legacy client_secrets.json."""
    p = Path(f"client_secrets_{channel}.json")
    if not p.exists():
        legacy = Path("client_secrets.json")
        if legacy.exists():
            return legacy
    return p


def _token_path(channel: str) -> Path:
    return Path(f"token_{channel}.json")


def get_credentials(channel: str):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        sys.exit("[ERROR] Missing: pip install google-api-python-client google-auth-oauthlib")

    secrets = _secrets_path(channel)
    token   = _token_path(channel)

    if not secrets.exists():
        sys.exit(
            f"[ERROR] {secrets} not found.\n"
            f"  1. Google Cloud Console > APIs & Services > Credentials\n"
            f"  2. Create OAuth 2.0 Client ID (Desktop app type)\n"
            f"  3. Download JSON, save as client_secrets_{channel}.json"
        )

    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")

    return creds


def get_channel_info(youtube) -> dict:
    r     = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    items = r.get("items", [])
    if not items:
        return {}
    ch = items[0]
    thumbs = ch["snippet"].get("thumbnails", {})
    return {
        "id":        ch["id"],
        "name":      ch["snippet"]["title"],
        "subs":      ch["statistics"].get("subscriberCount", "?"),
        "thumbnail": (thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
    }


def build_metadata(script: dict, publish_at: str | None = None) -> dict:
    title    = script.get("title", "FaunaWorks")
    base_desc = script.get("yt_description",
                           " ".join(l["text"] for l in script.get("lines", [])))
    description = base_desc + (
        "\n\n#Shorts #AnimalFacts #Wildlife #NatureFacts #FaunaWorks #Science #Animals #LearnOnYouTube"
    )
    tags = [t for t in script.get("yt_tags", ["animals", "wildlife", "nature", "shorts", "faunaworks"]) if t]

    status: dict = {"selfDeclaredMadeForKids": False}
    if publish_at:
        try:
            # Accept "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM"
            fmt = "%Y-%m-%dT%H:%M" if "T" in publish_at else "%Y-%m-%d %H:%M"
            dt  = datetime.strptime(publish_at.strip(), fmt).replace(tzinfo=timezone.utc)
            status["publishAt"]     = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            status["privacyStatus"] = "private"
        except ValueError:
            sys.exit("[ERROR] Invalid date. Use: YYYY-MM-DD HH:MM  (UTC)")
    else:
        status["privacyStatus"] = "public"

    return {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        tags,
            "categoryId":  CATEGORY_ID,
        },
        "status": status,
    }


def do_upload(youtube, video_path: Path, metadata: dict) -> str:
    from googleapiclient.http import MediaFileUpload

    media   = MediaFileUpload(str(video_path), mimetype="video/mp4",
                              resumable=True, chunksize=4 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=metadata, media_body=media)

    print("Uploading", end="", flush=True)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"\rUploading {pct}%...", end="", flush=True)
    print()
    return response["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",       type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--script",     type=Path, default=SCRIPT_JSON)
    parser.add_argument("--channel",    default=None, help="Key from channels.json")
    parser.add_argument("--publish-at", default=None, dest="publish_at",
                        help="Schedule: 'YYYY-MM-DD HH:MM' UTC. Omit = publish immediately.")
    parser.add_argument("--info",       action="store_true",
                        help="Print channel info only (no upload)")
    args = parser.parse_args()

    channels = load_channels()
    channel  = args.channel or next(iter(channels))
    if channel not in channels:
        sys.exit(f"[ERROR] Channel '{channel}' not in channels.json. Available: {list(channels)}")

    try:
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("[ERROR] Missing: pip install google-api-python-client google-auth-oauthlib")

    creds   = get_credentials(channel)
    youtube = build("youtube", "v3", credentials=creds)
    info    = get_channel_info(youtube)

    if info:
        print(f"CHANNEL: {info['name']} | {info['subs']} subscribers")
        if info.get("thumbnail"):
            print(f"THUMBNAIL: {info['thumbnail']}")

    if args.info:
        return

    if not args.file.exists():
        sys.exit(f"[ERROR] Video not found: {args.file}")
    if not args.script.exists():
        sys.exit(f"[ERROR] Script not found: {args.script}")

    script   = json.loads(args.script.read_text(encoding="utf-8"))
    metadata = build_metadata(script, args.publish_at)
    title    = metadata["snippet"]["title"]
    mb       = args.file.stat().st_size / 1024 / 1024

    print(f"Title: {title}")
    print(f"File:  {args.file}  ({mb:.1f} MB)")
    if args.publish_at:
        print(f"Scheduled publish: {args.publish_at} UTC")
    else:
        print("Privacy: public (immediate)")
    print()

    vid_id  = do_upload(youtube, args.file, metadata)
    vid_url = f"https://www.youtube.com/shorts/{vid_id}"
    if args.publish_at:
        print(f"Scheduled! Publishes at {args.publish_at} UTC")
    else:
        print("Upload complete!")
    print(f"URL: {vid_url}")


if __name__ == "__main__":
    main()
