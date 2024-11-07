from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]


class Formula:
    def __init__(self, text: str):
        self.text = text

    def __repr__(self):
        return f"Formula({self.text})"


class HyperlinkFormula(Formula):
    def __init__(self, url: str, label: str):
        escaped_label = label.replace('"', '""')
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "http://" + url
        self.url = url
        self.label = label
        super().__init__(f'=HYPERLINK("{url}", "{escaped_label}")')


@dataclass
class ErrorValue:
    message: str
    type: Literal[
        "ERROR",
        "NULL_VALUE",
        "DIVIDE_BY_ZERO",
        "VALUE",
        "REF",
        "NAME",
        "NUM",
        "N_A",
        "LOADING",
    ]


# Possible types of a value in a cell.
CellValue = str | float | bool | Formula | ErrorValue | None


def value_to_python(value: gs.ExtendedValue | None) -> CellValue:
    if value is None:
        return None
    b = value.get("boolValue")
    n = value.get("numberValue")
    s = value.get("stringValue")
    e = value.get("errorValue")
    f = value.get("formulaValue")
    if b is not None:
        return b
    if n is not None:
        return n
    if s is not None:
        return s
    if e is not None:
        type = e.get("type", "ERROR")
        if type == "ERROR_TYPE_UNSPECIFIED":
            type = "ERROR"
        return ErrorValue(message=e.get("message", ""), type=type)
    if f is not None:
        return Formula(f)


def python_to_value(value: CellValue) -> gs.ExtendedValue:
    if isinstance(value, Formula):
        return {"formulaValue": value.text}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, (int, float)):
        return {"numberValue": value}
    return {}
