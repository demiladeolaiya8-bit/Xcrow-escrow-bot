"""
Xcrow — Pyrogram one-time authentication script.

Run this ONCE on your VPS BEFORE starting the bot with Docker Compose:

    cd xcrow
    pip install pyrogram TgCrypto
    python pyrogram_auth.py

It will ask for your phone verification code (sent by Telegram),
save a session file to sessions/xcrow_user.session, and exit.
After that, docker-compose up -d will use the saved session automatically.

Flags:
  --mnemonic    Print a freshly generated BIP39 24-word mnemonic and exit.
"""
from __future__ import annotations
import asyncio
import sys
import os

os.makedirs("sessions", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# ── --mnemonic flag ────────────────────────────────────────────────────────
if "--mnemonic" in sys.argv:
    import urllib.request, hashlib
    entropy = os.urandom(32)
    h = hashlib.sha256(entropy).digest()
    bits = bin(int.from_bytes(entropy, "big"))[2:].zfill(256) + bin(h[0])[2:].zfill(8)[:8]
    idxs = [int(bits[i * 11:(i + 1) * 11], 2) for i in range(24)]
    url = "https://raw.githubusercontent.com/trezor/python-mnemonic/master/src/mnemonic/wordlist/english.txt"
    wl = urllib.request.urlopen(url).read().decode().strip().split()
    phrase = " ".join(wl[i] for i in idxs)
    print("\n  ✅  Your new HD mnemonic (24 words):\n")
    print(f"  {phrase}\n")
    print("  ⚠️  Copy this into HD_MNEMONIC in your .env file.")
    print("  ⚠️  NEVER share or lose this — it controls all deposit wallets.\n")
    sys.exit(0)

# ── Pyrogram auth ──────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")
PHONE = os.getenv("PHONE_NUMBER", "")
SESSION = os.getenv("SESSION_NAME", "sessions/xcrow_user")

if not all([API_ID, API_HASH, PHONE]):
    print("❌  API_ID, API_HASH, and PHONE_NUMBER must be set in .env before running this script.")
    sys.exit(1)

try:
    from pyrogram import Client
except ImportError:
    print("❌  pyrogram not installed. Run: pip install pyrogram TgCrypto")
    sys.exit(1)


async def auth() -> None:
    print(f"\n  Authenticating Pyrogram session for {PHONE}...")
    print("  Telegram will send a code to that number / your Telegram app.\n")
    app = Client(SESSION, api_id=int(API_ID), api_hash=API_HASH, phone_number=PHONE)
    await app.start()
    me = await app.get_me()
    await app.stop()
    print(f"\n  ✅  Authenticated as: {me.first_name} (@{me.username})")
    print(f"  ✅  Session saved to: {SESSION}.session")
    print("\n  You can now start the bot:\n    docker-compose up -d --build\n")


asyncio.run(auth())
