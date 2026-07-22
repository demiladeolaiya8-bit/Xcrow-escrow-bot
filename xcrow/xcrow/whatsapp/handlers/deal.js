/**
 * Deal creation & management flow for WhatsApp.
 *
 * Conversation states:
 *   idle → title → amount → crypto → seller_wallet → buyer_number → confirm → done
 *
 * Buyer flow (when invited):
 *   invited → accepted → waiting_payment → confirming_delivery → done
 */
const { getState, setState, clearState, updateState } = require('../state');
const { makeQrBuffer } = require('../qr');
const db = require('../db');
const crypto = require('crypto');

const NETWORKS = {
  '1': { key: 'USDT_BEP20',  label: 'USDT BEP20 (BSC)',        symbol: 'USDT' },
  '2': { key: 'USDT_ERC20',  label: 'USDT ERC20 (Ethereum)',   symbol: 'USDT' },
  '3': { key: 'ETH',          label: 'ETH (Ethereum)',           symbol: 'ETH'  },
  '4': { key: 'BTC',          label: 'BTC (Bitcoin)',            symbol: 'BTC'  },
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

// ── Outbound message helper (injected from index.js) ──────────────────────
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

function dealSummary(state, totalAmount) {
  return (
    `📋 *Deal Summary*\n\n` +
    `📦 Title:   ${state.title}\n` +
    `💰 Amount:  ${state.amount} ${state.network.symbol}\n` +
    `💸 Fee (${state.feePercent}%): ${state.feeAmount} ${state.network.symbol}\n` +
    `📨 Total buyer pays: *${totalAmount} ${state.network.symbol}*\n` +
    `🌐 Network: ${state.network.label}\n` +
    `💳 Seller wallet: \`${state.sellerWallet}\`\n\n` +
    `Reply *confirm* to create the deal, or *cancel* to abort.`
  );
}

async function sendPaymentInstructions(waId, deal, isRetry = false) {
  const notes  = JSON.parse(deal.admin_notes || '{}');
  const symbol = NETWORKS[Object.keys(NETWORKS).find(k => NETWORKS[k].key === deal.crypto)]?.symbol || 'USDT';
  const total  = deal.total_amount;
  const addr   = deal.deposit_address;

  const text =
    (isRetry ? '🔄 *Payment reminder*\n\n' : '💳 *Payment Instructions*\n\n') +
    `Deal ID: \`${deal.deal_uid}\`\n` +
    `📦 ${deal.title}\n\n` +
    `📨 *Send EXACTLY this amount:*\n` +
    `*${total} ${symbol}*\n\n` +
    `🔗 *To this wallet:*\n` +
    `\`${addr}\`\n\n` +
    `🌐 Network: ${deal.crypto}\n\n` +
    `⚠️ The exact amount matters — it identifies your deal.\n` +
    `Payment is detected automatically within ~1 minute.`;

  await _send(waId, text);

  try {
    const qrBuf = await makeQrBuffer(addr);
    await _sendImage(waId, qrBuf, `📷 Scan to copy wallet address\n${addr}`);
  } catch (e) {
    console.warn('QR send error:', e.message);
  }
}

// ── Main message handler ───────────────────────────────────────────────────

async function handleMessage(waId, text, pushName) {
  const msg  = (text || '').trim().toLowerCase();
  const state = getState(waId);

  // ── Global commands (always available) ────────────────────────────────
  if (msg === 'help' || msg === 'hi' || msg === 'hello' || msg === 'start') {
    clearState(waId);
    await _send(waId,
      `👋 *Welcome to Xcrow Escrow${pushName ? ', ' + pushName : ''}!*\n\n` +
      `I help you trade safely using crypto escrow.\n\n` +
      `*Commands:*\n` +
      `🆕 *new deal* — Create an escrow deal\n` +
      `📋 *my deals* — View your active deals\n` +
      `✅ *confirm DEALID* — Confirm you received delivery\n` +
      `📊 *status DEALID* — Check deal status\n` +
      `❌ *cancel DEALID* — Cancel a deal\n` +
      `❓ *help* — Show this menu\n\n` +
      `_Supported networks: USDT BEP20, USDT ERC20, ETH, BTC_`
    );
    return;
  }

  if (msg === 'my deals' || msg === 'mydeals' || msg === 'deals') {
    clearState(waId);
    const deals = await db.getDealsByWaId(waId);
    if (!deals.length) {
      await _send(waId, '📭 You have no deals yet.\n\nSend *new deal* to start one.');
      return;
    }
    let list = '📋 *Your Deals:*\n\n';
    for (const d of deals) {
      const notes = JSON.parse(d.admin_notes || '{}');
      const role  = notes.seller_wa === waId ? 'Seller' : 'Buyer';
      list += `• \`${d.deal_uid}\` — ${d.title}\n  Status: *${d.status}* | Role: ${role}\n\n`;
    }
    list += '_Reply_ *status DEALID* _for details._';
    await _send(waId, list);
    return;
  }

  if (msg.startsWith('status ')) {
    clearState(waId);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) {
      await _send(waId, '❌ Deal not found. Check the ID and try again.');
      return;
    }
    const notes  = JSON.parse(deal.admin_notes || '{}');
    const symbol = NETWORKS[Object.keys(NETWORKS).find(k => NETWORKS[k].key === deal.crypto)]?.symbol || 'USDT';
    await _send(waId,
      `📊 *Deal ${deal.deal_uid}*\n\n` +
      `📦 ${deal.title}\n` +
      `💰 Amount: ${deal.amount} ${symbol}\n` +
      `📨 Total: ${deal.total_amount} ${symbol}\n` +
      `🌐 Network: ${deal.crypto}\n` +
      `📍 Status: *${deal.status}*\n` +
      `📅 Created: ${new Date(deal.created_at).toLocaleDateString()}\n\n` +
      (deal.status === 'step5_pending' || deal.status === 'awaiting_payment'
        ? `⏳ Waiting for payment to your escrow wallet.\n\nSend *payment ${uid}* to see instructions again.`
        : deal.status === 'funded' || deal.status === 'in_delivery'
        ? `✅ Payment received! Waiting for delivery confirmation.\n\nBuyer: reply *confirm ${uid}* when you receive the item.`
        : deal.status === 'completed'
        ? `🏁 Deal completed! Funds released to seller.`
        : '')
    );
    return;
  }

  if (msg.startsWith('payment ')) {
    clearState(waId);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(waId, '❌ Deal not found.'); return; }
    await sendPaymentInstructions(waId, deal, true);
    return;
  }

  if (msg.startsWith('confirm ')) {
    clearState(waId);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(waId, '❌ Deal not found.'); return; }
    const notes = JSON.parse(deal.admin_notes || '{}');
    if (notes.buyer_wa !== waId) {
      await _send(waId, '❌ Only the buyer can confirm delivery.');
      return;
    }
    if (!['funded', 'in_delivery', 'buyer_confirming'].includes(deal.status)) {
      await _send(waId, `❌ Deal is *${deal.status}* — can't confirm delivery at this stage.`);
      return;
    }
    await db.updateDeal(deal.id, { status: 'releasing' });
    await _send(waId,
      `✅ *Delivery confirmed!*\n\n` +
      `Deal \`${uid}\` — funds are being released to the seller now.\n\n` +
      `Thank you for using Xcrow!`
    );
    // Notify seller
    if (notes.seller_wa) {
      await _send(notes.seller_wa,
        `🎉 *Buyer confirmed delivery!*\n\n` +
        `Deal \`${uid}\`\n` +
        `Funds are being released to your wallet now.\n\n` +
        `_Please allow a few minutes for the transaction._`
      );
    }
    // Trigger release via DB status — Python bot's auto-release picks this up
    return;
  }

  if (msg.startsWith('cancel ')) {
    clearState(waId);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(waId, '❌ Deal not found.'); return; }
    if (!['step5_pending', 'awaiting_payment', 'draft'].includes(deal.status)) {
      await _send(waId, `❌ Deal is *${deal.status}* — cannot cancel at this stage.\n\nContact support if needed.`);
      return;
    }
    const notes = JSON.parse(deal.admin_notes || '{}');
    if (notes.seller_wa !== waId && notes.buyer_wa !== waId) {
      await _send(waId, '❌ You are not part of this deal.');
      return;
    }
    await db.updateDeal(deal.id, { status: 'cancelled' });
    await _send(waId, `❌ Deal \`${uid}\` has been cancelled.`);
    if (notes.seller_wa && notes.seller_wa !== waId) {
      await _send(notes.seller_wa, `❌ Deal \`${uid}\` was cancelled by the other party.`);
    }
    if (notes.buyer_wa && notes.buyer_wa !== waId) {
      await _send(notes.buyer_wa, `❌ Deal \`${uid}\` was cancelled by the other party.`);
    }
    return;
  }

  // ── New deal flow ────────────────────────────────────────────────────────
  if (msg === 'new deal' || msg === 'new' || msg === 'create') {
    setState(waId, { step: 'title', pushName });
    await _send(waId,
      `🆕 *Create New Escrow Deal*\n\n` +
      `Step 1/5 — What are you trading?\n\n` +
      `Enter the deal title (e.g. "iPhone 15 Pro", "Freelance Logo Design"):`
    );
    return;
  }

  // ── State machine ────────────────────────────────────────────────────────
  if (!state) {
    await _send(waId,
      `👋 Send *help* to see available commands, or *new deal* to start an escrow.`
    );
    return;
  }

  switch (state.step) {

    case 'title': {
      if (text.length < 3) {
        await _send(waId, '❌ Title too short. Please enter at least 3 characters:');
        return;
      }
      updateState(waId, { step: 'amount', title: text });
      await _send(waId,
        `✅ Title: *${text}*\n\n` +
        `Step 2/5 — What is the deal amount?\n\n` +
        `Enter the amount in USDT/ETH/BTC (numbers only, e.g. 500):`
      );
      break;
    }

    case 'amount': {
      const amount = parseFloat(text.replace(/[^0-9.]/g, ''));
      if (isNaN(amount) || amount <= 0) {
        await _send(waId, '❌ Invalid amount. Enter a number (e.g. 500 or 0.05):');
        return;
      }
      updateState(waId, { step: 'crypto', amount });
      await _send(waId, `✅ Amount: *${amount}*\n\nStep 3/5 — ${networkMenu()}`);
      break;
    }

    case 'crypto': {
      const net = NETWORKS[msg] || NETWORKS[text.trim()];
      if (!net) {
        await _send(waId, `❌ Invalid choice. ${networkMenu()}`);
        return;
      }
      updateState(waId, { step: 'seller_wallet', network: net });
      await _send(waId,
        `✅ Network: *${net.label}*\n\n` +
        `Step 4/5 — Enter your *payout wallet address*\n` +
        `_(where you want to receive payment as the seller)_\n\n` +
        `${net.key === 'BTC' ? 'Enter your BTC address (bc1... or 1... or 3...)' : 'Enter your EVM wallet address (0x...)'}`
      );
      break;
    }

    case 'seller_wallet': {
      const addr = text.trim();
      const validEvm = /^0x[0-9a-fA-F]{40}$/.test(addr);
      const validBtc = /^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$/.test(addr);
      const isBtcNet = state.network.key === 'BTC';

      if (isBtcNet && !validBtc) {
        await _send(waId, '❌ Invalid BTC address. It should start with bc1, 1, or 3. Try again:');
        return;
      }
      if (!isBtcNet && !validEvm) {
        await _send(waId, '❌ Invalid wallet address. It should start with 0x and be 42 characters. Try again:');
        return;
      }
      updateState(waId, { step: 'buyer_number', sellerWallet: addr });
      await _send(waId,
        `✅ Seller wallet saved.\n\n` +
        `Step 5/5 — Enter the *buyer's WhatsApp number*\n` +
        `_(include country code, e.g. +2349012345678)_`
      );
      break;
    }

    case 'buyer_number': {
      const raw   = text.trim().replace(/[\s\-()]/g, '');
      const phone = raw.startsWith('+') ? raw.slice(1) : raw;
      if (!/^\d{7,15}$/.test(phone)) {
        await _send(waId, '❌ Invalid phone number. Include country code, e.g. +2349012345678:');
        return;
      }
      const buyerWaId = `${phone}@s.whatsapp.net`;
      const feePercent = await db.getFeePercent();
      const feeAmount  = Math.round(state.amount * feePercent / 100 * 1e6) / 1e6;
      const totalBase  = Math.round((state.amount + feeAmount) * 1e6) / 1e6;

      updateState(waId, { step: 'confirm', buyerWaId, feePercent, feeAmount, totalBase });
      await _send(waId, dealSummary(state, totalBase));
      break;
    }

    case 'confirm': {
      if (msg !== 'confirm' && msg !== 'yes') {
        if (msg === 'cancel' || msg === 'no') {
          clearState(waId);
          await _send(waId, '❌ Deal cancelled. Send *new deal* to start again.');
        } else {
          await _send(waId, `Reply *confirm* to create the deal or *cancel* to abort.`);
        }
        return;
      }

      // Build unique amount
      const offset     = randomOffset();
      const totalAmount = Math.round((state.totalBase + offset) * 1e6) / 1e6;
      const dealUid    = generateUid();
      const depositAddr = await getDepositAddress(state.network.key);

      if (!depositAddr) {
        await _send(waId, '❌ Escrow wallet not configured. Contact support.');
        clearState(waId);
        return;
      }

      const deal = await db.createDeal({
        dealUid,
        creatorWaId: waId,
        title:       state.title,
        amount:      state.amount,
        crypto:      state.network.key,
        feePercent:  state.feePercent,
        feeAmount:   state.feeAmount,
        totalAmount,
        depositAddress: depositAddr,
        sellerWallet:   state.sellerWallet,
        sellerWaId:  waId,
        buyerWaId:   state.buyerWaId,
      });

      clearState(waId);

      await _send(waId,
        `✅ *Deal Created!*\n\n` +
        `Deal ID: \`${dealUid}\`\n\n` +
        `I've messaged the buyer with payment instructions.\n` +
        `You'll be notified when payment is received.\n\n` +
        `_Check status anytime: status ${dealUid}_`
      );

      // Message the buyer
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
      );
      break;
    }

    default:
      clearState(waId);
      await _send(waId, `Send *help* to see available commands.`);
  }

  // ── Accept / reject deal invite ──────────────────────────────────────────
  if (msg.startsWith('accept ')) {
    clearState(waId);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(waId, '❌ Deal not found.'); return; }
    const notes = JSON.parse(deal.admin_notes || '{}');
    if (notes.buyer_wa !== waId) { await _send(waId, '❌ You are not the buyer for this deal.'); return; }
    await db.updateDeal(deal.id, { status: 'awaiting_payment' });
    await sendPaymentInstructions(waId, deal);
    const sellerWa = notes.seller_wa;
    if (sellerWa) {
      await _send(sellerWa,
        `✅ *Buyer accepted!*\n\nDeal \`${uid}\` — buyer has accepted and received payment instructions.\n\nYou'll be notified when payment arrives.`
      );
    }
    return;
  }

  if (msg.startsWith('reject ')) {
    clearState(waId);
    const uid  = msg.split(' ')[1]?.toUpperCase();
    const deal = uid ? await db.getDealByUid(uid) : null;
    if (!deal) { await _send(waId, '❌ Deal not found.'); return; }
    const notes = JSON.parse(deal.admin_notes || '{}');
    await db.updateDeal(deal.id, { status: 'cancelled' });
    await _send(waId, `❌ Deal \`${uid}\` rejected.`);
    if (notes.seller_wa) {
      await _send(notes.seller_wa, `❌ The buyer rejected deal \`${uid}\`.`);
    }
    return;
  }
}

module.exports = { init, handleMessage, sendPaymentInstructions };
