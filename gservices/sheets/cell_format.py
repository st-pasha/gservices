from __future__ import annotations
import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]
    from gservices.sheets.cell import Cell


WrapStrategy = Literal["OVERFLOW_CELL", "LEGACY_WRAP", "CLIP", "WRAP"]
HorizontalAlignment = Literal["LEFT", "CENTER", "RIGHT"]
VerticalAlignment = Literal["TOP", "MIDDLE", "BOTTOM"]

NUMBER_FORMATS = (
    "TEXT",
    "NUMBER",
    "PERCENT",
    "CURRENCY",
    "DATE",
    "TIME",
    "DATE_TIME",
    "SCIENTIFIC",
)


class CellFormat:
    def __init__(self, data: gs.CellFormat, cell: Cell | None) -> None:
        self._data = data
        self._cell = cell

    @property
    def number_format(self) -> str:
        nf = self._data.get("numberFormat", {})
        nf_type = nf.get("type", "TEXT")
        nf_pattern = nf.get("pattern", "")
        if nf_type == "NUMBER_FORMAT_TYPE_UNSPECIFIED":
            nf_type = "TEXT"
        if nf_pattern:
            nf_type += f"({nf_pattern})"
        return nf_type

    @number_format.setter
    def number_format(self, value: str) -> None:
        if value == self.number_format:
            return
        match = re.match(r"(\w+)\((.*)\)", value)
        if match:
            obj = {"type": match.group(1), "pattern": match.group(2)}
        else:
            obj = {"type": value}
        assert obj["type"] in NUMBER_FORMATS
        self._set_property("numberFormat", obj)

    @property
    def background_color(self) -> str | None:
        """
        The background color of the cell. This can be either a color in hex-format
        (such as "#A3BCFF"), a theme color (e.g. "ACCENT1"), or None for a default
        color within the spreadsheet.
        """
        return color_object_to_string(self._data.get("backgroundColorStyle"))

    @background_color.setter
    def background_color(self, value: str | None) -> None:
        if value == self.background_color:
            return
        obj = color_string_to_object(value)
        self._set_property("backgroundColorStyle", obj)

    @property
    def foreground_color(self) -> str | None:
        """
        The color of the text in the cell. This can be either a color in hex-format
        (such as "#A3BCFF"), a theme color (e.g. "ACCENT1"), or None for a default
        color within the spreadsheet.
        """
        return color_object_to_string(self._text_format.get("foregroundColorStyle"))

    @foreground_color.setter
    def foreground_color(self, value: str | None) -> None:
        if value == self.foreground_color:
            return
        obj = color_string_to_object(value)
        self._set_property("textFormat.foregroundColorStyle", obj)

    @property
    def padding(self) -> tuple[float, float, float, float] | None:  # TRBL
        p = self._data.get("padding")
        if p is None:
            return None
        return (
            p.get("top", 0),
            p.get("right", 0),
            p.get("bottom", 0),
            p.get("left", 0),
        )

    @padding.setter
    def padding(self, value: tuple[float, float, float, float] | None) -> None:
        if value is None:
            self._set_property("padding", {})
        else:
            obj = {
                "top": value[0],
                "right": value[1],
                "bottom": value[2],
                "left": value[3],
            }
            self._set_property("padding", obj)

    @property
    def horizontal_alignment(self) -> HorizontalAlignment | None:
        """
        The horizontal alignment of the text in the cell.
        """
        value = self._data.get("horizontalAlignment")
        if value == "HORIZONTAL_ALIGN_UNSPECIFIED":
            value = None
        return value

    @horizontal_alignment.setter
    def horizontal_alignment(self, value: HorizontalAlignment | None) -> None:
        if value == self.horizontal_alignment:
            return
        self._set_property("horizontalAlignment", value)

    @property
    def vertical_alignment(self) -> VerticalAlignment | None:
        """
        The vertical alignment of the text in the cell.
        """
        value = self._data.get("verticalAlignment")
        if value == "VERTICAL_ALIGN_UNSPECIFIED":
            value = None
        return value

    @vertical_alignment.setter
    def vertical_alignment(self, value: VerticalAlignment | None) -> None:
        if value == self.vertical_alignment:
            return
        self._set_property("verticalAlignment", value)

    @property
    def wrap_strategy(self) -> WrapStrategy | None:
        value = self._data.get("wrapStrategy")
        if value == "WRAP_STRATEGY_UNSPECIFIED":
            value = None
        return value

    @wrap_strategy.setter
    def wrap_strategy(self, value: WrapStrategy | None):
        if value == self.wrap_strategy:
            return
        self._set_property("wrapStrategy", value)

    @property
    def font_family(self) -> str | None:
        return self._text_format.get("fontFamily")

    @font_family.setter
    def font_family(self, value: str | None):
        if value == self.font_family:
            return
        self._set_property("textFormat.fontFamily", value)

    @property
    def font_size(self) -> float:
        return self._text_format.get("fontSize", 10)

    @font_size.setter
    def font_size(self, value: float) -> None:
        if value == self.font_size:
            return
        self._set_property("textFormat.fontSize", value)

    @property
    def is_bold(self) -> bool:
        return self._text_format.get("bold", False)

    @is_bold.setter
    def is_bold(self, value: bool) -> None:
        if value == self.is_bold:
            return
        self._set_property("textFormat.bold", value)

    @property
    def is_italic(self) -> bool:
        return self._text_format.get("italic", False)

    @is_italic.setter
    def is_italic(self, value: bool) -> None:
        if value == self.is_italic:
            return
        self._set_property("textFormat.italic", value)

    @property
    def is_strikethrough(self) -> bool:
        return self._text_format.get("strikethrough", False)

    @is_strikethrough.setter
    def is_strikethrough(self, value: bool) -> None:
        if value == self.is_strikethrough:
            return
        self._set_property("textFormat.strikethrough", value)

    @property
    def is_underline(self) -> bool:
        return self._text_format.get("underline", False)

    @is_underline.setter
    def is_underline(self, value: bool) -> None:
        if value == self.is_underline:
            return
        self._set_property("textFormat.underline", value)

    @property
    def border_top(self) -> BorderFormat | None:
        top_data = self._data.get("borders", {}).get("top")
        if top_data is not None:
            return BorderFormat.from_data(top_data)

    @border_top.setter
    def border_top(self, value: BorderFormat | None) -> None:
        if value == self.border_top:
            return
        obj: "gs.Border" = {} if value is None else value.to_data()
        self._set_property("borders.top", obj)

    @property
    def border_right(self) -> BorderFormat | None:
        right_data = self._data.get("borders", {}).get("right")
        if right_data is not None:
            return BorderFormat.from_data(right_data)

    @border_right.setter
    def border_right(self, value: BorderFormat | None) -> None:
        if value == self.border_right:
            return
        obj: "gs.Border" = {} if value is None else value.to_data()
        self._set_property("borders.right", obj)

    @property
    def border_bottom(self) -> BorderFormat | None:
        bottom_data = self._data.get("borders", {}).get("bottom")
        if bottom_data is not None:
            return BorderFormat.from_data(bottom_data)

    @border_bottom.setter
    def border_bottom(self, value: BorderFormat | None) -> None:
        if value == self.border_bottom:
            return
        obj: gs.Border = {} if value is None else value.to_data()
        self._set_property("borders.bottom", obj)

    @property
    def border_left(self) -> BorderFormat | None:
        left_data = self._data.get("borders", {}).get("left")
        if left_data is not None:
            return BorderFormat.from_data(left_data)

    @border_left.setter
    def border_left(self, value: BorderFormat | None) -> None:
        if value == self.border_left:
            return
        obj: gs.Border = {} if value is None else value.to_data()
        self._set_property("borders.left", obj)

    def print(self, indent: str = ""):
        if indent:
            i = indent
        else:
            pprint("[bold cyan]CellFormat:")
            i = "  "
        for prop in (
            "number_format",
            "background_color",
            "foreground_color",
            "padding",
            "horizontal_alignment",
            "vertical_alignment",
            "wrap_strategy",
            "font_family",
            "font_size",
            "is_bold",
            "is_italic",
            "is_underline",
            "is_strikethrough",
            "border_top",
            "border_right",
            "border_bottom",
            "border_left",
        ):
            pprint(f"{i}[green]{prop}:[/] {getattr(self, prop)}")

    def _set_property(self, property: str, value: Any) -> None:
        if self._cell is None:
            raise RuntimeError("This CellFormat cannot be modified")
        set_dotted_property(self._data, property, value)
        self._cell._set_property("userEnteredFormat." + property, value)

    @property
    def _text_format(self) -> gs.TextFormat:
        text_format = self._data.get("textFormat", {})
        self._data["textFormat"] = text_format
        return text_format


from gservices.sheets.border_format import BorderFormat
from gservices.sheets.utils import (
    color_object_to_string,
    color_string_to_object,
    set_dotted_property,
)
from gservices.print_utils import pprint
