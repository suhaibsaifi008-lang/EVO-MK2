"""Phase 6: vault_secrets -- secret storage with platform guard."""
import base64
import json
import logging
import os
import sys as _sys
from pathlib import Path

from . import db
from .config import DATA

log = logging.getLogger("mk2.vault_secrets")

STORE = DATA / "secrets.bin"
_no_crypt = (_sys.platform != "win32")

if not _no_crypt:
	try:
		import ctypes
		from ctypes import wintypes
		_crypt32 = ctypes.windll.crypt32
		_kernel32 = ctypes.windll.kernel32
	except Exception:
		_no_crypt = True


class _DATA_BLOB:
	def __init__(self, data=None):
		self.cbData = len(data) if data else 0
		self.pbData = ctypes.c_void_p.from_buffer(ctypes.create_string_buffer(data or b"")).value if data else 0


from cryptography.fernet import Fernet


def _fernet_fallback() -> Fernet:
	import base64
	import hashlib
	machine_id = os.environ.get("COMPUTERNAME", "evo-mk2-node")
	key_mat = hashlib.sha256(f"evo-secrets-{machine_id}".encode("utf-8")).digest()
	return Fernet(base64.urlsafe_b64encode(key_mat))


def _protect(plain):
	if not _no_crypt:
		try:
			in_b = _DATA_BLOB(plain)
			out = _DATA_BLOB()
			if _crypt32.CryptProtectData(ctypes.byref(in_b), None, None, None, None, 0x01, ctypes.byref(out)):
				return ctypes.string_at(out.pbData, out.cbData)
		except Exception as exc:
			log.warning("DPAPI encryption failed (%s); using Fernet encryption fallback.", exc)
	try:
		return b"fernet:" + _fernet_fallback().encrypt(plain)
	except Exception as exc:
		log.error("All encryption backends failed for secret: %s", exc)
		raise RuntimeError("Failed to encrypt secret; refusing to store plaintext.") from exc


def _unprotect(cipher):
	if isinstance(cipher, bytes) and cipher.startswith(b"fernet:"):
		try:
			return _fernet_fallback().decrypt(cipher[7:])
		except Exception as exc:
			log.error("Failed to decrypt Fernet secret: %s", exc)
			return b""
	if not _no_crypt:
		try:
			in_b = _DATA_BLOB(cipher)
			out = _DATA_BLOB()
			if _crypt32.CryptUnprotectData(ctypes.byref(in_b), None, None, None, None, 0x01, ctypes.byref(out)):
				return ctypes.string_at(out.pbData, out.cbData)
		except Exception:
			pass
	return cipher


def _load():
	if not STORE.exists():
		return {}
	try:
		raw = json.loads(STORE.read_text(encoding="utf-8"))
		return {k: _unprotect(base64.b64decode(v)).decode("utf-8")
				for k, v in raw.items()}
	except Exception:
		return {}


def _save_all(secrets):
	DATA.mkdir(parents=True, exist_ok=True)
	raw = {k: base64.b64encode(_protect(v.encode("utf-8"))).decode("ascii")
		 for k, v in secrets.items()}
	STORE.write_text(json.dumps(raw, indent=0), encoding="utf-8")


def get_secret(key):
	return _load().get(key.strip().lower())


from .tools import tool


@tool("secret_store", "Securely store an API key/password.",
	 {"key": {"type": "string"}, "value": {"type": "string"}},
	 permission="execute")
def secret_store(key, value):
	key = key.strip().lower()[:60]
	value = str(value or "").strip()
	if not key or len(value) < 4:
		return {"ok": False, "speech": "Need a key name and an actual value.", "data": {}}
	secrets = _load()
	secrets[key] = value
	try:
		_save_all(secrets)
	except Exception as exc:
		return {"ok": False, "speech": f"Storage failed: {str(exc)[:120]}", "data": {}}
	db.audit("secret_store", key, True, "(value hidden)")
	return {"ok": True,
			"speech": f"'{key}' stored securely.",
			"data": {"key": key}}


@tool("secret_get", "Retrieve a stored secret.",
	 {"key": {"type": "string"}, "reveal": {"type": "boolean"}},
	 permission="read")
def secret_get(key, reveal=False):
	key = key.strip().lower()
	val = get_secret(key)
	if val is None:
		db.audit("secret_get", key, False, "not found")
		return {"ok": False, "speech": f"No secret named '{key}'.", "data": {}}
	db.audit("secret_get", key, True, "(value hidden)")
	if reveal:
		return {"ok": True,
				"speech": f"'{key}' retrieved - starts with '{val[:3]}'.",
				"data": {"value": val}}
	masked = val[:2] + "*" * max(4, len(val) - 5) + val[-2:]
	return {"ok": True, "speech": f"'{key}' exists ({masked}).", "data": {"masked": masked}}


@tool("secret_list", "List which secret names are stored.", {}, permission="read")
def secret_list():
	names = sorted(_load().keys())
	if not names:
		return {"ok": True, "speech": "Vault is empty.", "data": {"keys": []}}
	return {"ok": True, "speech": f"Stored keys: {', '.join(names)}", "data": {"keys": names}}


@tool("secret_delete", "Remove a secret from the vault.",
	 {"key": {"type": "string"}}, permission="execute")
def secret_delete(key):
	key = key.strip().lower()
	secrets = _load()
	if key not in secrets:
		return {"ok": False, "speech": f"No secret '{key}'.", "data": {}}
	del secrets[key]
	_save_all(secrets)
	db.audit("secret_delete", key, True, "(value hidden)")
	return {"ok": True, "speech": f"'{key}' deleted.", "data": {}}


def vault_lookup(key):
	return os.environ.get(key) or get_secret(key)
