import pytest

from fiber_bundle.knowledge_graph.intake import prepare

from .support import build_corpus


@pytest.fixture(scope="module")
def corpus_config(tmp_path_factory):
    import json

    from fiber_bundle.config import load_config

    root = tmp_path_factory.mktemp("graph-synthetic-corpus")
    probe = root / "probe"
    probe.touch()
    if not hasattr(probe.stat(), "st_birthtime"):
        pytest.skip("stable physical note registration requires file birth time")
    probe.unlink()
    path = root / "config.json"
    path.write_text(
        json.dumps(
            {
                "state_root": ".state",
                "vaults": {
                    "personal": "test vault/temp_vault1",
                    "supplier": "test vault/temp_vault2",
                },
            }
        )
    )
    return load_config(path, root)


@pytest.fixture(scope="module")
def corpus(corpus_config):
    return build_corpus(corpus_config)


@pytest.fixture
def prepared(corpus_config, corpus):
    authorization, vaults, _, selected, _ = corpus
    return prepare(corpus_config, authorization, vaults["personal"], selected, enabled=True)
