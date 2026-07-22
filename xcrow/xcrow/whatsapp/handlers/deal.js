'use strict';
/**
 * Deal flow — works in both DMs and group chats.
 *
 * In a GROUP:
 *   - State is keyed by groupId, not individual user
 *   - All messages (prompts, payment instructions, QR) go to the group
 *   - Seller starts with "new deal"; anyone can accept as buyer
 *
 * In a DM:
 *   - State is keyed by the user's JID
 *   - Seller creates deal, enters buyer's phone number
 *   - Buyer gets a private invite
 */

const { getState, setState, clearState, updateState } = require('../state');
const { makeQrBuffer } = require('../qr');
const db     = require('../db');
const crypto = require('crypto');

const NETWORKS = {
  '1': { key: 'USDT_BEP20', label: 'USDT BEP20 (BSC)',      symbol: 'USDT' },
  '2': { key: 'USDT_ERC20', label: 'USDT ERC20 (Ethereum)', symbol: 'USDT' },
  '3': { key: 'ETH',        label: 'ETH (Ethereum)',         symbol: 'ETH'  },
  '4': { key: 'BTC',        label: 'BTC (Bitcoin)',          symbol: 'BTC'  },
};

function generateUid() {
  return crypto.randomBytes(4).toString('hex').toUpperCase();
}
function randomOffset() {
  return Math.round((Math.random() * 0.08 + 0.01) * 100) / 100;
}
async function getDepositAddress(network) {
  if (network === 'BTC') return db.getMainWalletBtc();
  return db.getMainWalletBscEth();
}

