"""Secret storage.

PRIVIA keeps credentials out of the database and out of the repository. Three
backends are supported, tried in this order:

1. **OS keychain** (``keyring``) - macOS Keychain, Windows Credential Manager,
   Secret Service on Linux. Preferred when available.
2. **Encrypted file** - AES-GCM with a key derived by scrypt from a machine-local
   key file stored with ``0600`` permissions. Always available.
3. **Environment** - read-only. Values from the process environment are visible
   to :meth:`SecretStore.get` but can never be written.

Secrets are never logged, never returned over the API, and never placed in the
child-process environment of the terminal tool.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as pysecrets
import stat
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from privia_shared.errors import ConfigurationError

SERVICE_NAME = "privia"
_KEY_FILE_NAME = "secrets.key"
_STORE_FILE_NAME = "privia_secrets.enc"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH = 32


class SecretBackend:
    """Interface for a secret backend."""

    name = "base"
    writable = False

    def available(self) -> bool:  # pragma: no cover - interface
        return False

    def get(self, key: str) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, key: str, value: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, key: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def keys(self) -> list[str]:  # pragma: no cover - interface
        return []


class KeyringBackend(SecretBackend):
    """OS keychain via the optional ``keyring`` package."""

    name = "keychain"
    writable = True

    def __init__(self, service: str = SERVICE_NAME) -> None:
        self.service = service
        self._index_key = "__privia_index__"

    def available(self) -> bool:
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring
        except Exception:
            return False
        try:
            backend = keyring.get_keyring()
        except Exception:
            # keyring raises a different exception on every platform when no
            # daemon is reachable. Unavailable is unavailable.
            return False
        return not isinstance(backend, FailKeyring)

    def get(self, key: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, key)

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, key, value)
        index = set(self.keys())
        index.add(key)
        keyring.set_password(self.service, self._index_key, json.dumps(sorted(index)))

    def delete(self, key: str) -> None:
        import keyring

        # Deleting a key that is not there is success, not failure, and every
        # keyring backend signals it differently.
        with suppress(Exception):
            keyring.delete_password(self.service, key)
        index = set(self.keys())
        index.discard(key)
        keyring.set_password(self.service, self._index_key, json.dumps(sorted(index)))

    def keys(self) -> list[str]:
        import keyring

        raw = keyring.get_password(self.service, self._index_key)
        if not raw:
            return []
        try:
            return list(json.loads(raw))
        except ValueError:
            return []


class EncryptedFileBackend(SecretBackend):
    """AES-GCM encrypted JSON file with a scrypt-derived key."""

    name = "encrypted_file"
    writable = True

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).expanduser()
        self.store_path = self.directory / _STORE_FILE_NAME
        self.key_path = self.directory / _KEY_FILE_NAME
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except Exception:
            return False
        return True

    # -- key material --------------------------------------------------------

    def _material(self) -> bytes:
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            data = self.key_path.read_bytes()
            if len(data) >= 32:
                self._harden(self.key_path)
                return data
        material = pysecrets.token_bytes(48)
        self.key_path.write_bytes(material)
        self._harden(self.key_path)
        return material

    @staticmethod
    def _harden(path: Path) -> None:
        # Best effort: some filesystems (FAT, network mounts, Windows) have no
        # POSIX mode bits. The file is AES-GCM encrypted either way.
        with suppress(OSError):  # pragma: no cover
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def _key(self, salt: bytes) -> bytes:
        import hashlib

        return hashlib.scrypt(
            self._material(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_LENGTH
        )

    # -- storage -------------------------------------------------------------

    def _read_all(self) -> dict[str, str]:
        if not self.store_path.exists():
            return {}
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            envelope = json.loads(self.store_path.read_text(encoding="utf-8"))
            salt = base64.b64decode(envelope["salt"])
            nonce = base64.b64decode(envelope["nonce"])
            payload = base64.b64decode(envelope["payload"])
        except (ValueError, KeyError, OSError) as exc:
            raise ConfigurationError(
                "The local secret store is corrupt. Delete it to start over.",
                details={"path": str(self.store_path), "reason": type(exc).__name__},
            ) from exc
        try:
            plaintext = AESGCM(self._key(salt)).decrypt(nonce, payload, b"privia-secrets-v1")
        except InvalidTag as exc:
            raise ConfigurationError(
                "The local secret store could not be decrypted with this machine's key.",
                details={"path": str(self.store_path)},
            ) from exc
        return dict(json.loads(plaintext.decode("utf-8")))

    def _write_all(self, values: dict[str, str]) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.directory.mkdir(parents=True, exist_ok=True)
        salt = pysecrets.token_bytes(16)
        nonce = pysecrets.token_bytes(12)
        payload = AESGCM(self._key(salt)).encrypt(
            nonce, json.dumps(values).encode("utf-8"), b"privia-secrets-v1"
        )
        envelope = {
            "version": 1,
            "kdf": "scrypt",
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "payload": base64.b64encode(payload).decode("ascii"),
        }
        tmp = self.store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(envelope), encoding="utf-8")
        self._harden(tmp)
        tmp.replace(self.store_path)
        self._harden(self.store_path)

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._read_all().get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            values = self._read_all()
            values[key] = value
            self._write_all(values)

    def delete(self, key: str) -> None:
        with self._lock:
            values = self._read_all()
            if values.pop(key, None) is not None:
                self._write_all(values)

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._read_all())


class EnvironmentBackend(SecretBackend):
    """Read-only view of the process environment."""

    name = "environment"
    writable = False

    def available(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return os.environ.get(key) or os.environ.get(key.upper())

    def set(self, key: str, value: str) -> None:
        raise ConfigurationError(
            "Secrets cannot be written to the environment. Use the keychain or the encrypted "
            "local store instead."
        )

    def delete(self, key: str) -> None:
        raise ConfigurationError("Secrets cannot be deleted from the environment.")

    def keys(self) -> list[str]:
        return []


@dataclass(frozen=True)
class SecretRef:
    """A pointer to a secret. Safe to log, store and pass around."""

    key: str
    backend: str

    def __str__(self) -> str:
        return f"secret://{self.backend}/{self.key}"


class SecretStore:
    """Facade over the available backends."""

    def __init__(
        self,
        data_dir: Path,
        *,
        preferred: str = "file",
        backends: list[SecretBackend] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser()
        if backends is not None:
            self._backends = backends
        else:
            keyring_backend = KeyringBackend()
            file_backend = EncryptedFileBackend(self.data_dir)
            ordered: list[SecretBackend] = []
            if preferred == "keychain" and keyring_backend.available():
                ordered.append(keyring_backend)
            if file_backend.available():
                ordered.append(file_backend)
            if preferred != "keychain" and keyring_backend.available():
                ordered.append(keyring_backend)
            ordered.append(EnvironmentBackend())
            self._backends = ordered

    @property
    def backends(self) -> tuple[str, ...]:
        return tuple(b.name for b in self._backends)

    @property
    def writable_backend(self) -> SecretBackend | None:
        for backend in self._backends:
            if backend.writable and backend.available():
                return backend
        return None

    def get(self, key: str, default: str | None = None) -> str | None:
        for backend in self._backends:
            if not backend.available():
                continue
            try:
                value = backend.get(key)
            except ConfigurationError:
                raise
            except Exception:  # noqa: S112
                # A backend that is installed but broken (an unavailable keyring
                # daemon, say) must not hide a secret another backend can supply.
                continue
            if value:
                return value
        return default

    def set(self, key: str, value: str) -> SecretRef:
        backend = self.writable_backend
        if backend is None:  # pragma: no cover - cryptography is a core dependency
            raise ConfigurationError("No writable secret backend is available on this machine.")
        backend.set(key, value)
        return SecretRef(key=key, backend=backend.name)

    def delete(self, key: str) -> None:
        for backend in self._backends:
            if backend.writable and backend.available():
                backend.delete(key)

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def list_keys(self) -> list[str]:
        keys: set[str] = set()
        for backend in self._backends:
            if backend.available():
                try:
                    keys.update(backend.keys())
                except Exception:  # noqa: S112
                    continue  # One broken backend, not a broken store.
        return sorted(keys)

    def describe(self) -> dict[str, Any]:
        """Metadata only: which keys exist and where. Never the values."""
        writable = self.writable_backend
        return {
            "backends": list(self.backends),
            "writable_backend": writable.name if writable else None,
            "stored_keys": self.list_keys(),
        }
