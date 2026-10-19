#!/usr/bin/env python3
"""Plan and apply token-efficient MOC organization for Markdown knowledge bases."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


VERSION = 4
DEFAULT_PLAN = ".knowledge-organizer-plan.json"
DEFAULT_CONFIG_FILENAME = ".knowledge-organizer.json"
DEFAULT_POLICY_FILENAME = ".knowledge-organizer-policy.json"
GLOBAL_ACCESS_POLICY_ENV = "KNOWLEDGE_VAULT_ACCESS_POLICY"
GLOBAL_ACCESS_POLICY_RELATIVE = Path("knowledge-vault-access") / "policy.json"
GLOBAL_ACCESS_POLICY_REQUIRED_MARKER = "required"
BACKUP_DIR_NAME = ".knowledge-organizer-backup"
DEFAULT_CONFIG = {
    "profile": "auto",
    "include_globs": ["**/*.md", "*.md"],
    "exclude_globs": [
        ".git/**",
        ".obsidian/**",
        ".trash/**",
        "node_modules/**",
        "agent-skills/**",
        "**/.git/**",
        "**/.obsidian/**",
        "**/.trash/**",
        "**/node_modules/**",
        "**/agent-skills/**",
    ],
    "fields": {
        "type": "type",
        "tags": "tags",
        "aliases": "aliases",
        "hubs": "moc",
        "topics": "topics",
        "organization_status": "organization_status",
        "organization_version": "organization_version",
        "organization_hash": "organization_hash",
        "organization_candidates": "organization_candidates",
    },
    "para": {
        "inbox_names": ["Inbox"],
        "map_names": ["Map", "Maps", "MOC", "MOCs"],
        "support_names": ["Projects", "Areas", "Resources", "Archives"],
        "high_confidence": 0.85,
        "medium_confidence": 0.65,
    },
    "hub_detection": {
        "type_values": ["moc", "hub", "index", "map"],
        "filename_suffixes": ["MOC", "Index", "Map", "Hub"],
        "max_hubs_per_note": 3,
    },
    "matching": {
        "high_score": 70,
        "review_score": 45,
        "stop_topics": ["AI", "notes", "research", "general", "MOC", "hub", "index", "map"],
        "topic_aliases": {},
    },
    "rules": [],
    "maintenance": {
        "default_mode": "auto",
        "full_review_days": 30,
        "note_growth_count": 25,
        "note_growth_ratio": 0.10,
        "pending_inbox_count": 5,
    },
    "taxonomy": {
        "enabled": True,
        "cluster_min_notes": 3,
        "broad_moc_note_limit": 80,
        "max_proposals": 10,
        "evidence_chars": 360,
    },
    "state": {"path": ".knowledge-organizer-state.json"},
    "index": {"enabled": True, "path": ""},
    "output": {"max_items": 10},
}
DEFAULT_POLICY = {"schema_version": 1, "deny_paths": []}

WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
WIKI_EMBED_RE = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
INLINE_TAG_RE = re.compile(r"(?<![\w#`])#([^\W\d_][\w/-]*)")
TOP_FIELD_RE = re.compile(r"^([A-Za-z0-9_\-\u4e00-\u9fff]+):(?:\s|$)")
ASSET_EXTENSIONS = {
    ".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".pdf", ".png",
    ".svg", ".tif", ".tiff", ".webp",
}


@dataclass
class Note:
    path: Path
    relative: Path
    original: str
    frontmatter_text: str
    frontmatter: OrderedDict
    body: str
    had_frontmatter: bool
    cached_file_hash: Optional[str] = None
    cached_organization_hash: Optional[str] = None
    cached_links: Optional[list[str]] = None

    @property
    def title(self) -> str:
        value = self.frontmatter.get("title")
        return str(value).strip() if value else self.path.stem

    @property
    def file_hash(self) -> str:
        return self.cached_file_hash or digest(self.original)

    @property
    def outgoing_links(self) -> list[str]:
        if self.cached_links is not None:
            return self.cached_links
        clean = strip_code(self.body)
        targets = [value.strip() for value in WIKILINK_RE.findall(clean)]
        targets.extend(
            Path(clean_link_target(value)).stem
            for value in MARKDOWN_LINK_RE.findall(clean)
        )
        return unique(targets)


@dataclass
class Hub:
    note: Note
    topics: set[str]
    links: set[str]


@dataclass
class Detection:
    selected_profile: str
    candidate_profile: str
    confidence: str
    score: float
    inbox_dirs: list[Path]
    map_dirs: list[Path]
    support_roles: list[str]
    hub_count: int
    requires_confirmation: bool


@dataclass
class Policy:
    path: Path
    denied_roots: tuple[Path, ...]
    denied_values: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class GlobalAccessPolicy:
    path: Path
    protected_roots: tuple[Path, ...]
    protected_values: tuple[str, ...]
    deny_ancestor_scopes: tuple[bool, ...]
    digest: str


@dataclass
class MaintenanceDecision:
    mode: str
    requested: str
    triggers: list[str]
    snapshot: dict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    detect_parser = sub.add_parser("detect", help="Detect PARA/Map structure and selected profile.")
    add_common_arguments(detect_parser)
    detect_parser.add_argument("--json", action="store_true")

    audit_parser = sub.add_parser("audit", help="Read-only compact repository audit.")
    add_common_arguments(audit_parser)
    audit_parser.add_argument("--json", action="store_true")
    audit_parser.add_argument("--details", action="store_true")
    audit_parser.add_argument("--max-items", type=int)
    audit_parser.add_argument(
        "--link-scope", choices=("scoped", "vault"), default="scoped",
        help="Check links only in the selected maintenance scope unless vault is explicit.",
    )

    plan_parser = sub.add_parser("plan", help="Create a reusable organization plan.")
    add_common_arguments(plan_parser)
    add_plan_arguments(plan_parser)

    apply_parser = sub.add_parser("apply", help="Apply an existing plan with hash checks.")
    apply_parser.add_argument("--root", default=".")
    apply_parser.add_argument("--plan", default=DEFAULT_PLAN)
    apply_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the automatic pre-apply backup copy of affected notes.",
    )

    verify_parser = sub.add_parser("verify", help="Verify only files referenced by a plan.")
    verify_parser.add_argument("--root", default=".")
    verify_parser.add_argument("--plan", default=DEFAULT_PLAN)
    verify_parser.add_argument("--json", action="store_true")
    verify_parser.add_argument(
        "--taxonomy-reviewed",
        action="store_true",
        help="Confirm that full-review MOC create/split/merge proposals were semantically reviewed.",
    )
    diff_parser = sub.add_parser(
        "diff", help="Print a human-readable summary of an existing plan."
    )
    diff_parser.add_argument("--root", default=".")
    diff_parser.add_argument("--plan", default=DEFAULT_PLAN)
    diff_parser.add_argument(
        "--max-items", type=int, default=30,
        help="Maximum plan items to print.",
    )

    init_parser = sub.add_parser("init-config", help="Create a neutral V3 JSON config.")
    init_parser.add_argument("--root", default=".")
    init_parser.add_argument("--output", default=DEFAULT_CONFIG_FILENAME)
    init_parser.add_argument("--force", action="store_true")

    policy_parser = sub.add_parser(
        "init-policy", help="Create an automatically enforced protected-path policy."
    )
    policy_parser.add_argument("--root", default=".")
    policy_parser.add_argument("--output", default=DEFAULT_POLICY_FILENAME)
    policy_parser.add_argument("--deny", action="append", required=True)
    policy_parser.add_argument("--force", action="store_true")

    legacy = sub.add_parser("organize", help="Compatibility wrapper for plan/apply.")
    add_common_arguments(legacy)
    add_plan_arguments(legacy)
    legacy.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    access_policy = load_global_access_policy()
    root = lexical_absolute(args.root)
    ensure_scope_allowed(root, access_policy, "knowledge-base access")
    root = root.resolve()
    ensure_scope_allowed(root, access_policy, "knowledge-base access")

    if args.command == "init-config":
        output = resolve_path(root, args.output)
        ensure_scope_allowed(output, access_policy, "configuration write")
        return init_config(root, output, args.force)
    if args.command == "init-policy":
        output = resolve_path(root, args.output)
        ensure_scope_allowed(output, access_policy, "policy write")
        return init_policy(root, output, args.deny, args.force)

    policy = merge_global_protections(root, load_policy(root), access_policy)
    if args.command in {"apply", "verify", "diff"}:
        plan_path = resolve_path(root, args.plan)
        if args.command == "apply":
            return apply_plan(root, plan_path, policy, no_backup=args.no_backup)
        if args.command == "diff":
            return diff_plan(
                root,
                plan_path,
                policy,
                args.max_items if args.max_items is not None else 30,
            )
        return verify_plan(
            root,
            plan_path,
            policy,
            args.json,
            taxonomy_reviewed=args.taxonomy_reviewed,
        )

    config_path = resolve_config_file(root, args.config)
    config = load_config(config_path)
    detection = detect_profile(root, config, args.profile, policy)
    maintenance = select_maintenance_mode(root, config, detection, policy, args.mode)
    if args.command == "detect":
        payload = detection_dict(root, detection)
        payload["maintenance"] = maintenance_dict(maintenance)
        payload["config"] = display_path(root, config_path) if config_path else "defaults"
        payload["policy"] = display_path(root, policy.path) if policy.path.is_file() else "none"
        print(
            json.dumps(payload, ensure_ascii=False, indent=2)
            if args.json
            else format_detection(root, detection, maintenance, config_path, policy)
        )
        return 0
    if args.command == "audit":
        report = audit(
            root, config, detection, maintenance, policy, args.details,
            args.link_scope, effective_max_items(args.max_items, config),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_audit(report))
        return 0

    plan_path = resolve_path(root, args.output)
    ensure_not_protected(plan_path, policy, "plan output")
    plan = build_plan(root, config, detection, maintenance, policy, args.path)
    write_json_atomic(plan_path, plan)
    print(format_plan_summary(plan, plan_path, root))
    if args.command == "organize" and args.apply:
        return apply_plan(root, plan_path, policy)
    return 0


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".", help="Knowledge-base root.")
    parser.add_argument(
        "--config",
        help=f"Optional V3 JSON configuration; defaults to <root>/{DEFAULT_CONFIG_FILENAME}.",
    )
    parser.add_argument("--profile", choices=("auto", "para-inbox", "generic"), help="Override profile detection.")
    parser.add_argument(
        "--mode", choices=("auto", "incremental", "full-review", "bootstrap"),
        help="Maintenance mode; auto uses the verified repository baseline.",
    )


def add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", action="append", help="Relative target file or directory; repeatable.")
    parser.add_argument("--output", default=DEFAULT_PLAN, help="Reusable JSON plan path.")


def init_config(root: Path, output: Path, force: bool) -> int:
    if output.exists() and not force:
        print(f"Refusing to overwrite existing config: {output}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output, DEFAULT_CONFIG)
    print(f"Created: {display_path(root, output)}")
    return 0


def init_policy(root: Path, output: Path, denied: list[str], force: bool) -> int:
    if output.exists() and not force:
        print(f"Refusing to overwrite existing policy: {output}", file=sys.stderr)
        return 2
    values = []
    for value in denied:
        path = resolve_under_root(root, value)
        values.append(path.relative_to(root).as_posix())
    write_json_atomic(
        output,
        {
            "schema_version": 1,
            "deny_paths": [
                {"path": value, "deny": ["read", "index", "classify", "link", "write", "verify"]}
                for value in unique(values)
            ],
        },
    )
    print(f"Created: {display_path(root, output)}")
    return 0


def load_config(path: Optional[Path]) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        deep_merge(config, json.loads(path.read_text(encoding="utf-8")))
    return config


def lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def default_global_access_policy_path() -> Path:
    configured = os.environ.get(GLOBAL_ACCESS_POLICY_ENV)
    if configured:
        return lexical_absolute(configured)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / GLOBAL_ACCESS_POLICY_RELATIVE


def load_global_access_policy() -> GlobalAccessPolicy:
    policy_path = default_global_access_policy_path()
    if not policy_path.is_file():
        if policy_path.with_name(GLOBAL_ACCESS_POLICY_REQUIRED_MARKER).is_file():
            raise SystemExit(
                f"Required global vault access policy is missing: {policy_path}"
            )
        return GlobalAccessPolicy(policy_path, (), (), (), digest("{}"))
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Unable to read global vault access policy: {error}") from error
    if int(payload.get("schema_version", 0)) != 1:
        raise SystemExit(
            f"Unsupported global vault access policy version: "
            f"{payload.get('schema_version')}"
        )
    entries = payload.get("protected_roots", [])
    if not isinstance(entries, list):
        raise SystemExit("Global vault access policy protected_roots must be a list")
    roots: dict[str, tuple[Path, bool]] = {}
    for entry in entries:
        raw = str(entry.get("path") or "").strip() if isinstance(entry, dict) else str(entry).strip()
        if not raw:
            continue
        path = lexical_absolute(raw)
        deny_ancestor = (
            bool(entry.get("deny_ancestor_scopes", True))
            if isinstance(entry, dict)
            else True
        )
        roots[str(path)] = (path, deny_ancestor)
    values = tuple(sorted(roots))
    canonical = {
        "schema_version": 1,
        "protected_roots": [
            {
                "path": value,
                "deny_ancestor_scopes": roots[value][1],
            }
            for value in values
        ],
    }
    return GlobalAccessPolicy(
        policy_path,
        tuple(roots[value][0] for value in values),
        values,
        tuple(roots[value][1] for value in values),
        digest(json.dumps(canonical, ensure_ascii=False, sort_keys=True)),
    )


def ensure_scope_allowed(
    root: Path,
    policy: GlobalAccessPolicy,
    operation: str,
) -> None:
    candidate = lexical_absolute(root)
    for value, protected, deny_ancestor in zip(
        policy.protected_values,
        policy.protected_roots,
        policy.deny_ancestor_scopes,
    ):
        if (
            candidate == protected
            or protected in candidate.parents
            or (deny_ancestor and candidate in protected.parents)
        ):
            raise SystemExit(
                f"Global vault access policy denied {operation}: {value}"
            )
        canonical_candidate = candidate.resolve(strict=False)
        canonical_protected = protected.resolve(strict=False)
        if (
            canonical_candidate == canonical_protected
            or canonical_protected in canonical_candidate.parents
            or (
                deny_ancestor
                and canonical_candidate in canonical_protected.parents
            )
        ):
            raise SystemExit(
                f"Global vault access policy denied {operation}: {value}"
            )


def resolve_config_file(root: Path, explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit).expanduser()
        path = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
        if not path.is_file():
            raise SystemExit(f"Configuration file not found: {path}")
        return path
    candidate = root / DEFAULT_CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def load_policy(root: Path) -> Policy:
    root = root.resolve()
    policy_path = root / DEFAULT_POLICY_FILENAME
    data = json.loads(json.dumps(DEFAULT_POLICY))
    if policy_path.is_file():
        try:
            value = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"Unable to read protected-path policy: {error}") from error
        if int(value.get("schema_version", 0)) != 1:
            raise SystemExit(
                f"Unsupported protected-path policy version: {value.get('schema_version')}"
            )
        data = value
    denied_roots: list[Path] = []
    denied_values: list[str] = []
    for entry in data.get("deny_paths", []):
        raw = str(entry.get("path", "")) if isinstance(entry, dict) else str(entry)
        if not raw.strip():
            continue
        path = resolve_under_root(root, raw)
        if path == root:
            raise SystemExit("Protected-path policy cannot deny the repository root")
        relative = path.relative_to(root).as_posix()
        denied_roots.append(path)
        denied_values.append(relative)
    canonical = {"schema_version": 1, "deny_paths": sorted(unique(denied_values))}
    return Policy(
        policy_path,
        tuple(sorted(set(denied_roots), key=str)),
        tuple(canonical["deny_paths"]),
        digest(json.dumps(canonical, ensure_ascii=False, sort_keys=True)),
    )


def merge_global_protections(
    root: Path,
    policy: Policy,
    access_policy: GlobalAccessPolicy,
) -> Policy:
    root = root.resolve()
    entries = {
        str(path): (path, value)
        for value, path in zip(policy.denied_values, policy.denied_roots)
    }
    for index, protected in enumerate(access_policy.protected_roots, start=1):
        protected = protected.resolve(strict=False)
        if str(protected) in entries:
            continue
        try:
            label = protected.relative_to(root).as_posix()
        except ValueError:
            label = f"global-policy:{index}"
        entries[str(protected)] = (protected, label)
    ordered = [entries[key] for key in sorted(entries)]
    canonical = {
        "repository_policy": policy.digest,
        "global_policy": access_policy.digest,
        "protected": [
            {"path": str(path), "label": label}
            for path, label in ordered
        ],
    }
    return Policy(
        policy.path,
        tuple(path for path, _ in ordered),
        tuple(label for _, label in ordered),
        digest(json.dumps(canonical, ensure_ascii=False, sort_keys=True)),
    )


def is_protected(path: Path, policy: Policy) -> bool:
    resolved = path.resolve()
    return any(resolved == denied or denied in resolved.parents for denied in policy.denied_roots)


def ensure_not_protected(path: Path, policy: Policy, operation: str) -> None:
    resolved = path.resolve()
    for value, denied in zip(policy.denied_values, policy.denied_roots):
        if resolved == denied or denied in resolved.parents:
            raise SystemExit(f"Protected-path policy denied {operation}: {value}")


def state_path(root: Path, config: dict) -> Path:
    return resolve_under_root(root, str(config.get("state", {}).get("path") or ".knowledge-organizer-state.json"))


def load_state(root: Path, config: dict, policy: Policy) -> dict:
    path = state_path(root, config)
    ensure_not_protected(path, policy, "state read")
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if int(value.get("version", 0)) == VERSION else {}


def repository_snapshot(
    root: Path, config: dict, detection: Detection, policy: Policy
) -> dict:
    paths = collect_all_paths(root, config, policy)
    map_paths = collect_target_paths(root, detection.map_dirs, config, policy)
    map_signature = []
    for path in map_paths:
        stat = path.stat()
        map_signature.append(
            [path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size]
        )
    fields = config["fields"]
    pending = 0
    if detection.inbox_dirs:
        for note in load_notes_from_targets(root, detection.inbox_dirs, config, policy):
            mocs = normalize_links(note.frontmatter.get(fields["hubs"]))
            status = str(note.frontmatter.get(fields["organization_status"]) or "")
            if not mocs and status not in {"linked", "review", "unclassified"}:
                pending += 1
    return {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "eligible_note_count": len(paths),
        "map_digest": digest(json.dumps(map_signature, ensure_ascii=False)),
        "moc_count": detection.hub_count,
        "pending_inbox_count": pending,
    }


def select_maintenance_mode(
    root: Path,
    config: dict,
    detection: Detection,
    policy: Policy,
    requested_mode: Optional[str],
) -> MaintenanceDecision:
    requested = requested_mode or str(
        config.get("maintenance", {}).get("default_mode", "auto")
    )
    snapshot = repository_snapshot(root, config, detection, policy)
    if requested != "auto":
        return MaintenanceDecision(requested, requested, ["explicit mode"], snapshot)

    state = load_state(root, config, policy)
    if not state or not state.get("last_full_verified_at"):
        return MaintenanceDecision("bootstrap", "auto", ["no verified full baseline"], snapshot)

    settings = config.get("maintenance", {})
    triggers: list[str] = []
    baseline_count = int(state.get("eligible_note_count", 0))
    growth = snapshot["eligible_note_count"] - baseline_count
    ratio = growth / max(1, baseline_count)
    if growth >= int(settings.get("note_growth_count", 25)):
        triggers.append(f"note growth {growth}")
    if growth > 0 and ratio >= float(settings.get("note_growth_ratio", 0.10)):
        triggers.append(f"note growth ratio {ratio:.1%}")
    if snapshot["pending_inbox_count"] >= int(settings.get("pending_inbox_count", 5)):
        triggers.append(f"pending Inbox {snapshot['pending_inbox_count']}")
    if snapshot["map_digest"] != str(state.get("map_digest", "")):
        triggers.append("Map structure changed")

    verified_at = str(state.get("last_full_verified_at") or "")
    try:
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(verified_at)
        if age.days >= int(settings.get("full_review_days", 30)):
            triggers.append(f"full review age {age.days} days")
    except ValueError:
        triggers.append("invalid full-review timestamp")

    mode = "full-review" if triggers else "incremental"
    return MaintenanceDecision(mode, "auto", triggers or ["baseline current"], snapshot)


def maintenance_dict(decision: MaintenanceDecision) -> dict:
    return {
        "mode": decision.mode,
        "requested": decision.requested,
        "triggers": decision.triggers,
        "snapshot": decision.snapshot,
    }


def deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value


def detect_profile(
    root: Path, config: dict, override: Optional[str], policy: Policy
) -> Detection:
    para = config["para"]
    dirs = [
        path
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and not is_protected(path, policy)
    ]
    inbox_dirs = match_role_dirs(dirs, para["inbox_names"])
    map_dirs = match_role_dirs(dirs, para["map_names"])
    support_roles = [
        role for role in para["support_names"] if match_role_dirs(dirs, [role])
    ]
    map_notes = (
        load_notes_from_targets(root, map_dirs, config, policy) if map_dirs else []
    )
    hubs = discover_hubs(map_notes, config)

    score = 0.0
    score += 0.35 if inbox_dirs else 0.0
    score += 0.35 if map_dirs else 0.0
    score += min(len(support_roles), 2) * 0.10
    score += 0.10 if hubs else 0.0
    required = bool(inbox_dirs and map_dirs)
    high = float(para["high_confidence"])
    medium = float(para["medium_confidence"])
    confidence = "high" if required and score >= high else "medium" if required and score >= medium else "low"
    candidate = "para-inbox" if required and confidence in {"high", "medium"} else "generic"

    requested = override or str(config.get("profile", "auto"))
    if requested == "para-inbox" and not required:
        raise SystemExit("para-inbox requires both an Inbox directory and a Map/MOC directory")
    if requested in {"para-inbox", "generic"}:
        selected = requested
        requires_confirmation = False
    elif confidence == "high":
        selected = "para-inbox"
        requires_confirmation = False
    else:
        selected = "generic"
        requires_confirmation = confidence == "medium"
    return Detection(
        selected, candidate, confidence, round(score, 2), inbox_dirs, map_dirs,
        support_roles, len(hubs), requires_confirmation,
    )


def normalize_dir_name(name: str) -> str:
    value = name.casefold().strip()
    value = re.sub(r"^\d+[\s._-]*", "", value)
    return re.sub(r"[\s._-]+", "", value)


def match_role_dirs(dirs: list[Path], names: Iterable[str]) -> list[Path]:
    wanted = {normalize_dir_name(name) for name in names}
    return sorted(path for path in dirs if normalize_dir_name(path.name) in wanted)


def detection_dict(root: Path, detection: Detection) -> dict:
    return {
        "selected_profile": detection.selected_profile,
        "candidate_profile": detection.candidate_profile,
        "confidence": detection.confidence,
        "score": detection.score,
        "requires_confirmation": detection.requires_confirmation,
        "inbox_dirs": [display_path(root, path) for path in detection.inbox_dirs],
        "map_dirs": [display_path(root, path) for path in detection.map_dirs],
        "support_roles": detection.support_roles,
        "hubs": detection.hub_count,
    }


def format_detection(
    root: Path,
    detection: Detection,
    maintenance: MaintenanceDecision,
    config_path: Optional[Path],
    policy: Policy,
) -> str:
    data = detection_dict(root, detection)
    return "\n".join([
        f"Profile: {data['selected_profile']} (confidence: {data['confidence']}, score: {data['score']:.2f})",
        f"Maintenance: {maintenance.mode} ({'; '.join(maintenance.triggers)})",
        "Inbox: " + (", ".join(data["inbox_dirs"]) or "not found"),
        "Maps: " + (", ".join(data["map_dirs"]) or "not found"),
        f"PARA roles: {len(data['support_roles'])}; hubs: {data['hubs']}",
        "Config: " + (display_path(root, config_path) if config_path else "defaults"),
        "Policy: " + (
            f"{display_path(root, policy.path)}; denied={len(policy.denied_roots)}"
            if policy.path.is_file() else "none"
        ),
        "Confirmation required: yes" if data["requires_confirmation"] else "Confirmation required: no",
    ])


def load_notes(root: Path, config: dict, policy: Policy) -> list[Note]:
    return [parse_note(root, path) for path in collect_all_paths(root, config, policy)]


def load_notes_from_targets(
    root: Path, targets: Iterable[Path], config: dict, policy: Policy
) -> list[Note]:
    return [
        parse_note(root, path)
        for path in collect_target_paths(root, targets, config, policy)
    ]


def collect_all_paths(root: Path, config: dict, policy: Policy) -> list[Path]:
    return collect_files(root, [root], config, policy, {".md"})


def collect_target_paths(
    root: Path, targets: Iterable[Path], config: dict, policy: Policy
) -> list[Path]:
    return collect_files(root, targets, config, policy, {".md"})


def collect_asset_paths(root: Path, config: dict, policy: Policy) -> list[Path]:
    return collect_files(root, [root], config, policy, ASSET_EXTENSIONS, honor_includes=False)


def collect_files(
    root: Path,
    targets: Iterable[Path],
    config: dict,
    policy: Policy,
    suffixes: set[str],
    honor_includes: bool = True,
) -> list[Path]:
    root = root.resolve()
    paths: set[Path] = set()
    for target in targets:
        target = target.resolve()
        if target != root and root not in target.parents:
            continue
        if is_protected(target, policy):
            continue
        if target.is_file():
            if target.suffix.casefold() in suffixes and include_candidate(
                root, target, config, policy, honor_includes
            ):
                paths.add(target)
            continue
        if not target.is_dir():
            continue
        for current, dirnames, filenames in os.walk(target, topdown=True, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for name in dirnames:
                child = current_path / name
                if include_directory(root, child, config, policy):
                    kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                path = current_path / name
                if path.suffix.casefold() not in suffixes:
                    continue
                if include_candidate(root, path, config, policy, honor_includes):
                    paths.add(path)
    return sorted(paths)


def in_backup_dir(root: Path, path: Path) -> bool:
    try:
        return BACKUP_DIR_NAME in path.relative_to(root).parts
    except ValueError:
        return False


def include_directory(root: Path, path: Path, config: dict, policy: Policy) -> bool:
    if is_protected(path, policy) or in_backup_dir(root, path):
        return False
    return not excluded(root, path, config["exclude_globs"], directory=True)


def include_candidate(
    root: Path,
    path: Path,
    config: dict,
    policy: Policy,
    honor_includes: bool,
) -> bool:
    resolved = path.resolve()
    if (
        (resolved != root and root not in resolved.parents)
        or is_protected(path, policy)
        or in_backup_dir(root, path)
        or excluded(root, path, config["exclude_globs"])
    ):
        return False
    if not honor_includes:
        return True
    relative = path.relative_to(root).as_posix()
    return any(path_matches_pattern(relative, pattern) for pattern in config["include_globs"])


def default_index_path(root: Path) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "vault"
    identifier = digest(str(root))[:12]
    return base / "codex" / "knowledge-organizer" / f"{label}-{identifier}.json"


def resolve_index_file(root: Path, config: dict) -> Path:
    value = str(config.get("index", {}).get("path") or "").strip()
    return resolve_path(root, value) if value else default_index_path(root)


def load_plan_notes(
    root: Path, paths: list[Path], config: dict, policy: Policy
) -> tuple[list[Note], int, int]:
    index_config = config.get("index", {})
    if not index_config.get("enabled", True):
        notes = [parse_note(root, path) for path in paths]
        return notes, 0, len(notes)
    index_path = resolve_index_file(root, config)
    ensure_not_protected(index_path, policy, "index write")
    config_digest = digest(json.dumps({
        "fields": config["fields"],
        "hub_detection": config["hub_detection"],
        "matching": config["matching"],
        "rules": config.get("rules", []),
        "taxonomy": config.get("taxonomy", {}),
    }, ensure_ascii=False, sort_keys=True))
    cache = load_index(index_path, config_digest, policy.digest)
    entries: dict[str, dict] = {}
    notes: list[Note] = []
    hits = 0
    parsed = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        cached = cache.get("entries", {}).get(relative)
        if cached and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("size") == stat.st_size:
            note = note_from_index(root, path, cached)
            hits += 1
        else:
            note = parse_note(root, path)
            parsed += 1
        notes.append(note)
        entries[relative] = index_entry(note, stat, config["fields"])
    payload = {
        "version": VERSION,
        "config_digest": config_digest,
        "policy_digest": policy.digest,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "entries": entries,
    }
    comparable = {
        "version": cache.get("version"),
        "config_digest": cache.get("config_digest"),
        "policy_digest": cache.get("policy_digest"),
        "entries": cache.get("entries", {}),
    }
    if comparable != {key: payload[key] for key in comparable}:
        try:
            write_json_atomic(index_path, payload)
        except OSError:
            pass
    return notes, hits, parsed


def load_index(path: Path, config_digest: str, policy_digest: str) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if (
        int(value.get("version", 0)) != VERSION
        or value.get("config_digest") != config_digest
        or value.get("policy_digest") != policy_digest
    ):
        return {}
    return value


def index_entry(note: Note, stat, fields: dict) -> dict:
    return {
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "frontmatter": note.frontmatter,
        "had_frontmatter": note.had_frontmatter,
        "body_prefix": note.body[:1200],
        "file_hash": note.file_hash,
        "organization_hash": organization_hash(note, fields),
        "links": note.outgoing_links,
    }


def note_from_index(root: Path, path: Path, entry: dict) -> Note:
    return Note(
        path=path,
        relative=path.relative_to(root),
        original="",
        frontmatter_text="",
        frontmatter=OrderedDict(entry.get("frontmatter", {})),
        body=str(entry.get("body_prefix") or ""),
        had_frontmatter=bool(entry.get("had_frontmatter")),
        cached_file_hash=str(entry.get("file_hash") or ""),
        cached_organization_hash=str(entry.get("organization_hash") or ""),
        cached_links=[str(value) for value in entry.get("links", [])],
    )


def path_matches_pattern(relative: str, pattern: str) -> bool:
    normalized = pattern.strip().lstrip("/")
    if fnmatch.fnmatch(relative, normalized):
        return True
    if normalized.endswith("/**"):
        base = normalized[:-3].rstrip("/")
        return fnmatch.fnmatch(relative, base) or fnmatch.fnmatch(
            relative + "/", base + "/"
        )
    return False


def excluded(
    root: Path, path: Path, patterns: Iterable[str], directory: bool = False
) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return True
    if rel == ".":
        return False
    return any(path_matches_pattern(rel, pattern) for pattern in patterns)


def parse_note(root: Path, path: Path) -> Note:
    text = path.read_text(encoding="utf-8", errors="ignore")
    frontmatter_text, body, had_frontmatter = split_document(text)
    return Note(
        path=path,
        relative=path.relative_to(root),
        original=text,
        frontmatter_text=frontmatter_text,
        frontmatter=parse_frontmatter(frontmatter_text),
        body=body,
        had_frontmatter=had_frontmatter,
    )


def split_document(text: str) -> tuple[str, str, bool]:
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return "", text, False
    match = re.search(r"\A---\r?\n(.*?)\r?\n---\r?\n?", text, flags=re.DOTALL)
    if not match:
        return "", text, False
    return match.group(1), text[match.end():], True


def parse_frontmatter(text: str) -> OrderedDict:
    data: OrderedDict[str, object] = OrderedDict()
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = TOP_FIELD_RE.match(lines[index])
        if not match:
            index += 1
            continue
        key = match.group(1)
        raw = lines[index].split(":", 1)[1].strip()
        if raw:
            data[key] = parse_scalar_or_inline_list(raw)
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            item = re.match(r"^\s+-\s*(.*)$", lines[cursor])
            if not item:
                break
            values.append(unquote(item.group(1).strip()))
            cursor += 1
        data[key] = values if values else ""
        index = cursor
    return data


def parse_scalar_or_inline_list(raw: str) -> object:
    if raw.startswith("[") and raw.endswith("]") and not raw.startswith("[["):
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return [str(item) for item in value]
        except json.JSONDecodeError:
            return [unquote(item.strip()) for item in raw[1:-1].split(",") if item.strip()]
    return unquote(raw)


def build_plan(
    root: Path,
    config: dict,
    detection: Detection,
    maintenance: MaintenanceDecision,
    policy: Policy,
    requested: list[str] | None,
) -> dict:
    full_scope = maintenance.mode in {"bootstrap", "full-review"}
    if detection.selected_profile == "para-inbox":
        hub_paths_for_load = collect_target_paths(
            root, detection.map_dirs, config, policy
        )
        if requested:
            requested_targets = []
            for value in requested:
                target = resolve_under_root(root, value)
                ensure_not_protected(target, policy, "target selection")
                requested_targets.append(target)
            target_paths = collect_target_paths(
                root, requested_targets, config, policy
            )
        else:
            target_paths = (
                collect_all_paths(root, config, policy)
                if full_scope
                else collect_target_paths(
                    root, detection.inbox_dirs, config, policy
                )
            )
        all_paths = sorted(set(target_paths + hub_paths_for_load))
        all_notes, cache_hits, parsed_count = load_plan_notes(
            root, all_paths, config, policy
        )
        target_path_set = set(target_paths)
        hub_path_set = set(hub_paths_for_load)
        target_notes = [note for note in all_notes if note.path in target_path_set]
        hub_notes = [note for note in all_notes if note.path in hub_path_set]
    else:
        all_paths = collect_all_paths(root, config, policy)
        all_notes, cache_hits, parsed_count = load_plan_notes(
            root, all_paths, config, policy
        )
        hub_notes = all_notes
        target_notes = all_notes
    hubs = discover_hubs(hub_notes, config)
    hub_paths = {hub.note.path for hub in hubs}
    target_notes = select_targets(root, target_notes, requested, policy)
    known_hubs = {
        f"[[{hub.note.path.stem}]]": hub.note.relative.as_posix() for hub in hubs
    }
    plan_base = {
        "version": VERSION,
        "root": str(root),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": detection_dict(root, detection),
        "maintenance": maintenance_dict(maintenance),
        "policy_digest": policy.digest,
        "protected_paths": list(policy.denied_values),
        "config_digest": digest(
            json.dumps(config, ensure_ascii=False, sort_keys=True)
        ),
        "fields": config["fields"],
        "known_hubs": known_hubs,
        "state_path": display_path(root, state_path(root, config)),
    }

    if not hubs:
        return {
            **plan_base,
            "summary": {
                "profile": detection.selected_profile,
                "confidence": detection.confidence,
                "mode": maintenance.mode,
                "scanned": len(target_notes),
                "hubs": 0,
                "skipped": len(target_notes),
                "linked": 0,
                "review": 0,
                "unclassified": 0,
                "skip_reasons": {"no_hubs": len(target_notes)},
                "blocked": "no hubs discovered; create hubs or configure hub detection before apply",
                "cache_hits": cache_hits,
                "parsed": parsed_count,
            },
            "taxonomy": empty_taxonomy_report(),
            "items": [],
        }

    fields = config["fields"]
    compiled_rules = compile_rules(config.get("rules", []))
    max_candidates = int(config["hub_detection"]["max_hubs_per_note"])
    items: list[dict] = []
    skipped = Counter()
    status_counts = Counter()
    for note in target_notes:
        if note.path in hub_paths:
            skipped["hub"] += 1
            continue
        mocs = normalize_links(note.frontmatter.get(fields["hubs"]))
        status = str(note.frontmatter.get(fields["organization_status"]) or "")
        saved_version = str(
            note.frontmatter.get(fields["organization_version"]) or ""
        )
        saved_hash = str(note.frontmatter.get(fields["organization_hash"]) or "")
        current_hash = organization_hash(note, fields)
        if mocs and not status:
            skipped["existing_moc"] += 1
            continue
        if (
            saved_version == str(VERSION)
            and status in {"linked", "review", "unclassified"}
            and saved_hash == current_hash
        ):
            skipped["unchanged"] += 1
            continue

        candidates = infer_hubs(note, hubs, config, compiled_rules)
        high_score = int(config["matching"]["high_score"])
        review_score = int(config["matching"]["review_score"])
        high = [candidate for candidate in candidates if candidate["score"] >= high_score]
        if high:
            selected = high[: int(config["hub_detection"]["max_hubs_per_note"])]
            action = "link"
            target_status = "linked"
            target_hubs = [candidate["link"] for candidate in selected]
        elif candidates and candidates[0]["score"] >= review_score:
            action = "review"
            target_status = "review"
            target_hubs = []
        else:
            action = "mark"
            target_status = "unclassified"
            target_hubs = []
        status_counts[target_status] += 1
        items.append({
            "path": note.relative.as_posix(),
            "expected_file_hash": note.file_hash,
            "organization_hash": current_hash,
            "action": action,
            "organization_status": target_status,
            "hubs": target_hubs,
            "candidate_hubs": (
                [candidate["link"] for candidate in candidates[:max_candidates]]
                if target_status != "linked"
                else []
            ),
            "candidates": candidates[:5],
            "review_context": (
                compact_note_context(note, config)
                if target_status in {"review", "unclassified"} else None
            ),
        })

    projected_mocs = {
        item["path"]: item.get("hubs", [])
        for item in items
        if item["action"] == "link"
    }
    taxonomy = (
        build_taxonomy_report(
            root, all_notes, hubs, config, policy, projected_mocs
        )
        if full_scope and config.get("taxonomy", {}).get("enabled", True)
        else empty_taxonomy_report()
    )
    summary = {
        "profile": detection.selected_profile,
        "confidence": detection.confidence,
        "mode": maintenance.mode,
        "scanned": len(target_notes),
        "hubs": len(hubs),
        "skipped": sum(skipped.values()),
        "linked": status_counts["linked"],
        "review": status_counts["review"],
        "unclassified": status_counts["unclassified"],
        "skip_reasons": dict(skipped),
        "cache_hits": cache_hits,
        "parsed": parsed_count,
        "taxonomy_create": len(taxonomy["create_candidates"]),
        "taxonomy_split": len(taxonomy["split_candidates"]),
        "taxonomy_merge": len(taxonomy["merge_candidates"]),
    }
    return {
        **plan_base,
        "summary": summary,
        "taxonomy": taxonomy,
        "items": items,
    }


def select_targets(
    root: Path,
    notes: list[Note],
    requested: list[str] | None,
    policy: Policy,
) -> list[Note]:
    if not requested:
        return notes
    targets = []
    for value in requested:
        target = resolve_under_root(root, value)
        ensure_not_protected(target, policy, "target selection")
        targets.append(target)
    return [note for note in notes if any(note.path == target or target in note.path.parents for target in targets)]


def discover_hubs(notes: list[Note], config: dict) -> list[Hub]:
    fields = config["fields"]
    detection = config["hub_detection"]
    type_values = {str(value).casefold() for value in detection["type_values"]}
    suffixes = tuple(str(value).casefold() for value in detection["filename_suffixes"])
    hubs: list[Hub] = []
    for note in notes:
        note_type = str(note.frontmatter.get(fields["type"]) or "").casefold()
        is_hub = note_type in type_values or (not note_type and note.path.stem.casefold().endswith(suffixes))
        if not is_hub:
            continue
        topics = set()
        for value in (
            normalize_list(note.frontmatter.get(fields["tags"]))
            + normalize_list(note.frontmatter.get(fields["topics"]))
            + normalize_list(note.frontmatter.get(fields.get("aliases", "aliases")))
        ):
            topics.update(topic_variants(value))
        cleaned = clean_hub_title(note.path.stem, detection["filename_suffixes"])
        topics.update(topic_variants(cleaned))
        hubs.append(Hub(note, {topic.casefold() for topic in topics if topic}, {link.casefold() for link in note.outgoing_links}))
    return hubs


def clean_hub_title(title: str, suffixes: Iterable[str]) -> str:
    value = title
    for suffix in suffixes:
        value = re.sub(rf"\s*{re.escape(str(suffix))}\s*$", "", value, flags=re.IGNORECASE)
    return value.strip()


def topic_variants(value: str) -> set[str]:
    value = str(value).strip().lstrip("#")
    if not value:
        return set()
    parts = [item.strip() for item in re.split(r"[/|,，、]", value) if item.strip()]
    return {value, *parts}


def inline_tags(note: Note) -> list[str]:
    body = note.body
    if not body and note.path.is_file():
        _, body, _ = split_document(
            note.path.read_text(encoding="utf-8", errors="ignore")
        )
    return INLINE_TAG_RE.findall(strip_code(body))


def effective_tags(note: Note, fields: dict) -> list[str]:
    return unique(
        normalize_list(note.frontmatter.get(fields["tags"])) + inline_tags(note)
    )


def infer_hubs(note: Note, hubs: list[Hub], config: dict, rules: list[dict]) -> list[dict]:
    fields = config["fields"]
    tags = {tag.casefold() for tag in effective_tags(note, fields)}
    expanded_tags = set(tags)
    for tag in list(tags):
        expanded_tags.update(part.casefold() for part in topic_variants(tag))
    aliases = {
        str(key).casefold(): normalize_list(value)
        for key, value in config["matching"].get("topic_aliases", {}).items()
    }
    for tag in list(expanded_tags):
        expanded_tags.update(value.casefold() for value in aliases.get(tag, []))
    title = f"{note.title} {note.path.stem}".casefold()
    body = note.body[:1200].casefold()
    stop = {str(value).casefold() for value in config["matching"].get("stop_topics", [])}
    scores: dict[str, dict] = {}

    hub_by_title = {hub.note.path.stem.casefold(): hub for hub in hubs}
    for rule in rules:
        if expanded_tags.intersection(rule["exclude_tags"]) or any(
            keyword in title or keyword in body for keyword in rule["exclude_keywords"]
        ):
            continue
        checks = []
        if rule["tags_any"]:
            checks.append(bool(expanded_tags.intersection(rule["tags_any"])))
        if rule["tags_all"]:
            checks.append(rule["tags_all"].issubset(expanded_tags))
        if rule["tag_prefixes"]:
            checks.append(
                any(
                    tag.startswith(prefix)
                    for tag in expanded_tags
                    for prefix in rule["tag_prefixes"]
                )
            )
        if rule["title_any"]:
            checks.append(any(keyword in title for keyword in rule["title_any"]))
        if rule["body_any"]:
            checks.append(any(keyword in body for keyword in rule["body_any"]))
        matched = (
            all(checks) if rule["match"] == "all" else any(checks)
        ) if checks else False
        if not matched:
            continue
        for hub_name in rule["hubs"]:
            hub = hub_by_title.get(hub_name.casefold())
            if hub:
                reason = "explicit rule"
                if rule["id"]:
                    reason += f": {rule['id']}"
                add_candidate(scores, hub, rule["score"], reason)

    for hub in hubs:
        if note.path.stem.casefold() in hub.links or note.title.casefold() in hub.links:
            add_candidate(scores, hub, 95, "already linked from MOC")
        exact = sorted((hub.topics - stop).intersection(expanded_tags))
        if exact:
            add_candidate(scores, hub, 70 + min(len(exact) - 1, 2) * 5, "exact tag: " + ", ".join(exact[:3]))
        hierarchical = sorted(
            topic for topic in hub.topics - stop
            if any(tag.startswith(topic + "/") or topic.startswith(tag + "/") for tag in tags)
        )
        if hierarchical:
            add_candidate(scores, hub, 55, "hierarchical tag: " + ", ".join(hierarchical[:3]))
        title_topics = sorted(topic for topic in hub.topics - stop if len(topic) >= 2 and topic in title)
        if title_topics:
            add_candidate(scores, hub, 45, "title topic: " + ", ".join(title_topics[:3]))
    return sorted(scores.values(), key=lambda item: (-item["score"], item["hub"]))


def compile_rules(rules: Iterable[dict]) -> list[dict]:
    return [{
        "id": str(rule.get("id") or ""),
        "tags_any": {str(value).casefold() for value in rule.get("tags_any", [])},
        "tags_all": {str(value).casefold() for value in rule.get("tags_all", [])},
        "tag_prefixes": [str(value).casefold() for value in rule.get("tag_prefixes", [])],
        "title_any": [
            str(value).casefold()
            for value in [*rule.get("keywords_any", []), *rule.get("title_any", [])]
        ],
        "body_any": [str(value).casefold() for value in rule.get("body_any", [])],
        "exclude_tags": {
            str(value).casefold() for value in rule.get("exclude_tags", [])
        },
        "exclude_keywords": [
            str(value).casefold() for value in rule.get("exclude_keywords", [])
        ],
        "match": "all" if str(rule.get("match", "any")).casefold() == "all" else "any",
        "score": max(1, min(100, int(rule.get("score", 100)))),
        "hubs": [str(value) for value in rule.get("hubs", [])],
    } for rule in rules]


def add_candidate(scores: dict[str, dict], hub: Hub, score: int, reason: str) -> None:
    key = hub.note.path.stem
    current = scores.setdefault(key, {"hub": key, "link": f"[[{key}]]", "score": 0, "reasons": []})
    current["score"] = max(current["score"], score)
    if reason not in current["reasons"]:
        current["reasons"].append(reason)


def empty_taxonomy_report() -> dict:
    return {
        "requires_review": False,
        "create_candidates": [],
        "split_candidates": [],
        "merge_candidates": [],
        "unclassified_pool": [],
    }


def compact_note_context(note: Note, config: dict) -> dict:
    body = note.body
    if not body and note.path.is_file():
        text = note.path.read_text(encoding="utf-8", errors="ignore")
        _, body, _ = split_document(text)
    cleaned = strip_code(body)
    cleaned = WIKI_EMBED_RE.sub(" ", cleaned)
    cleaned = MARKDOWN_IMAGE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    headings = re.findall(r"^#{1,3}\s+(.+)$", body, flags=re.MULTILINE)
    limit = max(120, int(config.get("taxonomy", {}).get("evidence_chars", 360)))
    fields = config["fields"]
    return {
        "title": note.title,
        "tags": normalize_list(note.frontmatter.get(fields["tags"])),
        "headings": headings[:4],
        "excerpt": cleaned[:limit],
    }


def taxonomy_terms(note: Note, config: dict) -> set[str]:
    fields = config["fields"]
    stop = {
        str(value).casefold()
        for value in config["matching"].get("stop_topics", [])
    }
    aliases = {
        str(key).casefold(): normalize_list(value)
        for key, value in config["matching"].get("topic_aliases", {}).items()
    }
    terms: set[str] = set()
    for raw in (
        normalize_list(note.frontmatter.get(fields["tags"]))
        + normalize_list(note.frontmatter.get(fields["topics"]))
        + inline_tags(note)
    ):
        for value in topic_variants(raw):
            term = value.casefold().strip()
            if 2 <= len(term) <= 40 and term not in stop:
                terms.add(term)
                terms.update(alias.casefold() for alias in aliases.get(term, []))
    return terms


def normalize_moc_name(value: str) -> str:
    return value.strip().removeprefix("[[").removesuffix("]]").casefold()


def build_taxonomy_report(
    root: Path,
    notes: list[Note],
    hubs: list[Hub],
    config: dict,
    policy: Policy,
    projected_mocs: Optional[dict[str, list[str]]] = None,
) -> dict:
    settings = config.get("taxonomy", {})
    minimum = max(2, int(settings.get("cluster_min_notes", 3)))
    broad_limit = max(minimum, int(settings.get("broad_moc_note_limit", 80)))
    limit = max(1, int(settings.get("max_proposals", 10)))
    fields = config["fields"]
    hub_paths = {hub.note.path for hub in hubs}
    ordinary = [note for note in notes if note.path not in hub_paths]
    projected_mocs = projected_mocs or {}
    notes_by_path = {
        note.relative.as_posix(): note
        for note in ordinary
    }
    by_title: dict[str, Note] = {}
    for note in ordinary:
        by_title[note.path.stem.casefold()] = note
        by_title[note.title.casefold()] = note

    members: dict[str, set[str]] = {
        hub.note.path.stem.casefold(): set() for hub in hubs
    }
    hub_display_names = {
        hub.note.path.stem.casefold(): hub.note.path.stem for hub in hubs
    }
    for note in ordinary:
        note_mocs = unique(
            normalize_links(note.frontmatter.get(fields["hubs"]))
            + normalize_links(projected_mocs.get(note.relative.as_posix(), []))
        )
        for value in note_mocs:
            key = normalize_moc_name(value)
            if key in members:
                members[key].add(note.relative.as_posix())
    for hub in hubs:
        key = hub.note.path.stem.casefold()
        for linked in hub.links:
            target = by_title.get(Path(linked).stem.casefold())
            if target:
                members[key].add(target.relative.as_posix())

    existing_topics = set().union(*(hub.topics for hub in hubs)) if hubs else set()
    unmapped = [
        note for note in ordinary
        if not normalize_links(note.frontmatter.get(fields["hubs"]))
        and not normalize_links(projected_mocs.get(note.relative.as_posix(), []))
    ]
    term_members: defaultdict[str, list[Note]] = defaultdict(list)
    for note in unmapped:
        for term in taxonomy_terms(note, config):
            if term not in existing_topics:
                term_members[term].append(note)

    create_candidates = []
    occupied: set[str] = set()
    compiled_rules = compile_rules(config.get("rules", []))
    for term, grouped in sorted(
        term_members.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        paths = {note.relative.as_posix() for note in grouped}
        if len(paths) < minimum:
            continue
        if occupied and len(paths & occupied) / len(paths) >= 0.75:
            continue
        selected = sorted(grouped, key=lambda note: note.relative.as_posix())[:8]
        parent_counts: Counter[str] = Counter()
        for note in selected:
            for candidate in infer_hubs(note, hubs, config, compiled_rules)[:2]:
                parent_counts[candidate["hub"]] += 1
        create_candidates.append({
            "action": "propose_create",
            "name": f"{term} MOC",
            "reason": f"{len(paths)} unmapped notes share topic '{term}'",
            "confidence": "high" if len(paths) >= max(5, minimum + 2) else "medium",
            "parent_candidates": [
                name for name, _ in parent_counts.most_common(2)
            ],
            "scope": {
                "include_topics": [term],
                "exclude_topics": [],
            },
            "seed_notes": [note.relative.as_posix() for note in selected],
            "evidence": [compact_note_context(note, config) for note in selected[:3]],
        })
        occupied.update(paths)
        if len(create_candidates) >= limit:
            break

    split_candidates = []
    for hub in hubs:
        key = hub.note.path.stem.casefold()
        paths = members.get(key, set())
        if len(paths) < broad_limit:
            continue
        topic_counts: Counter[str] = Counter()
        for path_value in paths:
            note = notes_by_path.get(path_value)
            if note:
                topic_counts.update(taxonomy_terms(note, config) - hub.topics)
        clusters = [
            {"topic": topic, "notes": count}
            for topic, count in topic_counts.most_common(8)
            if count >= minimum
        ]
        split_candidates.append({
            "action": "propose_split",
            "moc": hub.note.path.stem,
            "member_count": len(paths),
            "candidate_subtopics": clusters,
        })
        if len(split_candidates) >= limit:
            break

    merge_candidates = []
    hub_keys = sorted(members)
    for index, left in enumerate(hub_keys):
        for right in hub_keys[index + 1:]:
            intersection = members[left] & members[right]
            union = members[left] | members[right]
            if len(intersection) < minimum or not union:
                continue
            overlap = len(intersection) / len(union)
            if overlap >= 0.70:
                merge_candidates.append({
                    "action": "propose_merge",
                    "mocs": [
                        hub_display_names.get(left, left),
                        hub_display_names.get(right, right),
                    ],
                    "overlap": round(overlap, 3),
                    "shared_notes": len(intersection),
                })
    merge_candidates.sort(key=lambda item: (-item["overlap"], item["mocs"]))

    proposed_paths = {
        path
        for candidate in create_candidates
        for path in candidate["seed_notes"]
    }
    unclassified_pool = [
        {
            "path": note.relative.as_posix(),
            **compact_note_context(note, config),
        }
        for note in sorted(unmapped, key=lambda item: item.relative.as_posix())
        if note.relative.as_posix() not in proposed_paths
        and not is_protected(note.path, policy)
    ][:limit]
    result = {
        "create_candidates": create_candidates,
        "split_candidates": split_candidates,
        "merge_candidates": merge_candidates[:limit],
        "unclassified_pool": unclassified_pool,
    }
    result["requires_review"] = any(
        result[key]
        for key in ("create_candidates", "split_candidates", "merge_candidates")
    )
    return result


def organization_hash(note: Note, fields: dict) -> str:
    if note.cached_organization_hash:
        return note.cached_organization_hash
    payload = {
        "title": note.title,
        "tags": normalize_list(note.frontmatter.get(fields["tags"])),
        "aliases": normalize_list(
            note.frontmatter.get(fields.get("aliases", "aliases"))
        ),
        "topics": normalize_list(note.frontmatter.get(fields["topics"])),
    }
    return digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def apply_plan(root: Path, plan_path: Path, policy: Policy, no_backup: bool = False) -> int:
    ensure_not_protected(plan_path, policy, "plan read")
    plan = read_plan(root, plan_path, policy)
    if plan.get("summary", {}).get("blocked"):
        print("Refusing blocked plan: " + plan["summary"]["blocked"], file=sys.stderr)
        return 2
    fields = plan.get("fields", DEFAULT_CONFIG["fields"])
    updated = Counter()
    conflicts: list[str] = []
    pending: list[tuple[Path, str, str]] = []
    known_hubs = plan.get("known_hubs", {})
    for item in plan.get("items", []):
        path = resolve_under_root(root, item["path"])
        ensure_not_protected(path, policy, "note write")
        if not path.is_file():
            conflicts.append(f"{item['path']}: missing")
            continue
        note = parse_note(root, path)
        if note.file_hash != item["expected_file_hash"]:
            conflicts.append(f"{item['path']}: changed since plan")
            continue
        updates: dict[str, object] = {
            fields["organization_status"]: item["organization_status"],
            fields["organization_version"]: str(VERSION),
            fields["organization_hash"]: item["organization_hash"],
        }
        candidate_field = fields.get("organization_candidates")
        if candidate_field:
            updates[candidate_field] = list(item.get("candidate_hubs") or [])
        if item["action"] == "link":
            for link in item.get("hubs", []):
                hub_value = known_hubs.get(link)
                if not hub_value:
                    conflicts.append(f"{item['path']}: unknown hub {link}")
                    continue
                hub_path = resolve_under_root(root, hub_value)
                ensure_not_protected(hub_path, policy, "hub validation")
                if not hub_path.is_file():
                    conflicts.append(f"{item['path']}: missing hub {link}")
            existing = normalize_links(note.frontmatter.get(fields["hubs"]))
            updates[fields["hubs"]] = unique(existing + item.get("hubs", []))
        rendered = update_document(note, updates)
        if rendered != note.original:
            pending.append((path, rendered, item["organization_status"]))

    if conflicts:
        print("Apply summary")
        print("Updated: 0; linked: 0; review: 0; unclassified: 0")
        print(f"Conflicts: {len(conflicts)}")
        for conflict in conflicts[:10]:
            print(f"- {conflict}")
        print("No files were changed because the plan failed preflight.", file=sys.stderr)
        return 3

    try:
        preflight_write_access(path for path, _, _ in pending)
    except OSError as error:
        print(f"Write preflight failed: {error}", file=sys.stderr)
        print("No files were changed. Grant write access to the repository and rerun apply.", file=sys.stderr)
        return 5

    backup_path: Optional[Path] = None
    if pending and not no_backup:
        backup_path = backup_pending(root, pending, policy)

    for path, rendered, status in pending:
        atomic_write_text(path, rendered)
        updated[status] += 1
    print("Apply summary")
    print(f"Updated: {sum(updated.values())}; linked: {updated['linked']}; review: {updated['review']}; unclassified: {updated['unclassified']}")
    print(f"Conflicts: {len(conflicts)}")
    for conflict in conflicts[:10]:
        print(f"- {conflict}")
    if backup_path:
        print(f"Backup: {display_path(root, backup_path)}")
    elif no_backup:
        print("Backup: disabled (--no-backup)")
    return 0


def preflight_write_access(paths: Iterable[Path]) -> None:
    """Verify every destination directory before changing any note."""
    parents = sorted({path.parent for path in paths})
    for parent in parents:
        probe_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".kb-organizer-probe-", dir=parent, delete=False) as probe:
                probe_path = Path(probe.name)
                probe.write(b"ok")
                probe.flush()
                os.fsync(probe.fileno())
        finally:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)


def backup_pending(
    root: Path, pending: list[tuple[Path, str, str]], policy: Policy
) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = root / BACKUP_DIR_NAME / stamp
    counter = 0
    while base.exists():
        counter += 1
        base = root / BACKUP_DIR_NAME / f"{stamp}-{counter}"
    for path, _, _ in pending:
        target = base / path.relative_to(root)
        ensure_not_protected(target, policy, "backup write")
    base.mkdir(parents=True, exist_ok=True)
    for path, _, _ in pending:
        target = base / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return base


def atomic_write_text(path: Path, content: str) -> None:
    """Replace one note atomically while preserving its permission bits."""
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(path.stat().st_mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_plan(
    root: Path,
    plan_path: Path,
    policy: Policy,
    as_json: bool,
    taxonomy_reviewed: bool = False,
) -> int:
    ensure_not_protected(plan_path, policy, "plan read")
    plan = read_plan(root, plan_path, policy)
    fields = plan.get("fields", DEFAULT_CONFIG["fields"])
    failures: list[dict] = []
    checked = 0
    for item in plan.get("items", []):
        path = resolve_under_root(root, item["path"])
        ensure_not_protected(path, policy, "note verify")
        if not path.is_file():
            failures.append({"path": item["path"], "reason": "missing"})
            continue
        checked += 1
        note = parse_note(root, path)
        status = str(note.frontmatter.get(fields["organization_status"]) or "")
        saved_hash = str(note.frontmatter.get(fields["organization_hash"]) or "")
        mocs = set(normalize_links(note.frontmatter.get(fields["hubs"])))
        reasons = []
        if status != item["organization_status"]:
            reasons.append("status")
        if saved_hash != item["organization_hash"] or organization_hash(note, fields) != item["organization_hash"]:
            reasons.append("hash")
        if item["action"] == "link" and not set(item.get("hubs", [])).issubset(mocs):
            reasons.append("moc")
        if item.get("candidate_hubs"):
            saved_candidates = set(
                normalize_links(
                    note.frontmatter.get(
                        fields.get("organization_candidates", "organization_candidates")
                    )
                )
            )
            if not set(item["candidate_hubs"]).issubset(saved_candidates):
                reasons.append("candidates")
        if reasons:
            failures.append({"path": item["path"], "reason": ", ".join(reasons)})
    taxonomy_pending = bool(
        plan.get("taxonomy", {}).get("requires_review")
        and plan.get("maintenance", {}).get("mode") in {"bootstrap", "full-review"}
        and not taxonomy_reviewed
    )
    state_updated = False
    baseline_updated = False
    if (
        not failures
        and not plan.get("summary", {}).get("blocked")
        and not taxonomy_pending
    ):
        update_verified_state(root, plan, policy)
        state_updated = True
        baseline_updated = (
            plan.get("maintenance", {}).get("mode")
            in {"bootstrap", "full-review"}
        )
    report = {
        "checked": checked,
        "passed": checked - len(failures),
        "failed": len(failures),
        "failures": failures,
        "taxonomy_review_required": taxonomy_pending,
        "state_updated": state_updated,
        "baseline_updated": baseline_updated,
    }
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Verified: {report['passed']}/{checked}; failed: {report['failed']}")
        if taxonomy_pending:
            print(
                "Baseline not updated: review the taxonomy proposals, then rerun "
                "verify with --taxonomy-reviewed."
            )
    if failures:
        return 4
    return 6 if taxonomy_pending else 0


def update_verified_state(root: Path, plan: dict, policy: Policy) -> None:
    value = str(plan.get("state_path") or ".knowledge-organizer-state.json")
    path = resolve_under_root(root, value)
    ensure_not_protected(path, policy, "state write")
    current = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    mode = str(plan.get("maintenance", {}).get("mode") or "")
    snapshot = plan.get("maintenance", {}).get("snapshot", {})
    state = dict(current)
    state["version"] = VERSION
    state["last_verified_at"] = now
    state["last_mode"] = mode
    if mode in {"bootstrap", "full-review"}:
        state.update({
            "last_full_verified_at": now,
            "eligible_note_count": int(snapshot.get("eligible_note_count", 0)),
            "map_digest": str(snapshot.get("map_digest") or ""),
            "moc_count": int(snapshot.get("moc_count", 0)),
            "pending_inbox_count": int(snapshot.get("pending_inbox_count", 0)),
            "taxonomy_digest": digest(
                json.dumps(plan.get("taxonomy", {}), ensure_ascii=False, sort_keys=True)
            ),
        })
    write_json_atomic(path, state)


def update_document(note: Note, updates: dict[str, object]) -> str:
    frontmatter = update_frontmatter(note.frontmatter_text, updates)
    body = note.body
    if note.had_frontmatter:
        return f"---\n{frontmatter.rstrip()}\n---\n{body}"
    return f"---\n{frontmatter.rstrip()}\n---\n{body.lstrip()}"


def update_frontmatter(text: str, updates: dict[str, object]) -> str:
    lines = text.splitlines()
    spans = top_field_spans(lines)
    replacements = {key: render_field(key, value) for key, value in updates.items()}
    output: list[str] = []
    index = 0
    while index < len(lines):
        key = next((name for name, (start, _) in spans.items() if start == index), None)
        if key is None:
            output.append(lines[index])
            index += 1
            continue
        start, end = spans[key]
        output.extend(replacements.pop(key, lines[start:end]))
        index = end
    for key, rendered in replacements.items():
        output.extend(rendered)
    return "\n".join(output)


def top_field_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    starts: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = TOP_FIELD_RE.match(line)
        if match and not line.startswith((" ", "\t")):
            starts.append((match.group(1), index))
    return {
        key: (start, starts[position + 1][1] if position + 1 < len(starts) else len(lines))
        for position, (key, start) in enumerate(starts)
    }


def render_field(key: str, value: object) -> list[str]:
    if isinstance(value, list):
        return [f"{key}:", *(f"  - {quote_yaml(str(item))}" for item in value)]
    return [f"{key}: {quote_yaml(str(value))}"]


def quote_yaml(value: str) -> str:
    if value.startswith("[[") or not value or value.startswith("#") or ": " in value or value.startswith(("[", "{")):
        return json.dumps(value, ensure_ascii=False)
    return value


def audit(
    root: Path,
    config: dict,
    detection: Detection,
    maintenance: MaintenanceDecision,
    policy: Policy,
    details: bool,
    link_scope: str,
    max_items: int,
) -> dict:
    incremental_scope = (
        detection.selected_profile == "para-inbox"
        and maintenance.mode == "incremental"
    )
    if incremental_scope:
        notes = load_notes_from_targets(
            root,
            [*detection.inbox_dirs, *detection.map_dirs],
            config,
            policy,
        )
        scope = "Inbox + Maps"
        subject_notes = [
            note for note in notes if path_in_targets(note.path, detection.inbox_dirs)
        ]
    else:
        notes = load_notes(root, config, policy)
        scope = "all allowed Markdown"
        subject_notes = notes
    hubs = discover_hubs(notes, config)
    hub_paths = {hub.note.path for hub in hubs}
    fields = config["fields"]
    tags = Counter(tag for note in subject_notes for tag in normalize_list(note.frontmatter.get(fields["tags"])))
    missing_moc = [note.relative.as_posix() for note in subject_notes if note.path not in hub_paths and not normalize_links(note.frontmatter.get(fields["hubs"]))]
    statuses = Counter(str(note.frontmatter.get(fields["organization_status"]) or "unset") for note in subject_notes if note.path not in hub_paths)
    links_checked = details
    link_report = {
        "note_links": {"broken": [], "ambiguous": []},
        "attachments": {"broken": [], "ambiguous": []},
    }
    actual_link_scope = "not checked"
    if links_checked:
        link_notes = (
            load_notes(root, config, policy) if link_scope == "vault" else notes
        )
        assets = collect_asset_paths(root, config, policy)
        link_report = link_issues(root, link_notes, assets, policy)
        actual_link_scope = "all allowed Markdown" if link_scope == "vault" else scope
    note_broken = link_report["note_links"]["broken"]
    note_ambiguous = link_report["note_links"]["ambiguous"]
    asset_broken = link_report["attachments"]["broken"]
    asset_ambiguous = link_report["attachments"]["ambiguous"]
    report = {
        "profile": detection.selected_profile,
        "confidence": detection.confidence,
        "maintenance": maintenance_dict(maintenance),
        "scope": scope,
        "notes": len(subject_notes),
        "map_notes": len(notes) - len(subject_notes) if incremental_scope else 0,
        "hubs": len(hubs),
        "missing_moc": len(missing_moc),
        "organization_status": dict(statuses),
        "links": {
            "checked": links_checked,
            "scope": actual_link_scope,
            "note_broken": len(note_broken),
            "note_ambiguous": len(note_ambiguous),
            "attachment_broken": len(asset_broken),
            "attachment_ambiguous": len(asset_ambiguous),
        },
        "protected_paths": {
            "enforced": len(policy.denied_roots),
            "paths": list(policy.denied_values),
        },
        "tags": {"unique": len(tags), "singletons": sum(count == 1 for count in tags.values()), "top": tags.most_common(10)},
    }
    if details:
        detail_values = [
            missing_moc, note_broken, note_ambiguous, asset_broken, asset_ambiguous
        ]
        report["details"] = {
            "missing_moc": missing_moc[:max_items],
            "broken_note_links": note_broken[:max_items],
            "ambiguous_note_links": note_ambiguous[:max_items],
            "broken_attachments": asset_broken[:max_items],
            "ambiguous_attachments": asset_ambiguous[:max_items],
            "truncated": any(len(values) > max_items for values in detail_values),
        }
    return report


def link_issues(
    root: Path, notes: list[Note], assets: list[Path], policy: Policy
) -> dict:
    by_stem: defaultdict[str, list[Note]] = defaultdict(list)
    by_relative: dict[str, Note] = {}
    for note in notes:
        by_stem[note.path.stem.casefold()].append(note)
        by_relative[note.relative.with_suffix("").as_posix().casefold()] = note
    assets_by_name: defaultdict[str, list[Path]] = defaultdict(list)
    assets_by_relative: dict[str, Path] = {}
    for asset in assets:
        assets_by_name[asset.name.casefold()].append(asset)
        assets_by_relative[asset.relative_to(root).as_posix().casefold()] = asset
    note_broken: list[dict] = []
    note_ambiguous: list[dict] = []
    asset_broken: list[dict] = []
    asset_ambiguous: list[dict] = []
    for note in notes:
        clean = strip_code(note.original)
        for raw in [*WIKILINK_RE.findall(clean), *MARKDOWN_LINK_RE.findall(clean)]:
            target = clean_link_target(raw)
            if not target:
                continue
            if Path(target).suffix.casefold() in ASSET_EXTENSIONS:
                matches = resolve_asset_target(
                    root, note, target, assets_by_name, assets_by_relative, policy
                )
                if not matches:
                    asset_broken.append({"source": note.relative.as_posix(), "target": target})
                elif len(matches) > 1:
                    asset_ambiguous.append({
                        "source": note.relative.as_posix(),
                        "target": target,
                        "matches": [display_path(root, path) for path in matches],
                    })
                continue
            matches = resolve_note_target(note, target, by_stem, by_relative)
            if not matches:
                note_broken.append({"source": note.relative.as_posix(), "target": target})
            elif len(matches) > 1:
                note_ambiguous.append({
                    "source": note.relative.as_posix(),
                    "target": target,
                    "matches": [match.relative.as_posix() for match in matches],
                })
        for raw in [*WIKI_EMBED_RE.findall(clean), *MARKDOWN_IMAGE_RE.findall(clean)]:
            target = clean_link_target(raw)
            if not target or is_external_target(target):
                continue
            matches = resolve_asset_target(
                root, note, target, assets_by_name, assets_by_relative, policy
            )
            if not matches:
                asset_broken.append({"source": note.relative.as_posix(), "target": target})
            elif len(matches) > 1:
                asset_ambiguous.append({
                    "source": note.relative.as_posix(),
                    "target": target,
                    "matches": [display_path(root, path) for path in matches],
                })
    return {
        "note_links": {"broken": note_broken, "ambiguous": note_ambiguous},
        "attachments": {"broken": asset_broken, "ambiguous": asset_ambiguous},
    }


def clean_link_target(raw: str) -> str:
    value = urllib.parse.unquote(raw.strip().strip("<>"))
    return value.split("#", 1)[0].strip()


def is_external_target(value: str) -> bool:
    return bool(re.match(r"^(?:https?|mailto|data|obsidian):", value, re.IGNORECASE))


def resolve_note_target(
    note: Note,
    target: str,
    by_stem: dict[str, list[Note]],
    by_relative: dict[str, Note],
) -> list[Note]:
    target = target.removesuffix(".md").lstrip("/")
    keys = {target.casefold()}
    if "/" in target:
        relative_candidate = (
            note.relative.parent / target
        ).as_posix()
        keys.add(str(Path(relative_candidate).with_suffix("")).casefold())
        keys.add(str(Path(target).with_suffix("")).casefold())
        return unique_notes(
            by_relative[key] for key in keys if key in by_relative
        )
    return by_stem.get(Path(target).stem.casefold(), [])


def unique_notes(values: Iterable[Note]) -> list[Note]:
    output: list[Note] = []
    seen: set[str] = set()
    for note in values:
        key = note.relative.as_posix()
        if key not in seen:
            seen.add(key)
            output.append(note)
    return output


def resolve_asset_target(
    root: Path,
    note: Note,
    target: str,
    by_name: dict[str, list[Path]],
    by_relative: dict[str, Path],
    policy: Policy,
) -> list[Path]:
    if is_external_target(target):
        return [Path(target)]
    normalized = target.lstrip("/")
    candidates: list[Path] = []
    if "/" in normalized or normalized.startswith("."):
        for path in (root / normalized, note.path.parent / normalized):
            resolved = path.resolve()
            if (
                resolved.is_file()
                and root in resolved.parents
                and not is_protected(resolved, policy)
            ):
                candidates.append(resolved)
        relative_match = by_relative.get(Path(normalized).as_posix().casefold())
        if relative_match:
            candidates.append(relative_match)
    else:
        candidates.extend(by_name.get(Path(normalized).name.casefold(), []))
    return sorted(set(candidates))


def format_audit(report: dict) -> str:
    status = report["organization_status"]
    link_text = (
        (
            f"notes broken={report['links']['note_broken']}, "
            f"notes ambiguous={report['links']['note_ambiguous']}, "
            f"attachments broken={report['links']['attachment_broken']}, "
            f"attachments ambiguous={report['links']['attachment_ambiguous']}"
        )
        if report["links"].get("checked") else "not checked; use --details"
    )
    return "\n".join([
        f"Profile: {report['profile']} (confidence: {report['confidence']})",
        f"Maintenance: {report['maintenance']['mode']}",
        f"Scope: {report['scope']}",
        f"Notes: {report['notes']}; map notes: {report.get('map_notes', 0)}; hubs: {report['hubs']}; missing MOC: {report['missing_moc']}",
        f"Status: linked={status.get('linked', 0)}, review={status.get('review', 0)}, unclassified={status.get('unclassified', 0)}, unset={status.get('unset', 0)}",
        "Links: " + link_text,
        f"Protected paths enforced: {report['protected_paths']['enforced']}",
        f"Tags: {report['tags']['unique']} unique; {report['tags']['singletons']} singletons",
    ])


def path_in_targets(path: Path, targets: Iterable[Path]) -> bool:
    return any(path == target or target in path.parents for target in targets)


def format_plan_summary(plan: dict, plan_path: Path, root: Path) -> str:
    summary = plan["summary"]
    lines = [
        f"Profile: {summary['profile']} (confidence: {summary['confidence']})",
        f"Maintenance: {summary.get('mode', 'unknown')} ({'; '.join(plan.get('maintenance', {}).get('triggers', []))})",
        f"Scanned: {summary['scanned']}; hubs: {summary['hubs']}; skipped: {summary['skipped']}",
        f"Index: cache hits={summary.get('cache_hits', 0)}; parsed={summary.get('parsed', summary['scanned'])}",
        f"Linked: {summary['linked']}; review: {summary['review']}; unclassified: {summary['unclassified']}",
        (
            f"Taxonomy: create={summary.get('taxonomy_create', 0)}; "
            f"split={summary.get('taxonomy_split', 0)}; "
            f"merge={summary.get('taxonomy_merge', 0)}"
        ),
        f"Plan: {display_path(root, plan_path)}",
    ]
    if summary.get("blocked"):
        lines.append("Blocked: " + summary["blocked"])
    return "\n".join(lines)

def format_candidates(item: dict) -> str:
    parts = []
    for candidate in item.get("candidates") or []:
        reasons = "; ".join(str(reason) for reason in candidate.get("reasons", []))
        parts.append(f"{candidate.get('link')} ({candidate.get('score')}: {reasons})")
    return ", ".join(parts[:3])


def diff_plan(root: Path, plan_path: Path, policy: Policy, max_items: int) -> int:
    ensure_not_protected(plan_path, policy, "plan read")
    plan = read_plan(root, plan_path, policy)
    summary = plan.get("summary", {})
    items = plan.get("items", [])
    print(f"Plan: {display_path(root, plan_path)}")
    print(
        f"Profile: {summary.get('profile')}; mode: {summary.get('mode')}; "
        f"items: {len(items)}"
    )
    print(
        f"Linked: {summary.get('linked', 0)}; review: {summary.get('review', 0)}; "
        f"unclassified: {summary.get('unclassified', 0)}"
    )
    if summary.get("blocked"):
        print("Blocked: " + str(summary["blocked"]))
    shown = 0
    for item in items:
        if shown >= max_items:
            remaining = len(items) - max_items
            print(f"... {remaining} more item(s); raise --max-items or open the plan JSON")
            break
        shown += 1
        path = str(item.get("path") or "")
        action = str(item.get("action") or "")
        if action == "link":
            targets = ", ".join(item.get("hubs") or [])
            print(f"link          {path} -> {targets}")
        elif action == "review":
            print(f"review        {path} -> candidates: {format_candidates(item)}")
        else:
            candidates = format_candidates(item)
            suffix = f"candidates: {candidates}" if candidates else "(no candidates)"
            print(f"unclassified  {path} -> {suffix}")
    return 0

def read_plan(root: Path, plan_path: Path, policy: Policy) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if int(plan.get("version", 0)) != VERSION:
        raise SystemExit(f"Unsupported plan version: {plan.get('version')}")
    if Path(plan.get("root", "")).resolve() != root:
        raise SystemExit("Plan root does not match --root")
    if str(plan.get("policy_digest") or "") != policy.digest:
        raise SystemExit("Protected-path policy changed; regenerate the plan")
    for item in plan.get("items", []):
        ensure_not_protected(
            resolve_under_root(root, str(item.get("path") or "")),
            policy,
            "plan item",
        )
    for path_value in plan.get("known_hubs", {}).values():
        ensure_not_protected(
            resolve_under_root(root, str(path_value)), policy, "plan hub"
        )
    return plan


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        atomic_write_text(path, content)
        return
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolve_under_root(root: Path, value: str) -> Path:
    canonical_root = root.resolve()
    resolved = resolve_path(canonical_root, value)
    if resolved != canonical_root and canonical_root not in resolved.parents:
        raise SystemExit(f"Path escapes knowledge-base root: {value}")
    return resolved


def resolve_path(root: Path, value: str) -> Path:
    root = root.resolve()
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def effective_max_items(value: Optional[int], config: dict) -> int:
    return max(0, value if value is not None else int(config["output"].get("max_items", 10)))


def normalize_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    raw = value if isinstance(value, list) else re.split(r"[,，]", str(value))
    return unique(unquote(str(item).strip()).lstrip("#").strip() for item in raw if str(item).strip())


def normalize_links(value: object) -> list[str]:
    links = []
    for item in normalize_list(value):
        item = unquote(item)
        links.append(item if item.startswith("[[") and item.endswith("]]" ) else f"[[{item}]]")
    return unique(links)


def strip_code(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", "", text)


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

