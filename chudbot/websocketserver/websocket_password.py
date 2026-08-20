"""Generate a password hash for WEB_WS_PASSWORD_HASH."""

from __future__ import annotations

import base64
import getpass
import hashlib
import secrets


def main() -> None:
    password = getpass.getpass("WebSocket password: ")
    if not password:
        raise SystemExit("Password cannot be empty")
    iterations = 310_000
    salt = secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    print(f"WEB_WS_PASSWORD_HASH=pbkdf2_sha256${iterations}${salt}${encoded}")


if __name__ == "__main__":
    main()
