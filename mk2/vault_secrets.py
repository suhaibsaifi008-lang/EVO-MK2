"""Phase 6: vault_secrets — Windows DPAPI-encrypted local secret store.

Secrets (API keys, passwords) are encrypted with CryptProtectData bound
to YOUR Windows login, stored as base64 in data/secrets.bin. They never
appear in chat speech, logs, or the audit ledger - only the key NAME is
ever audited. Connectors can use them via auth_env-style lookups without
the value ever passing through a prompt.
"""
import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from . import db
from .config import DATA

STORE = DATA / "secrets.bin"
_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p)), buf


def _protect(plain: bytes) -> bytes:
    in_b, _buf = _blob(plain)
    out = _DATA_BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(in_b), None, None, None,
                                     None, 0x01,  # UI_FORBIDDEN
                                     ctypes.byref(out)):
        raise OSError("DPAPI protect failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.c_void_p(out.pbData))


def _unprotect(cipher: bytes) -> bytes:
    in_b, _buf = _blob(cipher)
    out = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(in_b), None, None,
                                       None, None, 0x01, ctypes.byref(out)):
        raise OSError("DPAPI unprotect failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.c_void_p(out.pbData))


def _load() -> dict:
    if not STORE.exists():
        return {}
    try:
        raw = json.loads(STORE.read_text(encoding="utf-8"))
        return {k: _unprotect(base64.b64decode(v)).decode("utf-8")
                for k, v in raw.items()}
    except Exception:
        return {}


def _save_all(secrets: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    raw = {k: base64.b64encode(_protect(v.encode("utf-8"))).decode("ascii")
           for k, v in secrets.items()}
    STORE.write_text(json.dumps(raw, indent=0), encoding="utf-8")


def get_secret(key: str) -> str | None:
    return _load().get(key.strip().lower())


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("secret_store", "Securely store an API key/password (Windows-encrypted). The value is NEVER logged or spoken back.",
      {"key": {"type": "string"}, "value": {"type": "string"}},
      permission="execute")
def secret_store(key: str, value: str) -> dict:
    key = key.strip().lower()[:60]
    value = str(value or "").strip()
    if not key or len(value) < 4:
        return {"ok": False,
                "speech": "Need a key name and an actual value.", "data": {}}
    secrets = _load()
    secrets[key] = value
    try:
        _save_all(secrets)
    except Exception as exc:
        return {"ok": False, "speech": f"Encryption failed: {str(exc)[:120]}",
                "data": {}}
    db.audit("secret_store", key, True, "(value hidden)")
    return {"ok": True,
            "speech": f"'{key}' is stored encrypted and bound to your "
                      "Windows login. I can use it but I will never show it.",
            "data": {"key": key}}


@tool("secret_get", "Retrieve a stored secret's presence. Value stays masked unless explicitly needed by another tool.",
      {"key": {"type": "string"}, "reveal": {"type": "boolean"}},
      permission="read")
def secret_get(key: str, reveal: bool = False) -> dict:
    key = key.strip().lower()
    val = get_secret(key)
    if val is None:
        db.audit("secret_get", key, False, "not found")
        return {"ok": False, "speech": f"No secret named '{key}'.",
                "data": {}}
    db.audit("secret_get", key, True, "(value hidden)")
    if reveal:
        return {"ok": True,
                "speech": f"'{key}' retrieved - it starts with '{val[:3]}' "
                          "and is hidden from chat.",
                "data": {"value": val}}
    masked = val[:2] + "*" * max(4, len(val) - 5) + val[-2:]
    return {"ok": True, "speech": f"'{key}' exists ({masked}). Use reveal=true "
                                  "only when another tool needs it.",
            "data": {"masked": masked}}


@tool("secret_list", "List which secret names are stored (never values).", {},
      permission="read")
def secret_list() -> dict:
    names = sorted(_load().keys())
    if not names:
        return {"ok": True, "speech": "Vault is empty.", "data": {"keys": []}}
    return {"ok": True, "speech": f"Stored keys: {', '.join(names)}",
            "data": {"keys": names}}


@tool("secret_delete", "Remove a secret from the vault.",
      {"key": {"type": "string"}}, permission="execute")
def secret_delete(key: str) -> dict:
    key = key.strip().lower()
    secrets = _load()
    if key not in secrets:
        return {"ok": False, "speech": f"No secret '{key}'.", "data": {}}
    del secrets[key]
    _save_all(secrets)
    db.audit("secret_delete", key, True, "(value hidden)")
    return {"ok": True, "speech": f"'{key}' deleted.", "data": {}}


def vault_lookup(key: str) -> str | None:
    """For other modules (connectors): env first, then encrypted vault."""
    return os.environ.get(key) or get_secret(key)
