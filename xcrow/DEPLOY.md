# Xcrow Bot — Deployment Guide

## Prerequisites
- VPS running Ubuntu/Debian with Docker + Docker Compose installed
- Git configured with access to the GitHub repo

---

## 1. Pull latest code on your VPS

```bash
cd /root/Xcrow-escrow-bot
git pull origin main
```

---

## 2. Configure your .env file

Open `.env` in the bot directory and add/update these values:

```bash
nano /root/Xcrow-escrow-bot/xcrow/xcrow/.env
```

**Required new keys to add:**

```env
# ── Main escrow wallet (SafePal) ──────────────────────────────────────────
MAIN_WALLET_BSC_ETH=0xB79fdeaCc172846a7BE52fdd04E8491424304d37
MAIN_WALLET_BTC=bc1qkda0dmyde93v72kd0ant00kpf2d3d99h5w9d78

# ⚠️ PRIVATE KEY — Never share this. Never commit to GitHub.
# Export from SafePal: Settings → Wallet → Export Private Key → BNB Chain
MAIN_WALLET_PRIVATE_KEY=your_64_char_hex_private_key_here

# Bot / admin settings (already set from previous setup)
BOT_TOKEN=...
ADMIN_IDS=...
DATABASE_URL=postgresql+asyncpg://xcrow:xcrowpass@postgres:5432/xcrow
ADMIN_DASHBOARD_PASSWORD=your-secure-password
```

> **Security:** The private key only lives in `.env` on your VPS. It is
> listed in `.gitignore` and is never pushed to GitHub.

---

## 3. Rebuild and restart the bot

```bash
cd /root/Xcrow-escrow-bot/xcrow/xcrow
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose logs -f bot
```

You should see:
```
✅  Database ready
✅  Platform settings seeded
💼  Main wallet (BSC/ETH): 0xB79fdeaCc...
🔑  Main wallet private key: configured ✅
🚀  Bot is live — polling for updates
🔍  Central wallet monitor started (interval: 30s)
```

---

## 4. Set up auto-start on reboot (systemd)

```bash
# Copy service file
cp /root/Xcrow-escrow-bot/xcrow/systemd/xcrow-bot.service /etc/systemd/system/

# Enable and start
systemctl daemon-reload
systemctl enable xcrow-bot
systemctl start xcrow-bot

# Check status
systemctl status xcrow-bot
```

---

## 5. Auto-restart on crash (Docker policy)

Your `docker-compose.yml` should already have `restart: unless-stopped` on the
bot service. If not, add it:

```yaml
services:
  bot:
    restart: unless-stopped
```

---

## 6. Admin dashboard

Access the web dashboard at:
```
http://YOUR_VPS_IP:8000/panel
```

Login with `ADMIN_DASHBOARD_PASSWORD` from your `.env`.

From the dashboard you can:
- View all incoming payments with TX hash, sender, confirmations, timestamp
- Configure fee % and payout wallet
- Update main wallet addresses
- Manage deals, disputes, users

---

## 7. How payment detection works

1. Buyer creates a deal and selects the payment network (USDT BEP20/ERC20, ETH, BTC)
2. Bot shows your main wallet address + a **unique amount** (e.g. $50.37 instead of $50.00)
3. Buyer sends the **exact amount** shown to your wallet
4. Central monitor scans your wallet every 30 seconds across all networks
5. Matching payment → deal marked FUNDED → parties notified
6. After buyer confirms delivery → funds auto-released from your main wallet to seller

---

## 8. Monitoring logs

```bash
# Live logs
docker compose logs -f bot

# Saved logs (last 100 lines)
tail -100 /root/Xcrow-escrow-bot/xcrow/xcrow/logs/xcrow.log

# Check if bot is running
docker compose ps
```

---

## 9. Updating the bot

```bash
cd /root/Xcrow-escrow-bot
git pull origin main
cd xcrow/xcrow
docker compose down && docker compose build && docker compose up -d
```
