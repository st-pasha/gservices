from __future__ import annotations
import re


def rich_print(text: str) -> None:
    rich.print(text)


def plain_print(text: str) -> None:
    # remove color decorations
    text = re.sub(r"(\\(?=\[))|((?<!\\)\[.*?\])", "", text)
    print(text)


try:
    import rich

    pprint = rich_print
except ImportError:
    pprint = plain_print
