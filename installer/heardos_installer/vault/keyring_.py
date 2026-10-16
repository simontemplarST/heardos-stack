"""Optional: remember the vault passphrase in the OS keyring (GNOME Keyring, KWallet, macOS Keychain, Windows)."""
from __future__ import annotations

from typing import Optional

SERVICE = "heardos"
ACCOUNT = "vault-passphrase"


def available() -> bool:
    try:
        import keyring
        from keyring.backends.fail import Keyring as Fail
        return not isinstance(keyring.get_keyring(), Fail)
    except Exception:
        return False


def remember(passphrase: str) -> bool:
    try:
        import keyring
        keyring.set_password(SERVICE, ACCOUNT, passphrase)
        return True
    except Exception:
        return False


def recall() -> Optional[str]:
    try:
        import keyring
        return keyring.get_password(SERVICE, ACCOUNT)
    except Exception:
        return None


def forget() -> None:
    try:
        import keyring
        keyring.delete_password(SERVICE, ACCOUNT)
    except Exception:
        pass
