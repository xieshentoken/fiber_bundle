"""Publication-date evidence from named HTML fields, never from capture time."""

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

META = {"article:published_time", "date", "pubdate"}
ASSIGNMENT = re.compile(
    r"""(?m)(?:^|[;\n])\s*var\s+(ct|create_time)\s*=\s*["']([0-9]{10})["']\s*(?:\*\s*1\s*)?;"""
)


class Fields(HTMLParser):
    def __init__(self, wechat: bool):
        super().__init__(convert_charrefs=True)
        self.wechat = wechat
        self.script = False
        self.index = 0
        self.parts: list[str] = []
        self.values: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "meta":
            name = fields.get("property") or fields.get("name") or ""
            if name in META and fields.get("content"):
                self.values.append((f"meta[{name}]", fields["content"] or "", "iso"))
        if tag == "script":
            self.script = True
            self.index += 1
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.script and self.wechat:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.script:
            for match in ASSIGNMENT.finditer("".join(self.parts)):
                self.values.append((f"script[{self.index}].var.{match[1]}", match[2], "epoch"))
            self.parts = []
            self.script = False


def extract(html: str, url: str) -> dict[str, Any]:
    fields = Fields(urlsplit(url).hostname == "mp.weixin.qq.com")
    fields.feed(html)
    evidence: list[dict[str, str]] = []
    moments = set()
    invalid = len(fields.values) > 100
    for locator, literal, kind in fields.values[:100]:
        try:
            if kind == "epoch":
                moment = datetime.fromtimestamp(int(literal), UTC).astimezone(
                    ZoneInfo("Asia/Shanghai")
                )
            else:
                moment = datetime.fromisoformat(literal.replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    raise ValueError("timezone unknown")
            if not 1990 <= moment.year <= 2100:
                raise ValueError("date outside supported range")
            moments.add(moment.astimezone(UTC))
            evidence.append(
                {"locator": locator, "value": moment.isoformat(), "meaning": "publication"}
            )
        except (ValueError, OverflowError, OSError):
            invalid = True
            evidence.append({"locator": locator, "value": "", "meaning": "invalid"})
    state = "known" if len(moments) == 1 and not invalid else "unknown"
    return {
        "state": state,
        "value": evidence[0]["value"] if state == "known" else "",
        "reason": "verified_named_fields"
        if state == "known"
        else "conflict_or_invalid"
        if evidence
        else "missing",
        "evidence": evidence,
    }
