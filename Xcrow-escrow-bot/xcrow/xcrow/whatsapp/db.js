/**
 * PostgreSQL connection — shares the same DB as the Telegram bot.
 * All deal/transaction data is unified.
 */
const { Pool } = require('pg');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL_SYNC ||
    process.env.DATABASE_URL?.replace('postgresql+asyncpg://', 'postgresql://') ||
    'postgresql://xcrow:xcrowpass@postgres:5432/xcrow',
});

pool.on('error', (err) => {
  console.error('DB pool error:', err.message);
});

// ── Deals ──────────────────────────────────────────────────────────────────

async function createDeal({ dealUid, creatorWaId, title, amount, crypto, feePercent, feeAmount, totalAmount, depositAddress, sellerWallet, sellerWaId, buyerWaId }) {
  const res = await pool.query(`
    INSERT INTO deals
      (deal_uid, creator_id, title, amount, crypto,
       fee_percent, fee_amount, total_amount, deposit_address,
       seller_wallet, status,
       admin_notes)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'step5_pending', $11)
    RETURNING id, deal_uid
  `, [
    dealUid, 0, title, amount, crypto,
    feePercent, feeAmount, totalAmount, depositAddress,
    sellerWallet,
    JSON.stringify({ seller_wa: sellerWaId, buyer_wa: buyerWaId, platform: 'whatsapp' }),
  ]);
  return res.rows[0];
}

async function getDealByUid(uid) {
  const res = await pool.query(
    `SELECT * FROM deals WHERE deal_uid = $1`, [uid.toUpperCase()]
  );
  return res.rows[0] || null;
}

async function getDealsByWaId(waId) {
  // Deals where seller_wa or buyer_wa matches in admin_notes JSON
  const res = await pool.query(`
    SELECT * FROM deals
    WHERE admin_notes::jsonb ->> 'seller_wa' = $1
       OR admin_notes::jsonb ->> 'buyer_wa'  = $1
    ORDER BY created_at DESC LIMIT 10
  `, [waId]);
  return res.rows;
}

async function updateDeal(id, fields) {
  const keys = Object.keys(fields);
  const vals = Object.values(fields);
  const sets = keys.map((k, i) => `${k} = $${i + 2}`).join(', ');
  await pool.query(`UPDATE deals SET ${sets} WHERE id = $1`, [id, ...vals]);
}

async function findDealByAmount(amount, network, tolerance = 0.005) {
  const res = await pool.query(`
    SELECT * FROM deals
    WHERE status IN ('step5_pending', 'awaiting_payment')
      AND crypto = $1
      AND total_amount BETWEEN $2 AND $3
    ORDER BY created_at ASC LIMIT 1
  `, [network, amount - tolerance, amount + tolerance]);
  return res.rows[0] || null;
}

async function getNewlyFundedWaDeals() {
  // Deals that became funded and are WhatsApp deals (have admin_notes with wa keys)
  const res = await pool.query(`
    SELECT * FROM deals
    WHERE status IN ('funded', 'in_delivery', 'buyer_confirming')
      AND admin_notes IS NOT NULL
      AND admin_notes::jsonb ? 'seller_wa'
      AND (admin_notes::jsonb ->> 'wa_funded_notified') IS NULL
    ORDER BY funded_at ASC
  `);
  return res.rows;
}

async function markWaNotified(id, key) {
  const res = await pool.query(`SELECT admin_notes FROM deals WHERE id = $1`, [id]);
  let notes = {};
  try { notes = JSON.parse(res.rows[0]?.admin_notes || '{}'); } catch {}
  notes[key] = true;
  await pool.query(`UPDATE deals SET admin_notes = $1 WHERE id = $2`, [JSON.stringify(notes), id]);
}

async function getCompletedWaDeals() {
  const res = await pool.query(`
    SELECT * FROM deals
    WHERE status IN ('completed', 'released')
      AND admin_notes IS NOT NULL
      AND admin_notes::jsonb ? 'seller_wa'
      AND (admin_notes::jsonb ->> 'wa_completed_notified') IS NULL
    ORDER BY released_at ASC
  `);
  return res.rows;
}

// ── Settings ───────────────────────────────────────────────────────────────

async function getSetting(key, def = '') {
  const res = await pool.query(`SELECT value FROM platform_settings WHERE key = $1`, [key]);
  return res.rows[0]?.value ?? def;
}

async function getFeePercent() {
  const val = await getSetting('fee_percent', '1.0');
  return parseFloat(val) || 1.0;
}

async function getMainWalletBscEth() {
  return getSetting('main_wallet_bsc_eth', process.env.MAIN_WALLET_BSC_ETH || '');
}

async function getMainWalletBtc() {
  return getSetting('main_wallet_btc', process.env.MAIN_WALLET_BTC || '');
}

// ── Transactions ───────────────────────────────────────────────────────────

async function createTransaction({ dealId, txHash, amount, crypto, fromAddr, confirmations }) {
  await pool.query(`
    INSERT INTO transactions (deal_id, tx_hash, amount, crypto, from_addr, confirmations, confirmed)
    VALUES ($1, $2, $3, $4, $5, $6, true)
    ON CONFLICT DO NOTHING
  `, [dealId, txHash, amount, crypto, fromAddr || null, confirmations || 0]);
}

module.exports = {
  pool,
  createDeal, getDealByUid, getDealsByWaId, updateDeal,
  findDealByAmount, getNewlyFundedWaDeals, markWaNotified,
  getCompletedWaDeals, getSetting, getFeePercent,
  getMainWalletBscEth, getMainWalletBtc, createTransaction,
};
