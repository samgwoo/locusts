"""Accounts: email addresses, passwords, session and emailed-link tokens, rate limits, and sending the emails.

Passwords are hashed with scrypt from the standard library, each with its own salt. Session cookies and the
tokens in emailed links are random, and the server keeps only their SHA-256 hashes.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
from collections import defaultdict, deque
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("locusts.auth")

# scrypt's cost: 2**14 takes about 50 ms and 16 MB per hash. Tests turn it down.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

PASSWORD_MIN = 10
PASSWORD_MAX = 128
EMAIL_MAX = 254

SESSION_DAYS = 30
VERIFY_HOURS = 24
RESET_HOURS = 1

# A normal address: a local part, then a domain with a dot (gmail.com, seas.upenn.edu, ...).
_EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return len(email) <= EMAIL_MAX and _EMAIL.match(email) is not None


def password_problem(password: str, email: str) -> str | None:
    """Why a password won't do, or None."""
    if len(password) < PASSWORD_MIN:
        return f"passwords need at least {PASSWORD_MIN} characters"
    if len(password) > PASSWORD_MAX:
        return f"passwords can be at most {PASSWORD_MAX} characters"
    if password.lower() in (email, email.split("@")[0]):
        return "the password can't be your email"
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(digest) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


_dummy: str | None = None


def check_nobody(password: str) -> bool:
    """Spend as long as checking a real password, for an email with no account, so response times don't
    tell an attacker which emails have one."""
    global _dummy
    if _dummy is None or int(_dummy.split("$")[1]) != SCRYPT_N:
        _dummy = hash_password(secrets.token_hex(16))
    check_password(password, _dummy)
    return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Counts attempts per key over a sliding window, in memory (per server process)."""

    def __init__(self):
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float, now: float) -> bool:
        """Record an attempt at `key`, unless `limit` have already been made in the last `window` seconds."""
        hits = self.hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        if len(self.hits) > 10_000:       # forget idle keys now and then, so memory stays bounded
            for k in [k for k, v in self.hits.items() if not v or v[-1] <= now - window]:
                del self.hits[k]
        return True


# --- Email ---

class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    """Development: emails go to the server log instead, links and all."""

    def send(self, to: str, subject: str, body: str) -> None:
        log.warning("Email to %s: %s\n%s", to, subject, body)


class SmtpMailer:
    """Sends over SMTP: STARTTLS on port 587 (or any port but 465), implicit TLS on 465."""

    def __init__(self, host: str, port: int, sender: str, user: str | None = None, password: str | None = None):
        self.host, self.port, self.sender, self.user, self.password = host, port, sender, user, password

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        if self.port == 465:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=20)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
            server.starttls()
        with server:
            if self.user:
                server.login(self.user, self.password or "")
            server.send_message(msg)


def mailer_from_env() -> Mailer:
    """SMTP if LOCUSTS_SMTP_HOST is set (with LOCUSTS_SMTP_PORT, LOCUSTS_SMTP_USER, LOCUSTS_SMTP_PASSWORD, and
    LOCUSTS_MAIL_FROM), otherwise the console."""
    host = os.environ.get("LOCUSTS_SMTP_HOST")
    if not host:
        return ConsoleMailer()
    user = os.environ.get("LOCUSTS_SMTP_USER")
    return SmtpMailer(host, int(os.environ.get("LOCUSTS_SMTP_PORT", "587")),
                      os.environ.get("LOCUSTS_MAIL_FROM", user or f"locusts@{host}"),
                      user, os.environ.get("LOCUSTS_SMTP_PASSWORD"))
