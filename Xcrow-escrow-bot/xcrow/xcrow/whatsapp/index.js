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
    image:    imageBuffer,
    caption:  caption,
    mimetype: 'image/png',
  });
}

dealHandler.init(sendText, sendImage);

// ── Payment-confirmed poller ───────────────────────────────────────────────
async function pollFundedDeals() {
  try {
    const deals = await db.getNewlyFundedWaDeals();
    for (const deal of deals) {
      const notes = JSON.parse(deal.admin_notes || '{}');
      const groupJid  = notes.group_jid;
      const sellerWa  = notes.seller_wa;
      const buyerWa   = notes.buyer_wa;

      const fundedMsg =
        `🎉 *Payment Confirmed!*\n\n` +
        `Deal \`${deal.deal_uid}\` — *${deal.title}*\n\n` +
        `✅ Payment is safely held in escrow!\n\n` +
        `@${(buyerWa || '').replace('@s.whatsapp.net', '')} — when you receive the item, reply:\n` +
        `*confirm ${deal.deal_uid}*`;

      if (groupJid) {
        // Group deal — notify in the group
        const mentions = [sellerWa, buyerWa].filter(Boolean);
        await sock.sendMessage(groupJid, { text: fundedMsg, mentions }).catch(() => {});
      } else {
        // DM deal — notify both parties individually
        if (sellerWa) await sendText(sellerWa,
          `🎉 *Payment Received!*\n\nDeal \`${deal.deal_uid}\` — ${deal.title}\n\n` +
          `✅ Payment confirmed in escrow! Deliver the item to the buyer.\n` +
          `Funds release when buyer confirms delivery.`
        ).catch(() => {});
        if (buyerWa) await sendText(buyerWa,
          `✅ *Payment Confirmed!*\n\nDeal \`${deal.deal_uid}\` — ${deal.title}\n\n` +
          `Payment is safely held in escrow.\n\nReply *confirm ${deal.deal_uid}* after you receive the item.`
        ).catch(() => {});
      }

      await db.markWaNotified(deal.id, 'wa_funded_notified');
    }

    const completed = await db.getCompletedWaDeals();
    for (const deal of completed) {
      const notes    = JSON.parse(deal.admin_notes || '{}');
      const groupJid = notes.group_jid;

      if (groupJid) {
        await sendText(groupJid,
          `💸 *Deal Complete!*\n\nDeal \`${deal.deal_uid}\` — *${deal.title}*\n\n` +
          `Funds released to seller's wallet. Thank you for using Xcrow! 🎊`
        ).catch(() => {});
      } else {
        if (notes.seller_wa) await sendText(notes.seller_wa,
          `💸 *Funds Released!*\n\nDeal \`${deal.deal_uid}\` completed.\nPayment sent to your wallet. Thank you for using Xcrow!`
        ).catch(() => {});
        if (notes.buyer_wa) await sendText(notes.buyer_wa,
          `🏁 *Deal Complete!*\n\nDeal \`${deal.deal_uid}\` — funds released to seller. Thank you for using Xcrow!`
        ).catch(() => {});
      }
      await db.markWaNotified(deal.id, 'wa_completed_notified');
    }
  } catch (err) {
    logger.error({ err }, 'Poll funded deals error');
  }
}

// ── Main connection ────────────────────────────────────────────────────────

