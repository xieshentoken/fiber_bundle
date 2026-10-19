from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.knowledge_graph.contracts import Source
from fiber_bundle.knowledge_graph.results import line_at
from fiber_bundle.knowledge_graph.sampling import body_sample


def source(text):
    digest = sha256(text.encode()).hexdigest()
    return Source("note:" + str(uuid4()), digest, digest, "冷板研究", text, "original", None)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("yaml", ["", "---\ntitle: 冷板研究\ntags: [液冷]\n---\n"])
def test_body_samples_preserve_original_coordinates(newline, yaml):
    note = source(
        (yaml + "# 冷板研究\n\n正文冷板与压降。\n\n## 第二节\n中部流体。\n\n末节传热。").replace(
            "\n", newline
        )
    )
    sample = body_sample(note)
    assert sample.text.startswith("冷板研究\n")
    assert [s.quote for s in sample.evidence] == ["正文冷板与压降。", "中部流体。", "末节传热。"]
    for span in sample.evidence:
        assert note.text[span.start : span.end] == span.quote
        assert span.line == line_at(note.text, span.start)
        assert span.line_end - span.line <= 2
        assert span.sha256 == note.sha256 and span.note_id == note.note_id
    changed = replace(note, sha256="a" * 64)
    assert body_sample(changed).evidence[0].sha256 != sample.evidence[0].sha256


@pytest.mark.parametrize(
    "text", ["", "# 冷板研究\n", "---\ntitle: 冷板研究\n---\n", "---\nunclosed: true"]
)
def test_no_body_is_explicit(text):
    with pytest.raises(InvalidInput, match="graph_body_unavailable"):
        body_sample(source(text))


def test_long_chinese_lines_and_many_lines_stay_bounded():
    note = source("# 冷板研究\n" + "热" * 600 + "\n\n" + "\n".join(["传热压降"] * 20))
    sample = body_sample(note)
    assert len(sample.evidence) <= 3 and len(sample.text) <= 443
    assert all(len(s.quote) <= 120 and s.line_end - s.line <= 2 for s in sample.evidence)


def test_title_terms_select_body_instead_of_images_or_capture_metadata():
    note = source(
        "# 冷板研究\n\n> 来源：[文章](https://example.invalid)\n\n![[image.png]]\n\n"
        "一般介绍。\n\n冷板热流与压降实验。\n\n中间无关段落。\n\n冷板优化设计与热阻。\n\n"
        "其他内容。\n\n冷板传热总结。\n\n> 抓取时间：2026-10-08\n"
    )
    sample = body_sample(note)
    assert [s.quote for s in sample.evidence] == [
        "冷板热流与压降实验。",
        "冷板优化设计与热阻。",
        "冷板传热总结。",
    ]
    assert "image.png" not in sample.text and "抓取时间" not in sample.text


def test_actual_model_budget_is_checked_before_embedding(prepared, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from fiber_bundle.knowledge_graph import semantica_adapter
    from fiber_bundle.knowledge_graph.contracts import Model

    request = replace(
        prepared.request,
        engine=replace(prepared.request.engine, model=Model("test", "v1", "a" * 64, 3)),
    )
    (tmp_path / "sentence_bert_config.json").write_text(json.dumps({"max_seq_length": 8}))

    class Embedder:
        def __init__(self, **kwargs):
            pass

        def get_method(self):
            return "sentence_transformers"

        def get_embedding_dimension(self):
            return 3

        def embed_batch(self, texts):
            pytest.fail("over budget input must never reach embedding/truncation")

    tokenizer = SimpleNamespace(model_max_length=512, encode=lambda text, **kw: [1] * 10)
    modules = {
        "numpy": SimpleNamespace(),
        "torch": SimpleNamespace(set_num_threads=lambda n: None),
        "semantica": SimpleNamespace(__version__=request.engine.version),
        "semantica.embeddings.text_embedder": SimpleNamespace(TextEmbedder=Embedder),
        "transformers": SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **kw: tokenizer)
        ),
    }
    monkeypatch.setattr(semantica_adapter.importlib, "import_module", lambda name: modules[name])
    with pytest.raises(InvalidInput, match="graph_model_input_limit"):
        semantica_adapter._embeddings(request, tmp_path, ["over budget"])
