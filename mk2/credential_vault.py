"""Encrypted Credential Vault for EVO MK2 (JARVIS Foundation).

Stores external service credentials (Upwork, Gmail, Fiverr, Stripe, etc.)
encrypted locally using AES-256 (Fernet) with keys derived via PBKDF2.
Zero plaintext credentials are ever logged or exposed to LLM context.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .config import DATA

log = logging.getLogger("mk2.cred_vault")

DEFAULT_VAULT_PATH = DATA / "credential_vault.enc"
DEFAULT_SALT_PATH = DATA / ".vault_salt"


class CredentialVault:
    """Encrypted storage for all user service credentials."""

    def __init__(self, vault_path: Optional[Path] = None, master_key: Optional[str] = None):
        self.vault_path = vault_path or DEFAULT_VAULT_PATH
        self.salt_path = (self.vault_path.parent / ".vault_salt") if vault_path else DEFAULT_SALT_PATH
        self._master_key = master_key
        self._fernet: Optional[Fernet] = None
        self._init_crypto()

    import hmac as _hmac
    import hashlib as _hashlib

    def _get_salt(self) -> bytes:
        self.salt_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.salt_path.exists():
            salt = os.urandom(16)
            tag = self._hmac.new(b"evo-mk2-salt-integrity", salt, self._hashlib.sha256).digest()
            self.salt_path.write_bytes(salt + tag)  # 16 + 32 = 48 bytes
            return salt
        raw = self.salt_path.read_bytes()
        if len(raw) == 48:  # new format with HMAC
            salt, tag = raw[:16], raw[16:]
            expected = self._hmac.new(b"evo-mk2-salt-integrity", salt, self._hashlib.sha256).digest()
            if not self._hmac.compare_digest(tag, expected):
                log.warning("Salt file integrity check failed! Possible tampering.")
                raise ValueError("Salt file integrity check failed")
            return salt
        return raw[:16]  # legacy 16-byte salt, no HMAC

    def _derive_key_material(self) -> str:
        key_source = (
            self._master_key
            or os.environ.get("EVO_MASTER_KEY")
        )
        if not key_source:
            raise ValueError(
                "No master key provided. Set EVO_MASTER_KEY environment variable "
                "or pass master_key to CredentialVault(). "
                "Machine fingerprint fallback is disabled for security."
            )
        return key_source

    def _init_crypto(self) -> None:
        salt = self._get_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600_000, # NIST SP 800-132 recommends >= 600k for PBKDF2-SHA256
        )
        derived = kdf.derive(self._derive_key_material().encode("utf-8"))
        url_safe = base64.urlsafe_b64encode(derived)
        self._fernet = Fernet(url_safe)

    def _read_vault_unlocked(self) -> dict[str, Any]:
        if not self.vault_path.exists():
            return {}
        try:
            enc_data = self.vault_path.read_bytes()
            if not enc_data:
                return {}
            dec_bytes = self._fernet.decrypt(enc_data)
            return json.loads(dec_bytes.decode("utf-8"))
        except Exception as exc:
            log.warning("Failed to decrypt credential vault: %s", exc)
            return {}

    def _write_vault_unlocked(self, data: dict[str, Any]) -> None:
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data).encode("utf-8")
        enc_data = self._fernet.encrypt(raw)
        self.vault_path.write_bytes(enc_data)

    def store(self, service: str, creds: dict[str, Any]) -> dict[str, Any]:
        """Store or update credentials for a service (e.g. upwork, gmail, stripe)."""
        s = service.strip().lower()
        if not s:
            raise ValueError("Service name cannot be empty")
        vault_data = self._read_vault_unlocked()
        entry = {
            "service": s,
            "data": creds,
            "updated_at": __import__("time").time(),
        }
        vault_data[s] = entry
        self._write_vault_unlocked(vault_data)
        log.info("Credentials stored securely for service: %s", s)
        return {"ok": True, "service": s}

    def get(self, service: str) -> Optional[dict[str, Any]]:
        """Retrieve decrypted credentials for a service. Returns None if missing."""
        s = service.strip().lower()
        vault_data = self._read_vault_unlocked()
        entry = vault_data.get(s)
        if not entry:
            return None
        return entry.get("data")

    def has(self, service: str) -> bool:
        """Check if active credentials exist for a service without exposing data."""
        s = service.strip().lower()
        vault_data = self._read_vault_unlocked()
        return s in vault_data

    def list_services(self) -> list[str]:
        """List all services with stored credentials."""
        vault_data = self._read_vault_unlocked()
        return sorted(list(vault_data.keys()))

    def remove(self, service: str) -> bool:
        """Delete credentials for a service."""
        s = service.strip().lower()
        vault_data = self._read_vault_unlocked()
        if s in vault_data:
            del vault_data[s]
            self._write_vault_unlocked(vault_data)
            log.info("Credentials removed for service: %s", s)
            return True
        return False

    def rotate_key(self) -> dict[str, Any]:
        """Re-encrypt all credentials with a fresh salt."""
        vault_data = self._read_vault_unlocked()
        if not vault_data:
            return {"ok": True, "message": "No credentials to rotate."}
        # Generate new salt
        new_salt = os.urandom(16)
        self.salt_path.write_bytes(new_salt)
        self._init_crypto()  # re-derive key with new salt
        self._write_vault_unlocked(vault_data)
        log.info("Credential vault key rotated successfully.")
        return {"ok": True, "rotated": len(vault_data)}

_global_vault: Optional[CredentialVault] = None


def get_credential_vault() -> CredentialVault:
    global _global_vault
    if _global_vault is None:
        _global_vault = CredentialVault()
    return _global_vault
