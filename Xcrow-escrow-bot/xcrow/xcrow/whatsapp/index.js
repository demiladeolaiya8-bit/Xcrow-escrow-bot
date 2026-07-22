/**
 * Xcrow WhatsApp Escrow Bot — powered by Baileys
 *
 * On first start: scan the QR code that appears in the terminal.
 * After that the session is saved to ./auth_info/ and reconnects automatically.
 */
'use strict';
require('dotenv').config({ path: '../.env' });

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require('@whiskeysockets/baileys');
const pino        = require('pino');
const fs          = require('fs');
const path        = require('path');
const qrTerminal  = require('qrcode-terminal');

const dealHandler = require('./handlers/deal');
const db          = require('./db');

// ── Logger ─────────────────────────────────────────────────────────────────
const logger = pino({
  level: process.env.WA_LOG_LEVEL || 'warn',
  transport: { target: 'pino-pretty', options: { colorize: true } },
});

// ── Auth storage ───────────────────────────────────────────────────────────
const AUTH_DIR = path.join(__dirname, 'auth_info');
fs.mkdirSync(AUTH_DIR, { recursive: true });

let sock;

// ── Send helpers ───────────────────────────────────────────────────────────

async function sendText(jid, text) {
  await sock.sendMessage(jid, { text });
}

async function sendImage(jid, imageBuffer, caption = '') {
  await sock.sendMessage(jid, {
    image:   imageBuffer,
    caption: caption,
    mimetype: 'image/png',
  });
}

// Inject send functions into deal handler
dealHandler.init(sendText, sendImage);

// ── Payment-confirmed poller ───────────────────────────────────────────────
// Python central monitor updates DB when payment is confirmed.
// We poll every 30s and notify WhatsApp parties.

async function pollFundedDeals() {
  try {
    const deals = await db.getNewlyFundedWaDeals();
    for (const deal of deals) {
      const notes  = JSON.parse(deal.admin_notes || '{}');
      const sellerWa = notes.seller_wa;
      const buyerWa  = notes.buyer_wa;

      if (sellerWa) {
        await sendText(sellerWa,
          `🎉 *Payment Received!*\n\n` +
          `Deal \`${deal.deal_uid}\` — ${deal.title}\n\n` +
          `✅ ${deal.total_amount} ${deal.crypto} confirmed in escrow!\n\n` +
          `Now deliver the item/service to the buyer.\n` +
          `Funds will be released when buyer confirms delivery.`
        ).catch(() => {});
      }
      if (buyerWa) {
        await sendText(buyerWa,
          `✅ *Payment Confirmed!*\n\n` +
          `Deal \`${deal.deal_uid}\` — ${deal.title}\n\n` +
          `Your payment is safely held in escrow.\n\n` +
          `When you receive the item, reply:\n` +
          `*confirm ${deal.deal_uid}*`
        ).catch(() => {});
      }

      await db.markWaNotified(deal.id, 'wa_funded_notified');
    }

    // Also check completed deals
    const completed = await db.getCompletedWaDeals();
    for (const deal of completed) {
      const notes = JSON.parse(deal.admin_notes || '{}');
      if (notes.seller_wa) {
        await sendText(notes.seller_wa,
          `💸 *Funds Released!*\n\n` +
          `Deal \`${deal.deal_uid}\` completed.\n` +
          `Payment sent to your wallet. Thank you for using Xcrow!`
        ).catch(() => {});
      }
      if (notes.buyer_wa) {
        await sendText(notes.buyer_wa,
          `🏁 *Deal Complete!*\n\n` +
          `Deal \`${deal.deal_uid}\` — funds released to seller.\n` +
          `Thank you for using Xcrow!`
        ).catch(() => {});
      }
      await db.markWaNotified(deal.id, 'wa_completed_notified');
    }
  } catch (err) {
    logger.error({ err }, 'Poll funded deals error');
  }
}

// ── Main connection ────────────────────────────────────────────────────────

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version }          = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    logger,
    auth:           state,
    browser:        ['Xcrow Escrow Bot', 'Chrome', '1.0.0'],
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
  });

  // ── Creds update ──────────────────────────────────────────────────────
  sock.ev.on('creds.update', saveCreds);

  // ── Connection events ─────────────────────────────────────────────────
  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('\n📱 Scan this QR code with WhatsApp (Settings → Linked Devices → Link a Device):\n');
      qrTerminal.generate(qr, { small: true });
    }
    if (connection === 'open') {
      console.log('✅ WhatsApp connected! Bot is live.');
      // Start polling for funded deals
      setInterval(pollFundedDeals, 30_000);
      pollFundedDeals(); // immediate first run
    }
    if (connection === 'close') {
      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log(
        '🔌 WhatsApp disconnected:',
        lastDisconnect?.error?.message,
        '— reconnecting:', shouldReconnect
      );
      if (shouldReconnect) {
        setTimeout(connectToWhatsApp, 5_000);
      } else {
        console.error('❌ Logged out. Delete auth_info/ and restart to scan a new QR.');
        process.exit(1);
      }
    }
  });

  // ── Incoming messages ─────────────────────────────────────────────────
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      try {
        // Skip old messages replayed on startup
        const msgTimestamp = Number(msg.messageTimestamp) * 1000;
        if (Date.now() - msgTimestamp > 60_000) continue;

        // Skip status broadcasts and group messages
        if (msg.key.remoteJid === 'status@broadcast') continue;
        if (msg.key.remoteJid?.endsWith('@g.us')) continue;

        // Skip our own messages
        if (msg.key.fromMe) continue;

        const jid      = msg.key.remoteJid;
        const pushName = msg.pushName || '';
        const body     =
          msg.message?.conversation ||
          msg.message?.extendedTextMessage?.text ||
          msg.message?.buttonsResponseMessage?.selectedDisplayText ||
          msg.message?.listResponseMessage?.title ||
          '';

        if (!body) continue;

        // Show typing indicator
        await sock.sendPresenceUpdate('composing', jid);

        await dealHandler.handleMessage(jid, body, pushName);

        await sock.sendPresenceUpdate('paused', jid);
      } catch (err) {
        logger.error({ err }, 'Message handler error');
        try {
          await sendText(msg.key.remoteJid, '❌ An error occurred. Please try again or send *help*.');
        } catch {}
      }
    }
  });
}

// ── Entry point ────────────────────────────────────────────────────────────

console.log('🚀 Xcrow WhatsApp Bot starting…');
connectToWhatsApp().catch((err) => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});

process.on('SIGTERM', () => {
  console.log('Received SIGTERM — shutting down gracefully.');
  db.pool.end();
  process.exit(0);
});
