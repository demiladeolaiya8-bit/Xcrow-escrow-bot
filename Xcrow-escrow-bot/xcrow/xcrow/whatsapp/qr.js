/**
 * Generate QR code for a crypto wallet address — returns a buffer
 * that can be sent as a WhatsApp image message.
 */
const QRCode = require('qrcode');

async function makeQrBuffer(address) {
  const buf = await QRCode.toBuffer(address, {
    errorCorrectionLevel: 'M',
    type: 'png',
    margin: 2,
    scale: 8,
    color: { dark: '#000000', light: '#FFFFFF' },
  });
  return buf;
}

module.exports = { makeQrBuffer };
