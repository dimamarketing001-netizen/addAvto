from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class TokenCryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.getenv("METRIKA_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise TokenCryptoError(
            "Не задан METRIKA_TOKEN_ENCRYPTION_KEY в .env. Сгенерируйте ключ Fernet."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise TokenCryptoError(
            "METRIKA_TOKEN_ENCRYPTION_KEY имеет неправильный формат. Сгенерируйте новый ключ Fernet."
        ) from exc


def encrypt_secret(value: str) -> str:
    if not value:
        raise TokenCryptoError("Нельзя зашифровать пустой секрет.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
        raise TokenCryptoError("Не удалось расшифровать OAuth-токен. Проверьте ключ шифрования в .env.") from exc
