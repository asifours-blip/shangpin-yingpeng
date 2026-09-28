"""口令哈希与校验。用标准库 PBKDF2，避免 Windows + bcrypt 编译问题。"""

import hashlib
import hmac
import secrets

_ROUNDS = 120_000


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), _ROUNDS).hex()
    return f"pbkdf2_sha256${_ROUNDS}${salt}${digest}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt, digest = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), rounds).hex()
    return hmac.compare_digest(check, digest)
