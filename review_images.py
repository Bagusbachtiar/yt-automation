#!/usr/bin/env python3
"""
Telegram image review — show all candidates at once, assign each to a line.

Reads:   image_candidates.json  (from fetch_images.py)
Saves:   images/{id}.jpg        (chosen image per line)

Usage:   python review_images.py
Env:     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (in .env)

Flow:
  1. Bot sends up to 20 images numbered #1-#N (best sources first, deduped)
  2. For each line: bot shows line text + keyword, you reply with a number
  3. Commands: number to assign, 'skip' to leave blank, 'quit' to stop
"""

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

TG_API          = "https://api.telegram.org"
CANDIDATES_JSON = Path("image_candidates.json")
IMAGES_DIR      = Path("images")
MAX_POOL        = 20
SOURCE_PRIORITY = ["pexels_video", "pexels", "pixabay", "wikipedia", "wiki_keyword", "flickr", "commons"]
_VIDEO_SOURCES  = {"pexels_video"}


# ── Env loader ────────────────────────────────────────────────────────────────

def load_env():
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg(token: str, method: str, params: dict = None, body: bytes = None, content_type: str = None) -> dict:
    url = f"{TG_API}/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=body)
    if content_type:
        req.add_header("Content-Type", content_type)
    timeout = 70 if method == "getUpdates" else 20
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return json.loads(r.read())


