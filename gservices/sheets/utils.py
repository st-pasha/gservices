from __future__ import annotations
import re
from typing import TYPE_CHECKING, Any, Mapping, cast

if TYPE_CHECKING:
    import googleapiclient._apis.sheets.v4.schemas as gs  # type: ignore[reportMissingModuleSource]

AnyDict = dict[str, Any]
ADDRESS_PATTERN = re.compile(r"^([A-Z]+)(\d+)$")


def coords_to_address(row: int, col: int) -> str:
    """
    The address of a cell in Excel notation, such as "B102".
    """
    addr = str(row + 1)
    i = col + 1
    while i:
        addr = chr(ord("A") + ((i - 1) % 26)) + addr
        i = (i - 1) // 26
    return addr


def address_to_coords(addr: str) -> tuple[int, int]:
    match = re.match(ADDRESS_PATTERN, addr)
    if not match:
        raise ValueError(f"Invalid cell address: {addr}")
    row = int(match.group(2))
    col = 0
    for ch in match.group(1):
        col = col * 26 + (ord(ch) - 64)  # 64 == ord('A') - 1
    return (row - 1, col - 1)


def color_object_to_string(color_style: gs.ColorStyle | None) -> str | None:
    if color_style is None:
        return None
    if "rgbColor" in color_style:
        rgb = color_style["rgbColor"]
        red = _float_to_hexstr(rgb.get("red", 0))
        green = _float_to_hexstr(rgb.get("green", 0))
        blue = _float_to_hexstr(rgb.get("blue", 0))
        alpha = _float_to_hexstr(rgb.get("alpha", 1))
        if alpha == "ff":
            alpha = ""
        return f"#{red}{green}{blue}{alpha}"
    if "themeColor" in color_style:
        color = color_style["themeColor"]
        if color == "THEME_COLOR_TYPE_UNSPECIFIED":
            return None
        return color


def color_string_to_object(color: str | None) -> "gs.ColorStyle":
    if color is None or color == "":
        return {}
    if color.startswith("#"):
        assert len(color) == 9 or len(color) == 7
        rgb: "gs.Color" = {
            "red": _hexstr_to_float(color[1:3]),
            "green": _hexstr_to_float(color[3:5]),
            "blue": _hexstr_to_float(color[5:7]),
        }
        if len(color) == 9:
            alpha = _hexstr_to_float(color[7:9])
            if alpha != 1:
                rgb["alpha"] = alpha
        return {"rgbColor": rgb}
    else:
        assert color in (
            "TEXT",
            "BACKGROUND",
            "ACCENT1",
            "ACCENT2",
            "ACCENT3",
            "ACCENT4",
            "ACCENT5",
            "ACCENT6",
            "LINK",
        )
        return {"themeColor": color}


def array_move(arr: list[Any], old_index: int, new_index: int):
    value = arr[old_index]
    del arr[old_index]
    if new_index > old_index:
        new_index -= 1
    arr.insert(new_index, value)


def _float_to_hexstr(x: float) -> str:
    return f"{int(x * 255 + 0.1):02x}"


def _hexstr_to_float(hex: str) -> float:
    return int(hex, base=16) / 255


def set_dotted_property(target: Mapping[str, Any], key: str, value: Any) -> None:
    """
    Sets `target[key] = value`, except when `key` is a dot-string (such as
    "format.color"), then creates a nested dictionary `target["format"]["color"] =
    value`.
    """
    assert isinstance(target, dict)
    if "." in key:
        parts = key.split(".", 1)
        if parts[0] in target:
            assert isinstance(target[parts[0]], dict)
        else:
            target[parts[0]] = {}
        set_dotted_property(target[parts[0]], parts[1], value)
    else:
        target[key] = value


def merge_requests(request0: gs.Request, request1: gs.Request) -> bool:
    """
    Attempts to merge [request0] with [request1].

    If successful, returns `True` and modifies [request0] to incorporate data from
    [request1]. Returns `False` if merge is not possible.
    """
    return merge_delete_range(
        request0.get("deleteRange"), request1.get("deleteRange")
    ) or merge_update_cells(request0.get("updateCells"), request1.get("updateCells"))


def merge_delete_range(
    req0: gs.DeleteRangeRequest | None,
    req1: gs.DeleteRangeRequest | None,
) -> bool:
    # When rows are deleted, all subsequent rows move up, which means two consecutive
    # delete operations can be merged if their starting row is the same.
    if req0 and req1:
        if req0.get("shiftDimension") != req1.get("shiftDimension"):
            return False
        range0 = req0.get("range", {})
        range1 = req1.get("range", {})
        if range0.get("sheetId") != range1.get("sheetId"):
            return False
        range0_row0 = range0.get("startRowIndex")
        range0_row1 = range0.get("endRowIndex")
        range1_row0 = range1.get("startRowIndex")
        range1_row1 = range1.get("endRowIndex")
        range0_col0 = range0.get("startColumnIndex")
        range0_col1 = range0.get("endColumnIndex")
        range1_col0 = range1.get("startColumnIndex")
        range1_col1 = range1.get("endColumnIndex")
        if (
            range0_row0 is not None
            and range0_row1 is not None
            and range1_row0 is not None
            and range1_row1 is not None
            and range0_row0 == range1_row0
        ):
            range0["endRowIndex"] = range0_row1 + (range1_row1 - range1_row0)
            return True
        if (
            range0_col0 is not None
            and range0_col1 is not None
            and range1_col0 is not None
            and range1_col1 is not None
            and range0_col0 == range1_col0
        ):
            range0["endColumnIndex"] = range0_col1 + (range1_col1 - range1_col0)
            return True
    return False


