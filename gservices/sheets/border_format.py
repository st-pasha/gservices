from typing import TYPE_CHECKING, Literal, cast

from gservices.sheets.utils import color_object_to_string, color_string_to_object

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

BorderStyle = Literal["DOTTED", "DASHED", "SOLID", "SOLID_MEDIUM", "SOLID_THICK", "DOUBLE"] | None


class BorderFormat:
    """A single edge of a cell's border — style, width, and color.

    Use via `CellFormat.border_top` / `border_right` / `border_bottom` /
    `border_left`. Construct directly to assign:

        cell.format.border_bottom = BorderFormat(style="SOLID", width=1)
    """

    def __init__(
        self,
        style: BorderStyle = "SOLID",
        width: int = 0,
        color: str | None = "#000000",
    ):
        # A border with width=0 is invisible, so a non-None style or color
        # would be misleading — normalize them away.
        if width == 0:
            style = None
            color = None
        self.style: BorderStyle = style
        self.width = width
        self.color = color

    @staticmethod
    def from_data(data: gs.Border) -> BorderFormat:
        style = data.get("style")
        if style in ("NONE", "STYLE_UNSPECIFIED"):
            style = None
        return BorderFormat(
            width=data.get("width", 0),
            style=style,
            color=color_object_to_string(data.get("colorStyle")),
        )

    def to_data(self) -> gs.Border:
        return {
            "style": self.style or "NONE",
            "width": self.width,
            "colorStyle": color_string_to_object(self.color),
        }

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if isinstance(other, dict):
            other = BorderFormat.from_data(cast("gs.Border", other))
        if isinstance(other, BorderFormat):
            return (
                self.style == other.style
                and self.width == other.width
                and self.color == other.color
            )
        return False
