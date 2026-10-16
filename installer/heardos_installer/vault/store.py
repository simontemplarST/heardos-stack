"""Encrypted secrets vault: one file, ~/.config/heardos/vault.json, PyNaCl SecretBox under an argon2id-derived key.

File format: {"v": 1, "kdf": "argon2id", "salt": b64, "ops": int, "mem": int, "nonce": b64, "box": b64}
The plaintext is a flat JSON object of name -> string. The passphrase is never stored here; keyring_.py may keep it.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from typing import Iterable, Optional

import nacl.exceptions
import nacl.pwhash
import nacl.secret
import nacl.utils

from ..engine.state import config_dir


class VaultLocked(RuntimeError):
    pass


class WrongPassphrase(RuntimeError):
    pass


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


class Vault:
    OPS = nacl.pwhash.argon2id.OPSLIMIT_MODERATE
    MEM = nacl.pwhash.argon2id.MEMLIMIT_MODERATE

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (config_dir() / "vault.json")
        self._lock = threading.RLock()
        self._key: Optional[bytes] = None
        self._data: dict[str, str] = {}
        self._meta: dict = {}

    # --- lifecycle ---
    def exists(self) -> bool:
        return self.path.exists()

    def create(self, passphrase: str) -> None:
        with self._lock:
            if self.exists():
                raise FileExistsError(self.path)
            salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
            self._meta = {"v": 1, "kdf": "argon2id", "salt": _b64(salt), "ops": self.OPS, "mem": self.MEM}
            self._key = self._derive(passphrase, salt, self.OPS, self.MEM)
            self._data = {}
            self._write()

    def unlock(self, passphrase: str) -> None:
        with self._lock:
            doc = json.loads(self.path.read_text())
            key = self._derive(passphrase, _unb64(doc["salt"]), doc["ops"], doc["mem"])
            box = nacl.secret.SecretBox(key)
            try:
                plain = box.decrypt(_unb64(doc["box"]), _unb64(doc["nonce"]))
            except nacl.exceptions.CryptoError as e:
                raise WrongPassphrase("wrong passphrase") from e
            self._meta = {k: doc[k] for k in ("v", "kdf", "salt", "ops", "mem")}
            self._key = key
            self._data = json.loads(plain.decode())

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._data = {}

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def change_passphrase(self, new: str) -> None:
        with self._lock:
            self._require()
            salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
            self._meta.update(salt=_b64(salt))
            self._key = self._derive(new, salt, self._meta["ops"], self._meta["mem"])
            self._write()

    # --- access ---
    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            self._require()
            return self._data.get(name, default)

    def set(self, name: str, value: str) -> None:
        self.update({name: value})

    def update(self, values: dict[str, str]) -> None:
        with self._lock:
            self._require()
            for k, v in values.items():
                if v is None:
                    self._data.pop(k, None)
                else:
                    self._data[k] = str(v)
            self._write()

    def delete(self, name: str) -> None:
        self.update({name: None})  # type: ignore[dict-item]

    def names(self) -> list[str]:
        with self._lock:
            self._require()
            return sorted(self._data)

    def values(self) -> Iterable[str]:
        """All secret values, for the log redactor. Empty when locked (nothing to redact yet)."""
        with self._lock:
            return list(self._data.values()) if self._key else []

    def as_env(self, names: Optional[Iterable[str]] = None) -> dict[str, str]:
        with self._lock:
            self._require()
            if names is None:
                return dict(self._data)
            return {n: self._data[n] for n in names if n in self._data}

    # --- internals ---
    def _require(self) -> None:
        if self._key is None:
            raise VaultLocked("vault is locked")

    @staticmethod
    def _derive(passphrase: str, salt: bytes, ops: int, mem: int) -> bytes:
        return nacl.pwhash.argon2id.kdf(nacl.secret.SecretBox.KEY_SIZE, passphrase.encode(), salt, opslimit=ops, memlimit=mem)

    def _write(self) -> None:
        box = nacl.secret.SecretBox(self._key)
        nonce = nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE)
        ct = box.encrypt(json.dumps(self._data, sort_keys=True).encode(), nonce).ciphertext
        doc = dict(self._meta, nonce=_b64(nonce), box=_b64(ct))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