let pollInterval   = null;
let reconnectDelay = 5_000;

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  let version;
  try {
    ({ version } = await fetchLatestBaileysVersion());
  } catch {
    version = [2, 3000, 1015901307];
    console.warn('⚠️  Could not fetch latest Baileys version — using fallback');
  }

  sock = makeWASocket({
    version,
    logger,
    auth:                  state,
    browser:               ['Xcrow Escrow Bot', 'Chrome', '1.0.0'],
    markOnlineOnConnect:   false,
    generateHighQualityLinkPreview: false,
    syncFullHistory:       false,
    connectTimeoutMs:      180_000,
    keepAliveIntervalMs:   10_000,
    retryRequestDelayMs:   2_000,
    defaultQueryTimeoutMs: undefined,
  });

  sock.ev.on('creds.update', saveCreds);

  // ── Connection state ──────────────────────────────────────────────────
  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log('\n📱 Scan this QR code with WhatsApp Business (Settings → Linked Devices → Link a Device):\n');
      qrTerminal.generate(qr, { small: true });
    }
    if (connection === 'open') {
      console.log('✅ WhatsApp connected! Bot is live.');
      reconnectDelay = 5_000;
      if (!pollInterval) {
        pollFundedDeals();
        pollInterval = setInterval(pollFundedDeals, 30_000);
      }
    }
    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut  = statusCode === DisconnectReason.loggedOut;
      console.log(`🔌 Disconnected (code ${statusCode}): ${lastDisconnect?.error?.message}`);
      if (loggedOut) {
        console.error('❌ Logged out. Delete auth_info/ and restart to scan a new QR.');
        process.exit(1);
      }
      console.log(`↩️  Reconnecting in ${reconnectDelay / 1000}s…`);
      setTimeout(connectToWhatsApp, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 60_000);
    }
  });

  // ── Bot added to a group ──────────────────────────────────────────────
  sock.ev.on('group-participants.update', async ({ id, participants, action }) => {
    if (action !== 'add') return;
    // Check if one of the added participants is us
    const botJid = sock.user?.id;
    const botNum = botJid?.split(':')[0] + '@s.whatsapp.net';
    const weWereAdded = participants.some(p => p === botJid || p === botNum);
    if (!weWereAdded) return;

    try {
      await sendText(id,
        `👋 *Hi! I'm Xcrow Escrow Bot.*\n\n` +
        `I help this group trade safely using crypto escrow. All deal activity happens right here in the group.\n\n` +
        `*How to start a deal:*\n` +
        `Anyone can type *new deal* to begin.\n\n` +
        `*Commands:*\n` +
        `🆕 *new deal* — Start an escrow deal\n` +
        `✅ *accept DEALID* — Accept a deal as buyer\n` +
        `📊 *status DEALID* — Check deal status\n` +
        `✔️ *confirm DEALID* — Confirm you received delivery\n` +
        `❌ *cancel DEALID* — Cancel a pending deal\n` +
        `❓ *help* — Show this message\n\n` +
        `_Supported: USDT BEP20, USDT ERC20, ETH, BTC_`
      );
    } catch (e) {
      logger.warn({ e }, 'Could not send group welcome');
    }
  });

  // ── Incoming messages ─────────────────────────────────────────────────
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      try {
        const msgTimestamp = Number(msg.messageTimestamp) * 1000;
        if (Date.now() - msgTimestamp > 60_000) continue;

        if (msg.key.remoteJid === 'status@broadcast') continue;
        if (msg.key.fromMe) continue;

        const isGroup  = msg.key.remoteJid?.endsWith('@g.us');
        const groupJid = isGroup ? msg.key.remoteJid : null;
        // In groups, sender is in participant; in DMs it's remoteJid
        const senderJid = isGroup
          ? (msg.key.participant || msg.participant)
          : msg.key.remoteJid;

        const body =
          msg.message?.conversation ||
          msg.message?.extendedTextMessage?.text ||
          msg.message?.buttonsResponseMessage?.selectedDisplayText ||
          msg.message?.listResponseMessage?.title ||
          '';

        if (!body) continue;

        // Only respond in groups if the message contains a bot keyword,
        // so we don't react to every single group message
        if (isGroup && !isBotCommand(body)) continue;

        const replyJid = groupJid || senderJid;
        await sock.sendPresenceUpdate('composing', replyJid);
        await dealHandler.handleMessage(senderJid, body, msg.pushName || '', groupJid);
        await sock.sendPresenceUpdate('paused', replyJid);

      } catch (err) {
        logger.error({ err }, 'Message handler error');
        try {
          const replyTo = msg.key.remoteJid;
          await sendText(replyTo, '❌ An error occurred. Please try again or send *help*.');
        } catch {}
      }
    }
  });
}

// ── Commands that should trigger the bot in a group ────────────────────────
const BOT_KEYWORDS = [
  'new deal', 'new', 'create', 'help', 'hi', 'hello', 'start',
  'my deals', 'mydeals', 'deals', 'accept ', 'reject ', 'confirm ',
  'cancel ', 'status ', 'payment ',
];
function isBotCommand(text) {
  const t = text.trim().toLowerCase();
  return BOT_KEYWORDS.some(k => t === k || t.startsWith(k));
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
