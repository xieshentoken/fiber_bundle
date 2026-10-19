"""Authenticated, bounded restoration into a fresh stopped project; never overwrite a project."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import backup_preflight, restore_guard, restore_rebind
from .backup_bundle import fresh_path, layout, section_entries, vault_root
from .backup_crypto import CHUNK
from .backup_inventory import Limits
from .config import Config, IntegrityError, InvalidInput, contained, make_directory, sync_directory
from .inputs import canonical
from .query_policy_store import write_private


def _manifest(
    value: bytes, root: Path, marker: dict[str, Any], limits: Limits
) -> tuple[dict[str, Any], Config]:
    try:
        manifest = json.loads(value)
        if canonical(manifest).encode() != value:
            raise ValueError()
        if (
            manifest["bundle_schema"] != "encrypted-backup/v1"
            or manifest["bundle_id"] != marker["bundle_id"]
        ):
            raise ValueError()
        plain = {
            k: v
            for k, v in manifest.items()
            if k not in {"bundle_id", "bundle_schema", "physical_files", "model_secrets"}
        }
        sha = plain.pop("sha256")
        if (
            hashlib.sha256(canonical(plain).encode()).hexdigest() != sha
            or sha != marker["scope_sha256"]
        ):
            raise ValueError()
        storage = manifest["storage_config"]
        if "model_secrets" in manifest:
            from .model_configuration.backup import validate as validate_models

            validate_models(manifest["model_secrets"])
        config = Config(root, storage["state_root"], tuple(sorted(storage["vaults"].items())))
        config.validate()
        entries = manifest["entries"]
        if not isinstance(entries, list) or len(entries) > limits.entries:
            raise ValueError()
        roots = {"state": config.state_root, "configuration": root}
        for v in manifest["vaults"]:
            if v["id"] in roots or v["status"] not in {"active", "pending"}:
                raise ValueError()
            if v.get("root_kind", "legacy") != "legacy":
                from uuid import UUID

                if v["relative_path"] != "vaults/" + str(UUID(v["id"])):
                    raise ValueError()
            roots["vault:" + v["id"]] = vault_root(config, v, restore=True)
        seen = set()
        total = 0
        for entry in entries:
            section, relative, kind = entry["section"], entry["path"], entry["kind"]
            if (
                section not in roots
                or (section, relative) in seen
                or kind not in {"file", "directory"}
            ):
                raise ValueError()
            seen.add((section, relative))
            if relative:
                contained(roots[section], relative)
                if section == "configuration" and not (
                    relative.startswith(("configs/", "skills/fake-expert/", "skills/bili-note-V3/"))
                    or relative in {"skills/fake-expert", "skills/bili-note-V3"}
                ):
                    raise ValueError()
            elif kind != "directory":
                raise ValueError()
            if len(Path(relative).parts) > limits.depth:
                raise ValueError()
            if kind == "file":
                if type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= limits.file_bytes:
                    raise ValueError()
                if (
                    not isinstance(entry["sha256"], str)
                    or len(entry["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in entry["sha256"])
                ):
                    raise ValueError()
                total += entry["bytes"]
        if total > limits.total_bytes or total != manifest["file_bytes"]:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise IntegrityError("restore_manifest_invalid") from exc
    return manifest, config


def restore(
    bundle: Path,
    destination: Path,
    password: str,
    *,
    limits: Limits = backup_preflight.DEFAULT_LIMITS,
) -> dict[str, Any]:
    limits.validate()
    destination = fresh_path(destination)
    marker = layout(bundle)
    # Authenticate both manifests before creating the destination.
    from .backup_incremental.readers import sections

    with sections(bundle, password, marker) as (left, right):
        header = left.get()
        if right.get() != header:
            raise IntegrityError("restore_sections_do_not_match")
        # Validation needs a canonical existing parent; the new project itself is exclusive below.
        destination.mkdir(mode=0o700)
        config: Config | None = None
        try:
            manifest, config = _manifest(header, destination, marker, limits)
            make_directory(config.state_root)
            restore_guard.save(
                config.state_root, {"state": "restoring", "bundle_id": marker["bundle_id"]}
            )
            roots = {
                "state": config.state_root,
                "configuration": destination,
                **{
                    "vault:" + v["id"]: vault_root(config, v, restore=True)
                    for v in manifest["vaults"]
                },
            }
            for part, reader in (("state", left), ("vaults", right)):
                for entry in section_entries(manifest, part):
                    root = roots[entry["section"]]
                    path = contained(root, entry["path"]) if entry["path"] else root
                    if entry["kind"] == "directory":
                        make_directory(path)
                        continue
                    make_directory(path.parent)
                    skip_gate = entry["section"] == "state" and entry["path"] == "restore-gate.json"
                    fd = (
                        None
                        if skip_gate
                        else os.open(
                            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                        )
                    )
                    digest, remaining = hashlib.sha256(), entry["bytes"]
                    handle = os.fdopen(fd, "wb") if fd is not None else None
                    try:
                        while remaining:
                            data = reader.get()
                            if len(data) != min(CHUNK, remaining):
                                raise IntegrityError("restore_file_frame_invalid")
                            digest.update(data)
                            if handle is not None:
                                handle.write(data)
                            remaining -= len(data)
                        if digest.hexdigest() != entry["sha256"]:
                            raise IntegrityError("restore_file_hash_invalid")
                        if handle is not None:
                            handle.flush()
                            os.fsync(handle.fileno())
                    finally:
                        if handle is not None:
                            handle.close()
                    sync_directory(path.parent)
                reader.finish(manifest["sha256"])
            # Normalize journal mode only in the fresh, authenticated restoration. Online
            # archives checkpoint WAL before capture; rebind must not leave a live WAL.
            from .backup_automation.online import restored_journals

            restored_journals(config)
            # Bytes matched their authenticated original hashes before operational changes.
            from .model_configuration.backup import rebind as rebind_models

            managed = rebind_models(config, manifest.get("model_secrets"))
            record = restore_rebind.prepare(config, manifest)
            write_private(
                destination / "config.test.json", canonical(manifest["storage_config"]).encode()
            )
            post = backup_preflight.inspect(
                config,
                limits=Limits(
                    entries=limits.entries + 16,
                    file_bytes=limits.file_bytes,
                    total_bytes=limits.total_bytes + 1024 * 1024,
                    depth=limits.depth,
                ),
            )
            result = {
                "state": "restored_locked",
                "bundle_id": marker["bundle_id"],
                "original_hashes_verified": True,
                "associations_verified": True,
                "restored_scope_sha256": post["sha256"],
                "quarantine": record["quarantine"],
                "automatic_start": False,
                "external_secrets": "reconfigure",
                "managed_model_credentials_restored": managed,
            }
            restore_guard.save(config.state_root, result)
            sync_directory(destination)
            sync_directory(destination.parent)
            return result
        except BaseException:
            if config is not None and config.state_root.is_dir():
                restore_guard.save(
                    config.state_root, {"state": "failed", "bundle_id": marker["bundle_id"]}
                )
            # Owned partial destination is retained privately for investigation, never activated.
            raise


def activate(config: Config, *, source_stopped: bool) -> dict[str, Any]:
    if source_stopped is not True:
        raise InvalidInput("restore_source_stop_confirmation_required")
    value = restore_guard.read(config.state_root)
    if value is None or value.get("state") != "restored_locked":
        raise InvalidInput("restore_activation_state_invalid")
    with backup_preflight.frozen(
        config, _after_validation=lambda: restore_guard.save(config.state_root, value)
    ):
        value.update(
            state="active", machine_id=restore_guard.machine_id(), source_stopped_confirmed=True
        )
    # Activation does not start services, release fees, enable clients or reconfigure secrets.
    return {
        "state": "active",
        "services_started": False,
        "clients_enabled": False,
        "original_hashes_verified": value["original_hashes_verified"],
    }


def retire(config: Config) -> dict[str, Any]:
    gate: dict[str, Any] = {}
    with backup_preflight.frozen(
        config, _after_validation=lambda: restore_guard.save(config.state_root, gate)
    ) as observed:
        digest = observed["sha256"]
        gate.update(state="retired", scope_sha256=digest, machine_id=restore_guard.machine_id())
    return {"state": "retired", "services_started": False, "scope_sha256": digest}
