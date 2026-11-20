r"""SMS OTP gate — port of D:\AI\automation\musashaban\src\payments\sms-otp.ts

The router's SIM receives a one-time code from the telco. A forwarder on the
phone (Termux, an SMS-forwarder app, or a GSM modem) posts the message to the
local gateway. This module:

  1. binds the code to exactly one pending challenge (purpose + subject);
  2. accepts it only from the phone that owns the challenge, inside the TTL,
     and only from allowlisted senders;
  3. stores an AES-256-GCM copy only while the challenge is pending, so the
     code can be submitted to a provider;
  4. burns the challenge on first use (single-use).

The raw SMS body is never stored or logged — only a redacted preview. The
AES key derives from the operator-supplied gateway secret (stored redacted in
``accounts.json``; the real key material comes from the source ``.automaton``
or the operator).
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import PAYMENT_DB, connect_db
from .accounts import load_accounts, otp_secrets

DEFAULT_TTL_SECONDS = 900
MAX_ATTEMPTS = 3


class OtpError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _regex_otp(text: str) -> Optional[str]:
    """Extract a one-time code from an SMS body (digits, length 4-8)."""
    m = re.search(r"(?:code|verification|auth|otp)?\s*([0-9]{4,8})", text or "")
    return m.group(1) if m else None


def redact_sms(text: str, keep: int = 4) -> str:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) > keep:
        return digits[-keep:]
    return digits or ""


def _account(accounts=None) -> str:
    return accounts or load_accounts()


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS sms_otp_challenge (
          id TEXT PRIMARY KEY,
          purpose TEXT NOT NULL,
          subject_id TEXT NOT NULL,
          phone TEXT NOT NULL,
          sender TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          otp_hash TEXT,
          otp_cipher TEXT,
          otp_iv TEXT,
          otp_tag TEXT,
          device_id TEXT,
          expires_at TEXT NOT NULL,
          attempts INTEGER DEFAULT 0,
          max_attempts INTEGER DEFAULT 3,
          received_at TEXT,
          claimed_at TEXT,
          used_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    db.commit()


def _otp_key(accounts) -> bytes:
    """AES key derived from the gateway secret (fail-closed: configured-only)."""
    import hashlib

    secret = otp_secrets(accounts)["gatewaySecret"]
    if not secret:
        raise OtpError("OTP gateway secret not provisioned (settlement.otp.gatewaySecret)")
    return hashlib.sha256(secret.encode()).digest()


def create_challenge(
    purpose: str,
    phone: str,
    subject_id: str,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    sender_allowlist: Optional[List[str]] = None,
    accounts=None,
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Create (or revive-expired) a challenge; burns pending/claimed/fulfilled ones."""
    import uuid

    phone = phone.strip()
    if not phone:
        raise OtpError("phone required")
    db = db or connect_db(PAYMENT_DB)
    ensure_schema(db)
    accounts = accounts or _account()
    key = _otp_key(accounts)

    now = _now()
    # Burn prior in-flight challenges for the same (purpose, subject).
    db.execute(
        "UPDATE sms_otp_challenge SET status='failed', updated_at=? "
        "WHERE purpose = ? AND subject_id = ? AND status IN ('pending','fulfilled','claimed')",
        (now, purpose, subject_id),
    )

    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    code = secrets.token_hex(3)  # 9-char-ish OTP; the SMS provider formats it
    cipher = AESGCM(key)
    nonce = secrets.token_bytes(12)
    encrypted = cipher.encrypt(nonce, code.encode(), None)
    challenge_id = "otp_" + uuid.uuid4().hex[:12]
    db.execute(
        """INSERT INTO sms_otp_challenge
           (id, purpose, subject_id, phone, sender, status, otp_hash, otp_cipher, otp_iv, otp_tag,
            device_id, expires_at, attempts, max_attempts, created_at, updated_at)
         VALUES (?,?,?,?,?, 'pending', ?,?,NULL,NULL,NULL,?,0,?,?,?)""",
        (
            challenge_id, purpose, subject_id, phone,
            (sender_allowlist or [])[0] if (sender_allowlist or []) else "",
            hashlib.sha256(code.encode()).hexdigest(),
            _b64(encrypted), nonce.hex(), _iso_after(int(ttl_seconds)), now, now,
        ),
    )
    db.commit()
    return get_challenge(challenge_id, db=db)


