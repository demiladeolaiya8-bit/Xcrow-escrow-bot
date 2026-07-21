# ⚡ XCROW — Telegram Crypto Escrow Bot

Production-ready Telegram escrow bot. Group-centric workflow, real blockchain monitoring, HD wallet per deal.

---

## VPS Quick Start (3 steps)

### Step 1 — Upload and prepare

```bash
# On your VPS
unzip xcrow.zip
cd xcrow

# Install Python deps (only needed for auth + mnemonic scripts)
pip install pyrogram TgCrypto

# Create your .env file with all credentials pre-filled
python create_env.py
```

### Step 2 — Authenticate Pyrogram (ONE TIME ONLY)

```bash
python pyrogram_auth.py
# Enter the verification code Telegram sends to your phone
# Session saved to sessions/xcrow_user.session
```

### Step 3 — Start

```bash
docker-compose up -d --build

# Watch logs
docker-compose logs -f bot
```

---

## How the Bot Works

```
User DMs bot → /start
  └─ Taps "Create Escrow Group"
       └─ Bot creates a Telegram supergroup automatically
            └─ Sends invite link to creator

Both parties join the group
  └─ Step 1: Someone taps "I am the Seller"
  └─ Step 2: Seller selects payout network + types their wallet address
  └─ Step 3: Someone (not seller) taps "I am the Buyer"
  └─ Step 4: Buyer types description + amount + selects currency → both confirm
  └─ Step 5: Bot generates unique deposit address + QR code
               └─ Blockchain monitor polls every 30s
                    └─ Payment detected → group notified automatically
                         └─ Seller delivers
                              └─ Buyer taps "Confirm Delivery"
                                   └─ Admin releases funds to seller
```

---

## Supported Coins

| Coin | Auto-detect | HD Wallet |
|------|-------------|-----------|
| USDT TRC20 | ✅ TronGrid | ✅ |
| USDT BEP20 | ✅ BscScan  | ✅ |
| ETH        | ✅ Etherscan| ✅ |
| BTC        | Manual      | ✅ |
| SOL        | Manual      | ✅ |
| TON        | Manual      | ✅ |
| LTC        | Manual      | ✅ |

Manual = admin confirms via /admin panel after buyer sends payment.

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome screen + Create Escrow Group button |
| `/create` | Same as pressing Create Escrow Group |
| `/history` | Your deal history |
| `/wallet` | Your saved payout wallets |
| `/new_wallet` | Info on adding a wallet |
| `/calculate` | Calculate escrow fees |
| `/escrow_fee` | Fee structure |
| `/verify` | Verify a deal by ID |
| `/feedback` | Send feedback to admin |
| `/support` | Contact support |
| `/menu` | All commands |
| `/admin` | Admin panel (admin only) |

---

## Admin Panel

Send `/admin` to the bot in a private chat to open the admin panel:

- 📋 **All Deals** — browse all deals, view details, release/refund/cancel
- 🔥 **Open Disputes** — resolve disputes (release to seller or refund to buyer)
- 👥 **Users** — browse users, ban/unban
- 📊 **Statistics** — deal counts by status

---

## REST API

Admin API runs on port 8000. All endpoints require `X-API-Key` header.

- `GET /health` — health check
- `GET /docs` — Swagger UI
- `GET /deals/` — list all deals
- `GET /deals/{uid}` — get deal details
- `POST /deals/{uid}/release` — mark deal completed
- `POST /deals/{uid}/refund` — mark deal refunded
- `GET /admin/stats` — statistics
- `POST /admin/users/{id}/ban` — ban/unban user

---

## Configuration (.env)

All credentials are pre-filled in your `.env`. Key settings:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | Your bot token |
| `ADMIN_IDS` | Your Telegram ID |
| `HD_MNEMONIC` | Your 24-word wallet seed |
| `ESCROW_FEE_PERCENT` | `1.0` |
| `FEE_MODEL` | `buyer_pays` |
| `MONITOR_INTERVAL_SECONDS` | `30` |

---

## HD Wallet — Critical

Your mnemonic is: **already set in `.env`** via `create_env.py`.

```
⚠️  BACK IT UP IMMEDIATELY.
⚠️  Never regenerate it once you have live deals.
⚠️  If you lose it, you lose access to all deposit wallets.
```

---

## Troubleshooting

**Bot doesn't create groups:**
```bash
python pyrogram_auth.py   # authenticate first
docker-compose restart bot
```

**Database connection error:**
```bash
docker-compose up -d postgres   # start postgres first
docker-compose logs postgres    # check for errors
```

**Bot crashes on start:**
```bash
docker-compose logs bot   # check for config errors
```
