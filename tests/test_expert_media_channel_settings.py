"""Adding output negotiation must not backfill or reinterpret old channel settings."""

from test_message_channels import channel as channel

from fiber_bundle import database
from fiber_bundle.channels import configuration, store
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest


def test_old_settings_read_and_explicit_save_keep_value_fingerprint(config, channel):
    item, _ = channel
    original = item["settings"]
    assert "expert_media" not in original
    fingerprint = digest(canonical(original).encode())
    with store.opened(config) as db:
        current = store.get(db, item["id"])
        assert current["revision"] == 1 and current["value"] == original
    with database.connect(config.state_root) as main:
        saved = configuration.save(
            config,
            main,
            configuration.Save(
                id=item["id"],
                expected_revision=1,
                settings=configuration.Settings.model_validate(original),
            ),
        )
        # Existing explicit-save semantics increment revision even for equal settings.
        assert saved["revision"] == 2
        assert digest(canonical(saved["settings"]).encode()) == fingerprint
        assert saved["settings"]["configuration_sha256"] == original["configuration_sha256"]
        enabled = configuration.save(
            config,
            main,
            configuration.Save(
                id=item["id"],
                expected_revision=2,
                settings=configuration.Settings.model_validate(
                    {**original, "expert_media": "images"}
                ),
            ),
        )
        assert enabled["revision"] == 3 and enabled["settings"]["expert_media"] == "images"
        # An older client omitting the new field cannot silently disable an explicit setting.
        compatible = configuration.save(
            config,
            main,
            configuration.Save(
                id=item["id"],
                expected_revision=3,
                settings=configuration.Settings.model_validate(original),
            ),
        )
        assert compatible["settings"] == enabled["settings"]