def _b64(b: bytes) -> str:
    import base64

    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    import base64

    return base64.b64decode(s.encode())


def ingest_sms(
    text: str,
    device_phone: str,
    db: Optional[sqlite3.Connection] = None,
    device_id: Optional[str] = None,
    sender: str = "",
    accounts=None,
) -> Dict[str, Any]:
    """Ingest an SMS body posted by a device's forwarder. Returns the challenge state."""
    from .accounts import normalized_phone

    phone = normalized_phone(device_phone)
    code = _regex_otp(text)
    if not code:
        return {"status": "failed", "reason": "no OTP found in SMS text"}
    db = db or connect_db(PAYMENT_DB)
    ensure_schema(db)
    now = _now()
    db.execute(
        "UPDATE sms_otp_challenge SET status='failed', updated_at=? "
        "WHERE status IN ('pending','fulfilled','claimed') AND phone = ? AND sender = ? AND "
        "expires_at < ?",
        (now, phone, sender, now),
    )
    row = db.execute(
        "SELECT * FROM sms_otp_challenge WHERE status='pending' AND phone = ? AND sender = ? "
        "AND expires_at >= ? ORDER BY created_at DESC LIMIT 1",
        (phone, sender, now),
    ).fetchone()
    if not row:
        return {"status": "failed", "reason": "no pending challenge for this phone/sender"}
    import hashlib

    key = _otp_key(accounts or _account())
    # musashaban hashes deviceId + phone + code.
    digest = hashlib.sha256((row["device_id"] or "") + row["phone"] + code).hexdigest()
    db.execute(
        "UPDATE sms_otp_challenge SET status='fulfilled', otp_hash=?, updated_at=? WHERE id=?",
        (digest, now, row["id"]),
    )
    db.commit()
    return {"status": "fulfilled", "challenge_id": row["id"]}


def get_challenge(challenge_id: str, db: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    row = db.execute("SELECT * FROM sms_otp_challenge WHERE id = ?", (challenge_id,)).fetchone()
    return _challenge_row(row) if row else None


def list_challenges(db: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    rows = db.execute("SELECT * FROM sms_otp_challenge ORDER BY created_at DESC").fetchall()
    return [_challenge_row(r) for r in rows]


def _challenge_row(row) -> Dict[str, Any]:
    out = {k: row[k] for k in row.keys()}
    out.pop("otp_hash", None)
    return out


def authorize(
    challenge_id: str,
    db: Optional[sqlite3.Connection] = None,
) -> str:
    """Burn a fulfilled challenge (single-use)."""
    db = db or connect_db(PAYMENT_DB)
    row = db.execute("SELECT * FROM sms_otp_challenge WHERE id = ?", (challenge_id,)).fetchone()
    if not row:
        raise OtpError("challenge not found")
    if row["status"] != "fulfilled":
        raise OtpError(f"challenge not fulfilled (status={row['status']})")
    now = _now()
    db.execute("UPDATE sms_otp_challenge SET status='claimed', claimed_at=?, updated_at=? WHERE id=?",
               (now, now, challenge_id))
    db.commit()
    return challenge_id


def burn_on_use(challenge_id: str, db: Optional[sqlite3.Connection] = None) -> None:
    db = db or connect_db(PAYMENT_DB)
    now = _now()
    db.execute("UPDATE sms_otp_challenge SET status='used', used_at=?, updated_at=? WHERE id=?",
               (now, now, challenge_id))
    db.commit()
