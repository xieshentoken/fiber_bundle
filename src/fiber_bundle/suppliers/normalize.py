"""Deterministic units, dates and classifications; no inferred commercial terms."""

import re
from datetime import date
from decimal import Decimal

MASS = {"t": Decimal(1000), "kg": Decimal(1), "100g": Decimal("0.1"), "g": Decimal("0.001")}
CATEGORIES = {
    "金刚石": ("金刚石", "diamond"),
    "碳化硅": ("碳化硅", "sic", "silicon carbide"),
    "热管理": ("散热", "导热", "冷板", "热管理", "石墨烯"),
    "设备": ("设备", "炉", "仪器"),
    "检测服务": ("检测", "检验", "测试报告"),
    "表面处理": ("电镀", "镀覆", "表面处理", "去毛刺"),
}


def categories(text: str) -> list[str]:
    return [name for name, terms in CATEGORIES.items() if any(t in text.lower() for t in terms)]


def money(text: str) -> dict[str, str | None]:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(CNY|USD|EUR|JPY|GBP|HKD|人民币|元|￥|¥|美元)\s*(?:元)?\s*[/／每]\s*(100g|kg|公斤|千克|克|g|吨|t|件|套|米|m2|m|L)",
        text,
        re.I,
    )
    if not match:
        return {"amount": None, "currency": None, "unit": None}
    amount, currency, unit = match.groups()
    currency = {"人民币": "CNY", "元": "CNY", "￥": "CNY", "¥": "CNY", "美元": "USD"}.get(
        currency, currency.upper()
    )
    unit = {
        "公斤": "kg",
        "千克": "kg",
        "克": "g",
        "吨": "t",
        "件": "piece",
        "套": "set",
        "米": "m",
    }.get(unit, unit)
    return {"amount": format(Decimal(amount), "f"), "currency": currency, "unit": unit}


def convert(amount: str, unit: str, target: str) -> Decimal | None:
    if unit == target:
        return Decimal(amount)
    if unit in MASS and target in MASS:
        return Decimal(amount) / MASS[unit] * MASS[target]
    return None


def valid_date(value: str | None) -> str | None:
    try:
        return value if value and date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def phone(value: str) -> str:
    return re.sub(r"[\s()（）-]", "", value)