// ── Send helpers injected from index.js ───────────────────────────────────
let _send;
let _sendImage;
function init(sendFn, sendImageFn) {
  _send      = sendFn;
  _sendImage = sendImageFn;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function networkMenu() {
  return (
    '🌐 *Choose payment network:*\n\n' +
    '1️⃣  USDT BEP20 (BSC) — fast & cheap\n' +
    '2️⃣  USDT ERC20 (Ethereum)\n' +
    '3️⃣  ETH (Ethereum)\n' +
    '4️⃣  BTC (Bitcoin)\n\n' +
    'Reply with the number (1–4)'
  );
}

function shortNum(jid) {
  return jid?.replace('@s.whatsapp.net', '').replace('@g.us', '') || '?';
}

async function sendPaymentInstructions(replyJid, deal, isRetry = false) {
  const symbol = Object.values(NETWORKS).find(n => n.key === deal.crypto)?.symbol || 'USDT';

  const text =
    (isRetry ? '🔄 *Payment Reminder*\n\n' : '💳 *Payment Instructions*\n\n') +
    `Deal ID: \`${deal.deal_uid}\`\n` +
    `📦 ${deal.title}\n\n` +
    `📨 *Send EXACTLY:*\n` +
    `*${deal.total_amount} ${symbol}*\n\n` +
    `🔗 *To this wallet:*\n` +
    `\`${deal.deposit_address}\`\n\n` +
    `🌐 Network: ${deal.crypto}\n\n` +
    `⚠️ The exact amount identifies your deal — don't round it.\n` +
    `Payment detected automatically within ~1 minute.`;

  await _send(replyJid, text);

  try {
    const qrBuf = await makeQrBuffer(deal.deposit_address);
    await _sendImage(replyJid, qrBuf, `📷 Scan to copy wallet address\n${deal.deposit_address}`);
  } catch (e) {
    console.warn('QR send error:', e.message);
  }
}

// ── Main handler ───────────────────────────────────────────────────────────
// senderJid — the person who sent the message
// groupJid  — the group JID if in a group, otherwise null
// replyJid  — where to send responses (group or DM)
// stateKey  — key for conversation state (group or DM)

async function handleMessage(senderJid, text, pushName, groupJid = null) {
  const msg      = (text || '').trim().toLowerCase();
  const replyJid = groupJid || senderJid;
  const stateKey = groupJid || senderJid; // group shares one state
  const state    = getState(stateKey);

  // ── Help / welcome ───────────────────────────────────────────────────────
  if (msg === 'help' || msg === 'hi' || msg === 'hello' || msg === 'start') {
    clearState(stateKey);
    if (groupJid) {
      await _send(replyJid,
        `👋 *Xcrow Escrow Bot*\n\n` +
        `I help this group trade safely with crypto escrow. Everything happens here in the group.\n\n` +
        `*Commands:*\n` +
        `🆕 *new deal* — Start an escrow deal\n` +
        `✅ *accept DEALID* — Accept a deal as buyer\n` +
        `📊 *status DEALID* — Check deal status\n` +
        `✔️ *confirm DEALID* — Confirm you received delivery\n` +
        `❌ *cancel DEALID* — Cancel a deal\n` +
        `❓ *help* — Show this message\n\n` +
        `_Supported: USDT BEP20, USDT ERC20, ETH, BTC_`
      );
    } else {
      await _send(replyJid,
        `👋 *Welcome to Xcrow${pushName ? ', ' + pushName : ''}!*\n\n` +
        `*Commands:*\n` +
        `🆕 *new deal* — Create an escrow deal\n` +
        `📋 *my deals* — View your deals\n` +
        `✅ *confirm DEALID* — Confirm delivery\n` +
        `📊 *status DEALID* — Check status\n` +
        `❌ *cancel DEALID* — Cancel a deal\n` +
        `❓ *help* — Show this menu\n\n` +
        `_Supported: USDT BEP20, USDT ERC20, ETH, BTC_`
      );
    }
    return;
  }

  // ── My deals (DM only) ───────────────────────────────────────────────────
  if (!groupJid && (msg === 'my deals' || msg === 'mydeals' || msg === 'deals')) {
    clearState(stateKey);
    const deals = await db.getDealsByWaId(senderJid);
    if (!deals.length) {
      await _send(replyJid, '📭 You have no deals yet.\n\nSend *new deal* to start one.');
      return;
    }
    let list = '📋 *Your Deals:*\n\n';
    for (const d of deals) {
      const notes = JSON.parse(d.admin_notes || '{}');
      const role  = notes.seller_wa === senderJid ? 'Seller' : 'Buyer';
      list += `• \`${d.deal_uid}\` — ${d.title}\n  Status: *${d.status}* | Role: ${role}\n\n`;
    }
    list += '_Reply_ *status DEALID* _for details._';
    await _send(replyJid, list);
    return;
  }

  // ── Status ───────────────────────────────────────────────────────────────
  if (msg.startsWith('status ')) {
    clearState(stateKey);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(replyJid, '❌ Deal not found. Check the ID and try again.'); return; }
    const symbol = Object.values(NETWORKS).find(n => n.key === deal.crypto)?.symbol || 'USDT';
    await _send(replyJid,
      `📊 *Deal ${deal.deal_uid}*\n\n` +
      `📦 ${deal.title}\n` +
      `💰 Amount: ${deal.amount} ${symbol}\n` +
      `📨 Total: ${deal.total_amount} ${symbol}\n` +
      `🌐 Network: ${deal.crypto}\n` +
      `📍 Status: *${deal.status}*\n` +
      `📅 Created: ${new Date(deal.created_at).toLocaleDateString()}\n\n` +
      (deal.status === 'awaiting_payment'
        ? `⏳ Waiting for payment.\n\nReply *payment ${uid}* to resend instructions.`
        : ['funded', 'in_delivery'].includes(deal.status)
        ? `✅ Payment received! Waiting for buyer to confirm delivery.\n\nBuyer: reply *confirm ${uid}*`
        : deal.status === 'completed'
        ? `🏁 Deal completed! Funds released to seller.`
        : '')
    );
    return;
  }

  // ── Resend payment instructions ──────────────────────────────────────────
  if (msg.startsWith('payment ')) {
    clearState(stateKey);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(replyJid, '❌ Deal not found.'); return; }
    await sendPaymentInstructions(replyJid, deal, true);
    return;
  }

  // ── Accept deal (buyer) ──────────────────────────────────────────────────
  if (msg.startsWith('accept ')) {
    clearState(stateKey);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(replyJid, '❌ Deal not found.'); return; }

    const notes = JSON.parse(deal.admin_notes || '{}');

    if (groupJid) {
      // In a group: the person who types accept becomes the buyer
      // but they can't be the seller
      if (notes.seller_wa === senderJid) {
        await _send(replyJid, `❌ You created this deal — you can't also be the buyer.`);
        return;
      }
      if (deal.status !== 'awaiting_buyer' && deal.status !== 'step5_pending') {
        if (deal.status === 'awaiting_payment') {
          // Already accepted, just resend instructions
          await sendPaymentInstructions(replyJid, deal);
          return;
        }
        await _send(replyJid, `❌ Deal \`${uid}\` is already *${deal.status}*.`);
        return;
      }
      // Set buyer and move to awaiting_payment
      notes.buyer_wa = senderJid;
      await db.updateDeal(deal.id, {
        status:      'awaiting_payment',
        admin_notes: JSON.stringify(notes),
      });
      await _send(replyJid,
        `✅ *${pushName || shortNum(senderJid)} accepted the deal!*\n\n` +
        `Deal \`${uid}\` — *${deal.title}*\n\n` +
        `Payment instructions below 👇`
      );
      await sendPaymentInstructions(replyJid, deal);
      return;
    }

    // DM flow: check they are the invited buyer
    if (notes.buyer_wa !== senderJid) {
      await _send(replyJid, '❌ You are not the buyer for this deal.'); return;
    }
    if (deal.status === 'awaiting_payment') {
      await sendPaymentInstructions(replyJid, deal); return;
    }
    await db.updateDeal(deal.id, { status: 'awaiting_payment' });
    await sendPaymentInstructions(replyJid, deal);
    if (notes.seller_wa) {
      await _send(notes.seller_wa,
        `✅ *Buyer accepted!*\n\nDeal \`${uid}\` — buyer has accepted and received payment instructions.\nYou'll be notified when payment arrives.`
      );
    }
    return;
  }

  // ── Reject deal ──────────────────────────────────────────────────────────
  if (msg.startsWith('reject ')) {
    clearState(stateKey);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(replyJid, '❌ Deal not found.'); return; }
    const notes = JSON.parse(deal.admin_notes || '{}');
    await db.updateDeal(deal.id, { status: 'cancelled' });
    await _send(replyJid, `❌ Deal \`${uid}\` has been rejected and cancelled.`);
    if (!groupJid && notes.seller_wa && notes.seller_wa !== senderJid) {
      await _send(notes.seller_wa, `❌ The buyer rejected deal \`${uid}\`.`);
    }
    return;
  }

  // ── Confirm delivery (buyer) ─────────────────────────────────────────────
  if (msg.startsWith('confirm ')) {
    clearState(stateKey);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(replyJid, '❌ Deal not found.'); return; }
    const notes = JSON.parse(deal.admin_notes || '{}');

    if (notes.buyer_wa !== senderJid) {
      await _send(replyJid, '❌ Only the buyer can confirm delivery.');
      return;
    }
    if (!['funded', 'in_delivery', 'buyer_confirming'].includes(deal.status)) {
      await _send(replyJid, `❌ Deal is *${deal.status}* — can't confirm delivery at this stage.`);
      return;
    }
    await db.updateDeal(deal.id, { status: 'releasing' });
    await _send(replyJid,
      `✅ *Delivery Confirmed!*\n\n` +
      `Deal \`${uid}\` — *${deal.title}*\n\n` +
      `Funds are being released to the seller's wallet now.\n` +
      `_Please allow a few minutes for the transaction._\n\n` +
      `Thank you for using Xcrow! 🎊`
    );
    return;
  }

  // ── Cancel deal ──────────────────────────────────────────────────────────
  if (msg.startsWith('cancel ')) {
    clearState(stateKey);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(replyJid, '❌ Deal not found.'); return; }
    if (!['step5_pending', 'awaiting_buyer', 'awaiting_payment', 'draft'].includes(deal.status)) {
      await _send(replyJid, `❌ Deal is *${deal.status}* — cannot cancel at this stage.`);
      return;
    }
    const notes = JSON.parse(deal.admin_notes || '{}');
    if (notes.seller_wa !== senderJid && notes.buyer_wa !== senderJid) {
      await _send(replyJid, '❌ You are not part of this deal.');
      return;
    }
    await db.updateDeal(deal.id, { status: 'cancelled' });
    await _send(replyJid, `❌ Deal \`${uid}\` has been cancelled.`);
    if (!groupJid) {
      // DM — notify the other party
      const other = notes.seller_wa === senderJid ? notes.buyer_wa : notes.seller_wa;
      if (other) await _send(other, `❌ Deal \`${uid}\` was cancelled by the other party.`).catch(() => {});
    }
    return;
  }

  // ── New deal ─────────────────────────────────────────────────────────────
  if (msg === 'new deal' || msg === 'new' || msg === 'create') {
    if (state?.step && state.step !== 'done') {
      await _send(replyJid,
        `⚠️ A deal is already being created.\n\nReply *cancel* to abort it, or continue from where you left off.`
      );
      return;
    }
    setState(stateKey, { step: 'title', sellerJid: senderJid, pushName, isGroup: !!groupJid });
    await _send(replyJid,
      `🆕 *New Escrow Deal*${groupJid ? ` (started by ${pushName || shortNum(senderJid)})` : ''}\n\n` +
      `Step 1/4 — What are you trading?\n\n` +
      `Enter the deal title (e.g. "iPhone 15 Pro", "Logo Design"):`
    );
    return;
  }

  // ── State machine (deal creation steps) ──────────────────────────────────
  if (!state) {
    await _send(replyJid, `Send *help* to see commands, or *new deal* to start an escrow.`);
    return;
  }

  // In group mode, only the seller who started can answer the deal creation prompts
  if (groupJid && state.sellerJid && state.sellerJid !== senderJid) {
    // Someone else typed something — ignore during deal creation
    return;
  }

  switch (state.step) {

    case 'title': {
      if (text.trim().length < 3) {
        await _send(replyJid, '❌ Title too short — at least 3 characters:');
        return;
      }
      updateState(stateKey, { step: 'amount', title: text.trim() });
      await _send(replyJid,
        `✅ Title: *${text.trim()}*\n\n` +
        `Step 2/4 — What is the deal amount?\n\n` +
        `Enter the amount (numbers only, e.g. 500 or 0.05):`
      );
      break;
    }

    case 'amount': {
      const amount = parseFloat(text.replace(/[^0-9.]/g, ''));
      if (isNaN(amount) || amount <= 0) {
        await _send(replyJid, '❌ Invalid amount. Enter a number (e.g. 500 or 0.05):');
        return;
      }
      updateState(stateKey, { step: 'crypto', amount });
      await _send(replyJid, `✅ Amount: *${amount}*\n\nStep 3/4 — ${networkMenu()}`);
      break;
    }

    case 'crypto': {
      const net = NETWORKS[msg] || NETWORKS[text.trim()];
      if (!net) {
        await _send(replyJid, `❌ Invalid choice. ${networkMenu()}`);
        return;
      }
      updateState(stateKey, { step: 'seller_wallet', network: net });
      await _send(replyJid,
        `✅ Network: *${net.label}*\n\n` +
        `Step 4/4 — Enter your *seller payout wallet address*\n` +
        `_(where you want to receive the funds)_\n\n` +
        `${net.key === 'BTC' ? 'Enter your BTC address (bc1... or 1... or 3...)' : 'Enter your EVM wallet address (0x...)'}`
      );
      break;
    }

    case 'seller_wallet': {
      const addr    = text.trim();
      const validEvm = /^0x[0-9a-fA-F]{40}$/.test(addr);
      const validBtc = /^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$/.test(addr);
      const isBtc   = state.network.key === 'BTC';

      if (isBtc && !validBtc) {
        await _send(replyJid, '❌ Invalid BTC address. Should start with bc1, 1, or 3. Try again:');
        return;
      }
      if (!isBtc && !validEvm) {
        await _send(replyJid, '❌ Invalid wallet address. Should start with 0x and be 42 chars. Try again:');
        return;
      }

      if (groupJid) {
        // GROUP: no buyer phone needed — anyone in group can accept
        const feePercent = await db.getFeePercent();
        const feeAmount  = Math.round(state.amount * feePercent / 100 * 1e6) / 1e6;
        const totalBase  = Math.round((state.amount + feeAmount) * 1e6) / 1e6;
        updateState(stateKey, { step: 'group_confirm', sellerWallet: addr, feePercent, feeAmount, totalBase });
        await _send(replyJid,
          `✅ Seller wallet saved.\n\n` +
          `📋 *Deal Summary*\n\n` +
          `📦 Title: *${state.title}*\n` +
          `💰 Amount: *${state.amount} ${state.network.symbol}*\n` +
          `💸 Platform fee (${feePercent}%): ${feeAmount} ${state.network.symbol}\n` +
          `📨 Buyer pays: *${totalBase} ${state.network.symbol}*\n` +
          `🌐 Network: ${state.network.label}\n\n` +
          `Reply *confirm* to create this deal, or *cancel* to abort.`
        );
      } else {
        // DM: ask for buyer phone number
        updateState(stateKey, { step: 'buyer_number', sellerWallet: addr });
        await _send(replyJid,
          `✅ Seller wallet saved.\n\n` +
          `Step 5/5 — Enter the *buyer's WhatsApp number*\n` +
          `_(include country code, e.g. +2349012345678)_`
        );
      }
      break;
    }

    // ── DM only: collect buyer number ────────────────────────────────────
    case 'buyer_number': {
      const raw   = text.trim().replace(/[\s\-()]/g, '');
      const phone = raw.startsWith('+') ? raw.slice(1) : raw;
      if (!/^\d{7,15}$/.test(phone)) {
        await _send(replyJid, '❌ Invalid phone number. Include country code, e.g. +2349012345678:');
        return;
      }
      const buyerWaId  = `${phone}@s.whatsapp.net`;
      const feePercent = await db.getFeePercent();
      const feeAmount  = Math.round(state.amount * feePercent / 100 * 1e6) / 1e6;
      const totalBase  = Math.round((state.amount + feeAmount) * 1e6) / 1e6;
      updateState(stateKey, { step: 'confirm', buyerWaId, feePercent, feeAmount, totalBase });
      await _send(replyJid,
        `📋 *Deal Summary*\n\n` +
        `📦 Title: *${state.title}*\n` +
        `💰 Amount: *${state.amount} ${state.network.symbol}*\n` +
        `💸 Fee (${feePercent}%): ${feeAmount} ${state.network.symbol}\n` +
        `📨 Total buyer pays: *${totalBase} ${state.network.symbol}*\n` +
        `🌐 Network: ${state.network.label}\n` +
        `💳 Seller wallet: \`${state.sellerWallet}\`\n\n` +
        `Reply *confirm* to create the deal or *cancel* to abort.`
      );
      break;
    }

    // ── Confirm step (DM) ────────────────────────────────────────────────
    case 'confirm': {
      if (msg === 'cancel' || msg === 'no') {
        clearState(stateKey);
        await _send(replyJid, '❌ Deal cancelled. Send *new deal* to start again.');
        return;
      }
      if (msg !== 'confirm' && msg !== 'yes') {
        await _send(replyJid, `Reply *confirm* to create the deal or *cancel* to abort.`);
        return;
      }
      await createDeal({ stateKey, state, replyJid, senderJid, groupJid });
      break;
    }

    // ── Confirm step (Group) ─────────────────────────────────────────────
    case 'group_confirm': {
      if (msg === 'cancel' || msg === 'no') {
        clearState(stateKey);
        await _send(replyJid, '❌ Deal cancelled. Send *new deal* to start again.');
        return;
      }
      if (msg !== 'confirm' && msg !== 'yes') {
        await _send(replyJid, `Reply *confirm* to create the deal or *cancel* to abort.`);
        return;
      }
      await createDeal({ stateKey, state, replyJid, senderJid, groupJid });
      break;
    }

    default:
      clearState(stateKey);
      await _send(replyJid, `Send *help* to see available commands.`);
  }
}