def send_message(token: str, chat_id: str, text: str):
    _tg(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_photo(token: str, chat_id: str, img_bytes: bytes, caption: str = ""):
    boundary = "tgboundary42"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="photo.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + img_bytes + f"\r\n--{boundary}--\r\n".encode()
    _tg(token, "sendPhoto", body=body, content_type=f"multipart/form-data; boundary={boundary}")


def drain_updates(token: str) -> int:
    resp = _tg(token, "getUpdates", {"timeout": 0})
    results = resp.get("result", [])
    if not results:
        return 0
    last_id = results[-1]["update_id"]
    _tg(token, "getUpdates", {"offset": last_id + 1, "timeout": 0})
    return last_id


def wait_for_reply(token: str, chat_id: str, after_id: int) -> tuple[str, int]:
    update_id = after_id
    while True:
        try:
            resp = _tg(token, "getUpdates", {
                "offset": update_id + 1,
                "timeout": 60,
                "allowed_updates": json.dumps(["message"]),
            })
        except Exception:
            time.sleep(2)
            continue
        for u in resp.get("result", []):
            update_id = u["update_id"]
            msg = u.get("message", {})
            if str(msg.get("chat", {}).get("id")) == str(chat_id):
                return msg.get("text", "").strip().lower(), update_id


# ── Image fetch ───────────────────────────────────────────────────────────────

def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "yt-automation/1.0 (bagusbachtiar50@gmail.com)"}
    )
    delays = [10, 30]
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                wait = delays[attempt]
                print(f"    [429] waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("fetch_bytes: unreachable")


# ── Pool builder ──────────────────────────────────────────────────────────────

def collect_pool(candidates: dict, max_images: int = MAX_POOL) -> list[tuple]:
    """Returns [(src, url, thumb), ...]. thumb is preview URL for videos, None for images."""
    seen = set()
    pool = []
    for source in SOURCE_PRIORITY:
        for data in candidates.values():
            for entry in data["sources"].get(source, []):
                if source in _VIDEO_SOURCES:
                    url   = entry.get("url", "")   if isinstance(entry, dict) else entry
                    thumb = entry.get("thumb")      if isinstance(entry, dict) else None
                    if not thumb:
                        continue  # skip video with no thumbnail — can't preview in Telegram
                else:
                    url, thumb = entry, None
                if url and url not in seen:
                    seen.add(url)
                    pool.append((source, url, thumb))
                    if len(pool) >= max_images:
                        return pool
    return pool


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_env()
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID",   "").strip()

    if not token:
        sys.exit("[ERROR] TELEGRAM_BOT_TOKEN not set in .env")
    if not chat_id:
        sys.exit("[ERROR] TELEGRAM_CHAT_ID not set in .env")
    if not CANDIDATES_JSON.exists():
        sys.exit(f"[ERROR] {CANDIDATES_JSON} not found — run fetch_images.py first.")

    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    IMAGES_DIR.mkdir(exist_ok=True)

    pending = {
        lid: data for lid, data in candidates.items()
        if not (IMAGES_DIR / f"{lid}.jpg").exists()
        and not (IMAGES_DIR / f"{lid}.mp4").exists()
    }
    if not pending:
        print("All media already present. Nothing to review.")
        return

    print(f"Building media pool...")
    pool_entries = collect_pool(candidates)

    # Step 1 — send all previews
    send_message(token, chat_id,
        f"Sending {len(pool_entries)} media — note the numbers, then assign each line.\n"
        f"[VIDEO] items show thumbnail; actual video clip saved when you pick."
    )
    loaded = []  # (src, url, is_video)
    for src, url, thumb in pool_entries:
        is_video = src in _VIDEO_SOURCES
        try:
            preview_bytes = fetch_bytes(thumb if (is_video and thumb) else url)
            num = len(loaded) + 1
            label = f"#{num} — {src}" + (" [VIDEO]" if is_video else "")
            send_photo(token, chat_id, preview_bytes, caption=label)
            loaded.append((src, url, is_video))
            print(f"  #{num} uploaded ({src})")
            time.sleep(1.5)
        except Exception as e:
            print(f"  failed ({src}): {e}")

    if not loaded:
        send_message(token, chat_id, "No media loaded. Aborting.")
        return

    send_message(token, chat_id,
        f"{len(loaded)} items ready. Now assign each line.\n"
        f"Commands: reply number (1-{len(loaded)}) to assign, 'skip', or 'quit'."
    )

    # Drain stale messages accumulated while uploading
    update_id = drain_updates(token)

    # Step 2 — assign per line
    ok = skipped = 0
    for lid, data in pending.items():
        send_message(token, chat_id,
            f"Line {lid}/{len(candidates)}: \"{data['text']}\"\n"
            f"Keyword: {data['keyword']}\n"
            f"Reply 1-{len(loaded)}, 'skip', or 'quit'."
        )
        while True:
            reply, update_id = wait_for_reply(token, chat_id, update_id)
            if reply == "quit":
                send_message(token, chat_id, "Review stopped. Run again to continue.")
                print("\nReview stopped by user.")
                print(f"Done. Saved={ok}  Skipped={skipped}")
                return
            elif reply == "skip":
                send_message(token, chat_id, f"Line {lid} skipped.")
                print(f"  Line {lid}: skipped")
                skipped += 1
                break
            elif reply.isdigit() and 1 <= int(reply) <= len(loaded):
                chosen_src, chosen_url, chosen_is_video = loaded[int(reply) - 1]
                ext = ".mp4" if chosen_is_video else ".jpg"
                dest = IMAGES_DIR / f"{lid}{ext}"
                try:
                    media_bytes = fetch_bytes(chosen_url)
                    dest.write_bytes(media_bytes)
                    send_message(token, chat_id, f"Line {lid} saved. ({dest.stat().st_size // 1024} KB)")
                    print(f"  Line {lid}: saved ({dest.stat().st_size // 1024} KB)")
                    ok += 1
                except Exception as e:
                    send_message(token, chat_id, f"Download failed: {e} — pick another or 'skip'.")
                    continue
                break
            else:
                send_message(token, chat_id, f"Not recognized. Reply 1-{len(loaded)}, 'skip', or 'quit'.")

    send_message(token, chat_id,
        f"Review done. Saved={ok}  Skipped={skipped}\n"
        f"Next: python generate_audio.py && python assemble.py"
    )
    print(f"\nDone. Saved={ok}  Skipped={skipped}")
    print("Run: python generate_audio.py && python assemble.py")


if __name__ == "__main__":
    main()
