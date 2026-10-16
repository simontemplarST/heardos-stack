"""The installer's own SSH key (~/.ssh/heardos_ed25519): generated once, never touches the user's other keys."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_KEY = Path.home() / ".ssh" / "heardos_ed25519"


def ensure_key(path: Path = DEFAULT_KEY, comment: str = "heardos-installer") -> tuple[Path, str]:
    """Returns (private key path, public key line). Creates the pair if missing (0600 private, 0644 public)."""
    pub_path = path.with_suffix(".pub") if path.suffix != ".pub" else path
    pub_path = Path(str(path) + ".pub")
    if path.exists() and pub_path.exists():
        return path, pub_path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode() + f" {comment}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, priv)
    os.close(fd)
    pub_path.write_text(pub + "\n")
    return path, pub
