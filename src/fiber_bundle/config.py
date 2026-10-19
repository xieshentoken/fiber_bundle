"""Explicit, local-only storage configuration and path validation."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


class StorageError(Exception):
    """A bounded storage failure; messages must not include received content."""


class InvalidInput(StorageError):
    pass


class IntegrityError(StorageError):
    pass


def contained(root: Path, relative: str) -> Path:
    """Reject traversal and existing symlink components, even dangling links."""
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise InvalidInput("invalid relative path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidInput("path must remain inside configured root")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise InvalidInput("symlink paths are not allowed")
    return path


def _external_root(value: str) -> Path:
    from .cloud_files import require_local

    path = Path(value)
    if not path.is_absolute():
        raise InvalidInput("explicit vault root must be absolute")
    try:
        require_local(path)  # Deny materialization before resolve/stat can touch a child.
        if path.resolve(strict=True) != path or not path.is_dir():
            raise InvalidInput("explicit vault root changed")
    except (OSError, StorageError) as exc:
        raise InvalidInput("explicit vault root unavailable or changed") from exc
    return path


@dataclass(frozen=True)
class Config:
    project_root: Path
    state_relative: str
    vaults: tuple[tuple[str, str], ...]
    vault_roots: tuple[tuple[str, str], ...] = ()

    @property
    def state_root(self) -> Path:
        return contained(self.project_root, self.state_relative)

    def storage_configuration(self) -> dict[str, str | dict[str, str]]:
        """Serialize the supported storage contract without any secrets or routing policy."""
        value: dict[str, str | dict[str, str]] = {
            "state_root": self.state_relative,
            "vaults": dict(self.vaults),
        }
        if self.vault_roots:
            value["vault_roots"] = dict(self.vault_roots)
        return value

    def vault_path(self, relative: str) -> Path:
        for name, configured in self.vaults:
            if relative == configured and name in dict(self.vault_roots):
                return _external_root(dict(self.vault_roots)[name])
        return contained(self.project_root, relative)

    def validate(self) -> None:
        if not self.project_root.is_absolute() or not self.project_root.is_dir():
            raise InvalidInput("project root must be an existing absolute directory")
        if self.project_root.resolve() != self.project_root:
            raise InvalidInput("project root must be canonical")
        # M1 is deliberately limited to the two approved test vaults.
        if (
            dict(self.vaults)
            != {
                "personal": "test vault/temp_vault1",
                "supplier": "test vault/temp_vault2",
            }
            or len(self.vaults) != 2
        ):
            raise InvalidInput("M1 accepts only the two approved test vault paths")
        state = self.state_root
        if "Mobile Documents" in state.parts:
            raise InvalidInput("runtime state must remain outside iCloud")
        overrides = dict(self.vault_roots)
        if (
            len(overrides) != len(self.vault_roots)
            or not set(overrides) <= dict(self.vaults).keys()
        ):
            raise InvalidInput("invalid explicit vault roots")
        for value in overrides.values():
            _external_root(value)
        paths: list[Path] = []
        for _, relative in self.vaults:
            vault = self.vault_path(relative)
            if state == vault or state in vault.parents or vault in state.parents:
                raise InvalidInput("state and vault paths must not overlap")
            if any(vault == p or vault in p.parents or p in vault.parents for p in paths):
                raise InvalidInput("vault roots must not overlap")
            paths.append(vault)


def load_config(path: Path, project_root: Path) -> Config:
    root = project_root.resolve(strict=True)
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise InvalidInput("configuration must be inside project root") from exc
    safe = contained(root, relative)
    try:
        with safe.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise InvalidInput("cannot read configuration JSON") from exc
    if not isinstance(raw, dict) or set(raw) not in (
        {"state_root", "vaults"},
        {"state_root", "vaults", "vault_roots"},
    ):
        raise InvalidInput("configuration fields are invalid")
    if not isinstance(raw["state_root"], str) or not isinstance(raw["vaults"], dict):
        raise InvalidInput("configuration types are invalid")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in raw["vaults"].items()):
        raise InvalidInput("vault configuration types are invalid")
    overrides = raw.get("vault_roots", {})
    if not isinstance(overrides, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in overrides.items()
    ):
        raise InvalidInput("invalid explicit vault roots")
    config = Config(
        root,
        raw["state_root"],
        tuple(sorted(raw["vaults"].items())),
        tuple(sorted(overrides.items())),
    )
    config.validate()
    return config


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_directory(path: Path) -> None:
    """Create parents durably. Caller must first validate the complete path."""
    if path.is_symlink():
        raise InvalidInput("symlink directory is not allowed")
    if path.exists():
        if not path.is_dir():
            raise InvalidInput("expected a directory")
        return
    make_directory(path.parent)
    path.mkdir(mode=0o700)
    sync_directory(path.parent)