// ── Create deal (shared by group + DM confirm steps) ──────────────────────

async function createDeal({ stateKey, state, replyJid, senderJid, groupJid }) {
  const offset      = randomOffset();
  const totalAmount = Math.round((state.totalBase + offset) * 1e6) / 1e6;
  const dealUid     = generateUid();
  const depositAddr = await getDepositAddress(state.network.key);

  if (!depositAddr) {
    await _send(replyJid, '❌ Escrow wallet not configured. Contact support.');
    clearState(stateKey);
    return;
  }

  const deal = await db.createDeal({
    dealUid,
    creatorWaId:    senderJid,
    title:          state.title,
    amount:         state.amount,
    crypto:         state.network.key,
    feePercent:     state.feePercent,
    feeAmount:      state.feeAmount,
    totalAmount,
    depositAddress: depositAddr,
    sellerWallet:   state.sellerWallet,
    sellerWaId:     senderJid,
    buyerWaId:      groupJid ? null : (state.buyerWaId || null),
    groupJid:       groupJid || null,
    status:         groupJid ? 'awaiting_buyer' : 'step5_pending',
  });

  clearState(stateKey);

  if (groupJid) {
    // Group deal — post instructions in the group, anyone can accept
    await _send(replyJid,
      `✅ *Escrow Deal Created!*\n\n` +
      `📦 *${state.title}*\n` +
      `💰 Amount: ${state.amount} ${state.network.symbol}\n` +
      `💸 Platform fee: ${state.feeAmount} ${state.network.symbol}\n` +
      `📨 *Buyer pays: ${totalAmount} ${state.network.symbol}*\n` +
      `🌐 Network: ${state.network.label}\n` +
      `🆔 Deal ID: \`${dealUid}\`\n\n` +
      `👥 *Anyone in this group can be the buyer.*\n` +
      `Reply *accept ${dealUid}* to accept and see payment instructions.\n\n` +
      `_Seller will be notified when payment is confirmed._`
    );
  } else {
    // DM deal — notify seller and send invite to buyer
    await _send(replyJid,
      `✅ *Deal Created!*\n\n` +
      `Deal ID: \`${dealUid}\`\n\n` +
      `I've messaged the buyer with an invite.\n` +
      `You'll be notified when payment is received.\n\n` +
      `_Check status: status ${dealUid}_`
    );
    // Invite the buyer
    await _send(state.buyerWaId,
      `🔐 *Xcrow Escrow Invitation*\n\n` +
      `You've been invited to an escrow deal!\n\n` +
      `📦 *${state.title}*\n` +
      `💰 Amount: ${state.amount} ${state.network.symbol}\n` +
      `💸 Fee: ${state.feeAmount} ${state.network.symbol}\n` +
      `📨 *You pay: ${totalAmount} ${state.network.symbol}*\n` +
      `🌐 Network: ${state.network.label}\n\n` +
      `Deal ID: \`${dealUid}\`\n\n` +
      `Reply *accept ${dealUid}* to see payment instructions, or *reject ${dealUid}* to decline.`
    ).catch(() => {});
  }
}

module.exports = { init, handleMessage, sendPaymentInstructions };