def merge_update_cells(
    req0: gs.UpdateCellsRequest | None,
    req1: gs.UpdateCellsRequest | None,
) -> bool:
    # Two UpdateCells requests can be merged if they refer to the same cell
    if req0 and req1:
        start0 = req0.get("start")
        start1 = req1.get("start")
        if not (start0 and start0 == start1):
            return False
        rows0 = req0.get("rows")
        rows1 = req1.get("rows")
        if not (rows0 and rows1 and len(rows0) == len(rows1) and len(rows0) == 1):
            return False
        values0 = rows0[0].get("values")
        values1 = rows1[0].get("values")
        if not (
            values0 and values1 and len(values0) == len(values1) and len(values0) == 1
        ):
            return False
        props0 = values0[0]
        props1 = values1[0]
        merge_structs(cast(AnyDict, props0), cast(AnyDict, props1))
        fields0 = req0.get("fields", "")
        fields1 = req1.get("fields", "")
        fields_both = set(fields0.split(",") + fields1.split(","))
        req0["fields"] = ",".join(sorted(fields_both))
        return True
    return False


def merge_structs(struct1: dict[str, Any], struct2: dict[str, Any]):
    for key in struct2:
        if key in struct1:
            if isinstance(struct1[key], dict) and isinstance(struct2[key], dict):
                merge_structs(struct1[key], struct2[key])
                continue
        struct1[key] = struct2[key]


def cell_formats_equal(
    format1: gs.CellFormat | None, format2: gs.CellFormat | None
) -> bool:
    return (format1 is None and format2 is None) or (
        (format1 is not None and format2 is not None)
        and _color_style_equal(
            format1.get("backgroundColorStyle"), format2.get("backgroundColorStyle")
        )
        and _borders_equal(format1.get("borders"), format2.get("borders"))
        and format1.get("horizontalAlignment") == format2.get("horizontalAlignment")
        and format1.get("hyperlinkDisplayType") == format2.get("hyperlinkDisplayType")
        and format1.get("numberFormat") == format2.get("numberFormat")
        and format1.get("padding") == format2.get("padding")
        and format1.get("textDirection") == format2.get("textDirection")
        and _text_format_equal(format1.get("textFormat"), format2.get("textFormat"))
        and format1.get("textRotation") == format2.get("textRotation")
        and format1.get("verticalAlignment") == format2.get("verticalAlignment")
        and format1.get("wrapStrategy") == format2.get("wrapStrategy")
    )


def _borders_equal(borders1: gs.Borders | None, borders2: gs.Borders | None) -> bool:
    if borders1 is None and borders2 is None:
        return True
    if borders1 is not None and borders2 is not None:
        return (
            _border_equal(borders1.get("top"), borders2.get("top"))
            and _border_equal(borders1.get("right"), borders2.get("right"))
            and _border_equal(borders1.get("bottom"), borders2.get("bottom"))
            and _border_equal(borders1.get("left"), borders2.get("left"))
        )
    return False


def _border_equal(border1: gs.Border | None, border2: gs.Border | None) -> bool:
    if border1 is None and border2 is None:
        return True
    if border1 is not None and border2 is not None:
        return (
            _color_style_equal(border1.get("colorStyle"), border2.get("colorStyle"))
            and border1.get("style") == border2.get("style")
            and border1.get("width") == border2.get("width")
        )
    return False


def _color_style_equal(bg1: gs.ColorStyle | None, bg2: gs.ColorStyle | None) -> bool:
    return color_object_to_string(bg1) == color_object_to_string(bg2)


def _text_format_equal(tf1: gs.TextFormat | None, tf2: gs.TextFormat | None) -> bool:
    if tf1 is None and tf2 is None:
        return True
    if tf1 is not None and tf2 is not None:
        return (
            _color_style_equal(
                tf1.get("foregroundColorStyle"), tf2.get("foregroundColorStyle")
            )
            and tf1.get("fontFamily") == tf2.get("fontFamily")
            and tf1.get("fontSize") == tf2.get("fontSize")
            and tf1.get("link") == tf2.get("link")
            and tf1.get("bold", False) == tf2.get("bold", False)
            and tf1.get("italic", False) == tf2.get("italic", False)
            and tf1.get("strikethrough", False) == tf2.get("strikethrough", False)
            and tf1.get("underline", False) == tf2.get("underline", False)
        )
    return False
