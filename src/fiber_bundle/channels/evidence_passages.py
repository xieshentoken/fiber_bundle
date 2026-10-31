"""Question-aware selection of bounded, source-exact prose; no semantic assertions."""

import re
from itertools import chain
from typing import Any

from .. import auth, note_index, vault_scope, vaults
from ..config import IntegrityError
from ..search_matching import fold, terms

CAUSE = re.compile(r"原因|为何|为什么|机制|机理|导致|起源|本征|\b(?:why|cause|mechanism)\b", re.I)
EXPLANATION = re.compile(
    r"因为|由于|源于|来自|导致|决定|机制|机理|散射|取决于|影响|阻碍|"
    r"\b(?:because|due to|arises|causes?|mechanism|scattering|depends)\b",
    re.I,
)


def select(
    config: Any,
    db: Any,
    channel: Any,
    item: Any,
    expression: str,
    question: str,
    limit: int,
    *,
    procedure_context: bool = False,
) -> list[dict[str, Any]]:
    if item.get("expert_projection") or item.get("expert_module_id"):
        return [item]
    vault = auth.grant(db, channel["value"]["client_id"], item["vault_id"], "query")
    root = vaults.checked_vault(config, vault)
    if not vault_scope.for_vault(root, vault).allows(item["note_path"]):
        raise IntegrityError("channel_source_changed")
    note = note_index.read_note(root, item["note_path"], note_index.Limits().note_bytes)
    if note.sha256 != item["sha256"]:
        raise IntegrityError("channel_source_changed")
    if procedure_context:
        from . import evidence_procedures

        selected = evidence_procedures.ranked(
            note.text, expression, question, limit, title=note.title
        )
        if selected:
            return [
                {**item, "line": line, "line_end": end, "snippet": snippet, "section_path": path}
                for line, end, snippet, path in selected
            ]
    return [
        {**item, "line": line, "line_end": end, "snippet": snippet}
        for line, end, snippet in ranked(note.text, expression, question, limit, title=note.title)
    ]


def ranked(
    content: str, expression: str, question: str, limit: int, *, title: str = ""
) -> list[tuple[int, int, str]]:
    front = re.match(r"\ufeff?---\r?\n.*?\r?\n(?:---|\.\.\.)\s*\r?\n", content, re.S)
    first = content[: front.end()].count("\n") if front else 0
    allowed = set()
    fenced = False
    for line, text in enumerate(content.splitlines(), 1):
        if line <= first:
            continue
        if text.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if (
            not fenced
            and text.strip()
            and not text.lstrip().startswith(("#", "!", "[[", "- [[", "<!--"))
        ):
            allowed.add(line)
    patterns = terms(expression)
    causal = bool(CAUSE.search(question))
    choices = []
    seen = set()
    for line, passage in chain(note_index.passages(content), note_index.adjacent_passages(content)):
        end = line + passage.count("\n")
        if not set(range(line, end + 1)) <= allowed or not any(c.isalnum() for c in passage):
            continue
        if (line, passage) in seen:
            continue
        seen.add((line, passage))
        coverage = sum(bool(p.search(fold(passage))) for p in patterns)
        if not coverage and not any(p.search(fold(title)) for p in patterns):
            continue
        intent = int(causal and bool(EXPLANATION.search(passage)))
        # Distinct term coverage beats repetition. Equal matches favor explanations
        # for causal questions, then the concise passage, never a longer paragraph.
        choices.append(((coverage, intent, -len(passage), -line), line, end, passage))
    result: list[tuple[int, int, str]] = []
    if any(score[0] for score, *_ in choices):
        choices = [choice for choice in choices if choice[0][0]]
    for _, line, end, passage in sorted(choices, reverse=True):
        if any(not (end < start or line > stop) for start, stop, _ in result):
            continue
        result.append((line, end, passage))
        if len(result) == limit:
            break
    return result
