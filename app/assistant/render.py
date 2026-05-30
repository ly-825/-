from __future__ import annotations


def table(title: str, columns: list[dict], rows: list[dict], data_type: str = "table") -> dict:
    return {"type": data_type, "title": title, "columns": columns, "rows": rows}

