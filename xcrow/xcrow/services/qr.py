"""Generate QR code PNG bytes for a crypto deposit address."""
from __future__ import annotations
import io
import qrcode


def generate_qr_bytes(data: str) -> bytes:
    """Return PNG bytes of a QR code for the given data string."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
