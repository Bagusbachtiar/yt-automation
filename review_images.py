#!/usr/bin/env python3
"""
Telegram image review — send candidate photos per script line, human picks one.

Reads:   image_candidates.json  (from fetch_images.py)
Saves:   images/{id}.jpg        (chosen image per line)

Usage:   python review_images.py
Env:     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (in .env)

Review flow per line:
  1. Bot shows up to 3 Commons photos — reply 1/2/3 to pick, or "next" for Pexels
  2. Bot shows up to 3 Pexels photos  — reply 1/2/3 to pick, or "next" for Pixabay
  3. Bot shows up to 3 Pixabay photos — reply 1/2/3 to pick, or "skip" to leave blank
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

TG_API           = "https://api.telegram.org"
CANDIDATES_JSON  = Path("image_candidates.json")
IMAGES_DIR       = Path("images")


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
    """Ack all pending updates so old messages don't trigger review. Returns last seen update_id."""
    resp = _tg(token, "getUpdates", {"timeout": 0})
    results = resp.get("result", [])
    if not results:
        return 0
    last_id = results[-1]["update_id"]
    _tg(token, "getUpdates", {"offset": last_id + 1, "timeout": 0})
    return last_id


def wait_for_reply(token: str, chat_id: str, after_id: int) -> tuple[str, int]:
    """Long-poll until a text message arrives from chat_id. Returns (text.lower(), update_id)."""
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
    req = urllib.request.Request(url, headers={"User-Agent": "yt-automation/1.0"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(5)
            else:
                raise
    raise RuntimeError("fetch_bytes: unreachable")


# ── Review loop ───────────────────────────────────────────────────────────────

def review_line(token: str, chat_id: str, lid: str, data: dict,
                update_id: int, total: int) -> tuple[str | None, int]:
    """
    Show candidate photos from each source in order.
    Returns (chosen_url | None, update_id).
    """
    text    = data["text"]
    sources = data["sources"]
    source_order = [
        ("Wikipedia article", sources.get("wikipedia", [])),
        ("Commons search",    sources.get("commons",   [])),
        ("Pexels",            sources.get("pexels",    [])),
        ("Pixabay",           sources.get("pixabay",   [])),
    ]
    available_sources = [(name, urls) for name, urls in source_order if urls]

    send_message(token, chat_id,
        f"Line {lid}/{total}\n\"{text}\"\nKeyword: {data['keyword']}"
    )

    for src_idx, (src_name, urls) in enumerate(available_sources):
        is_last_source = (src_idx == len(available_sources) - 1)

        send_message(token, chat_id, f"--- {src_name} options ---")

        loaded_urls = []
        for i, url in enumerate(urls, 1):
            try:
                img_bytes = fetch_bytes(url)
                send_photo(token, chat_id, img_bytes, caption=f"Option {i}")
                loaded_urls.append(url)
            except Exception as e:
                send_message(token, chat_id, f"Option {i} failed to load: {e}")

        if not loaded_urls:
            send_message(token, chat_id, f"No loadable images from {src_name}.")
            continue

        opts = "/".join(str(i) for i in range(1, len(loaded_urls) + 1))
        if is_last_source:
            prompt = f"Reply {opts} to pick, or 'skip' to leave this line blank."
        else:
            next_src = available_sources[src_idx + 1][0]
            prompt = f"Reply {opts} to pick, or 'next' for {next_src} options, or 'skip' to leave blank."
        send_message(token, chat_id, prompt)

        while True:
            reply, update_id = wait_for_reply(token, chat_id, update_id)
            if reply.isdigit() and 1 <= int(reply) <= len(loaded_urls):
                return loaded_urls[int(reply) - 1], update_id
            elif reply == "next" and not is_last_source:
                break
            elif reply == "skip":
                return None, update_id
            else:
                send_message(token, chat_id, f"Not recognized. Reply {opts}, 'next', or 'skip'.")

    send_message(token, chat_id, "No sources had loadable images for this line.")
    return None, update_id


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

    # Figure out which lines still need images
    pending = {
        lid: data for lid, data in candidates.items()
        if not (IMAGES_DIR / f"{lid}.jpg").exists()
    }
    if not pending:
        print("All images already present. Nothing to review.")
        return

    total = len(candidates)
    print(f"Reviewing {len(pending)} lines via Telegram...\n")

    # Drain old Telegram messages so stale replies don't trigger anything
    update_id = drain_updates(token)

    send_message(token, chat_id,
        f"Image review started — {len(pending)} line(s) to review.\n"
        f"Commands: reply a number to pick, 'next' to see next source, 'skip' to skip line."
    )

    ok = skipped = 0

    for lid, data in pending.items():
        chosen_url, update_id = review_line(token, chat_id, lid, data, update_id, total)

        dest = IMAGES_DIR / f"{lid}.jpg"
        if chosen_url:
            try:
                img_bytes = fetch_bytes(chosen_url)
                dest.write_bytes(img_bytes)
                send_message(token, chat_id, f"Saved line {lid}. ({dest.stat().st_size // 1024} KB)")
                print(f"  Line {lid}: saved ({dest.stat().st_size // 1024} KB)")
                ok += 1
            except Exception as e:
                send_message(token, chat_id, f"Download failed for line {lid}: {e}")
                print(f"  Line {lid}: download failed — {e}")
        else:
            send_message(token, chat_id, f"Skipped line {lid} — no image.")
            print(f"  Line {lid}: skipped")
            skipped += 1

    send_message(token, chat_id,
        f"Review done. Saved={ok}  Skipped={skipped}\n"
        f"Next: python generate_audio.py && python assemble.py"
    )
    print(f"\nDone. Saved={ok}  Skipped={skipped}")
    print("Run: python generate_audio.py && python assemble.py")


if __name__ == "__main__":
    main()
