"""Validated KRX symbol normalization shared by publisher and receiver."""
from __future__ import annotations

import re

_KRX_SYMBOL = re.compile(r"^[0-9]{4}[0-9A-Z]{2}$")


def normalize_symbol(value):
    if not isinstance(value, str):
        return None
    if value != value.strip() or value != value.upper() or not _KRX_SYMBOL.fullmatch(value):
        return None
    return value
