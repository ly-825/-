import hashlib
import hmac
import secrets
from datetime import datetime

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError


_password_hasher = PasswordHasher()


def hash_password(raw_password: str) -> str:
    return _password_hasher.hash(raw_password)


def verify_password(encoded_password: str, raw_password: str) -> bool:
    try:
        return _password_hasher.verify(encoded_password, raw_password)
    except (VerifyMismatchError, VerificationError):
        return False


def hash_secret(raw_secret: str, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}:{raw_secret}".encode("utf-8")).hexdigest()


def secrets_match(raw_secret: str, encoded_secret: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_secret(raw_secret, pepper), encoded_secret)


def new_activation_code() -> str:
    return f"{secrets.randbelow(100_000_000):08d}"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def verify_totp(secret: str, code: str, now: datetime) -> bool:
    return pyotp.TOTP(secret).verify(code, for_time=now, valid_window=1)
