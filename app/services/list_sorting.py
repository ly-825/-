from collections.abc import Callable, Iterable, Mapping
from html import escape
from typing import Any


def sort_records(
    records: Iterable[Any],
    sort_by: str,
    sort_dir: str,
    key_map: Mapping[str, Callable[[Any], Any]],
) -> tuple[list[Any], str, str]:
    rows = list(records)
    field = (sort_by or "").strip()
    direction = (sort_dir or "").strip().lower()
    if field not in key_map or direction not in {"asc", "desc"}:
        return rows, "", ""

    key_fn = key_map[field]
    valued = [row for row in rows if key_fn(row) not in (None, "")]
    empty = [row for row in rows if key_fn(row) in (None, "")]
    valued.sort(key=key_fn, reverse=direction == "desc")
    return valued + empty, field, direction


def sort_select_options(options: Mapping[str, str], selected: str) -> str:
    return "".join(
        f"<option value='{escape(value)}' {'selected' if value == selected else ''}>{escape(label)}</option>"
        for value, label in options.items()
    )
