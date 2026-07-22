/**
 * In-memory conversation state per WhatsApp user.
 * Tracks which step of the deal flow each user is in.
 */
const NodeCache = require('node-cache');

// State expires after 30 minutes of inactivity
const cache = new NodeCache({ stdTTL: 1800, checkperiod: 60 });

function getState(waId) {
  return cache.get(waId) || null;
}

function setState(waId, state) {
  cache.set(waId, state);
}

function clearState(waId) {
  cache.del(waId);
}

function updateState(waId, patch) {
  const current = getState(waId) || {};
  cache.set(waId, { ...current, ...patch });
}

module.exports = { getState, setState, clearState, updateState };
