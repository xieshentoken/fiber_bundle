"""Version-aware directory roles; attachment roots are namespaces, not note targets."""

import unicodedata

from .vault_save_contract import SaveSettings, SettingsError
from .vault_scope import inside


def overlaps(left: str, right: str) -> bool:
    left, right = (unicodedata.normalize("NFC", value).casefold() for value in (left, right))
    return inside(left, right) or inside(right, left)


def directories(settings: SaveSettings) -> list[str]:
    notes = [settings.inbox_dir, settings.archive_dir, *settings.moc_destinations.values()]
    if settings.policy_version == 1:
        if (
            settings.inbox_dir == settings.archive_dir
            or settings.inbox_dir in settings.moc_destinations.values()
        ):
            raise SettingsError("save_policy_directory_overlap")
        if any(
            inside(path, settings.image_dir) or inside(settings.image_dir, path) for path in notes
        ):
            raise SettingsError("save_policy_directory_overlap")
        return [*notes, settings.image_dir]
    roles = [settings.inbox_dir, settings.archive_dir, settings.moc_dir, settings.book_dir]
    if any(overlaps(a, b) for i, a in enumerate(roles) for b in roles[i + 1 :]):
        raise SettingsError("save_policy_directory_overlap")
    if any(overlaps(path, settings.attachment_root) for path in roles):
        raise SettingsError("save_policy_directory_overlap")
    if any(
        not inside(path, settings.attachment_root) or path == settings.attachment_root
        for path in [settings.image_dir, settings.original_dir]
    ):
        raise SettingsError("save_policy_attachment_parent_required")
    if overlaps(settings.image_dir, settings.original_dir):
        raise SettingsError("save_policy_directory_overlap")
    for path in settings.moc_destinations.values():
        if any(
            overlaps(path, reserved)
            for reserved in [
                settings.inbox_dir,
                settings.moc_dir,
                settings.book_dir,
                settings.attachment_root,
            ]
        ):
            raise SettingsError("save_policy_directory_overlap")
    return [
        *roles,
        settings.attachment_root,
        settings.image_dir,
        settings.original_dir,
        *settings.moc_destinations.values(),
    ]


def excluded(settings: dict[str, object], relative: str) -> bool:
    return settings.get("policy_version") == 2 and inside(
        relative, str(settings["attachment_root"])
    )
