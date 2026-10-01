import base64
import hashlib
import hmac
import os

ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return 'pbkdf2_sha256${}${}${}'.format(
        ITERATIONS,
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split('$', 3)
        if scheme != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode(), base64.urlsafe_b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(digest, base64.urlsafe_b64decode(digest_b64))
    except Exception:
        return False
