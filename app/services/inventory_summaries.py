from collections.abc import Callable
from typing import Any

from app.models import MaterialInventory, ScrapGenerationRecord
from app.services.drawing_search import natural_sort_key


SummaryRow = dict[str, Any]
SummarySortMap = dict[str, Callable[[SummaryRow], Any]]


def _latest_time(item: MaterialInventory):
    return item.updated_at or item.created_at


def _minimum_value(values: set):
    present = [value for value in values if value is not None]
    return min(present) if present else None


def product_summary_rows(items: list[MaterialInventory]) -> list[SummaryRow]:
    grouped: dict[str, SummaryRow] = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        group = grouped.setdefault(
            code,
            {
                "code": code,
                "material": item.material,
                "product_thicknesses": set(),
                "plate_thicknesses": set(),
                "paper_materials": set(),
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
                "latest": _latest_time(item),
            },
        )
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        group["product_thicknesses"].add(item.product_thickness or item.thickness)
        group["plate_thicknesses"].add(item.plate_thickness or item.thickness)
        if item.paper_material:
            group["paper_materials"].add(item.paper_material)
        if item.location:
            group["locations"].add(item.location)
        item_time = _latest_time(item)
        if item_time and (not group["latest"] or item_time > group["latest"]):
            group["latest"] = item_time
    return list(grouped.values())


def raw_plate_summary_rows(
    items: list[MaterialInventory],
    spec_names: dict[tuple, str],
) -> list[SummaryRow]:
    grouped: dict[tuple, SummaryRow] = {}
    for item in items:
        if item.quantity <= 0:
            continue
        spec_key = (item.material, item.length, item.width, item.thickness)
        model = item.raw_plate_model or spec_names.get(spec_key) or "临时规格"
        key = (model, *spec_key)
        group = grouped.setdefault(
            key,
            {
                "spec_name": model,
                "material": item.material,
                "length": item.length,
                "width": item.width,
                "thickness": item.thickness,
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
                "latest": _latest_time(item),
            },
        )
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        if item.location:
            group["locations"].add(item.location)
        item_time = _latest_time(item)
        if item_time and (not group["latest"] or item_time > group["latest"]):
            group["latest"] = item_time
    return list(grouped.values())


def resolved_raw_plate_model(
    item: MaterialInventory,
    spec_names: dict[tuple, str],
) -> str:
    key = (item.material, item.length, item.width, item.thickness)
    return item.raw_plate_model or spec_names.get(key) or "临时规格"


def scrap_summary_rows(
    records: list[ScrapGenerationRecord],
    scrap_map: dict[int, MaterialInventory],
) -> list[SummaryRow]:
    grouped: dict[tuple, SummaryRow] = {}
    seen_inventory_ids: set[int] = set()
    for record in records:
        item = scrap_map.get(record.scrap_inventory_id)
        if not item or item.status != "available" or item.id in seen_inventory_ids:
            continue
        seen_inventory_ids.add(item.id)
        size_label = item.usable_size or (f"φ{item.diameter:g}" if item.diameter is not None else "-")
        key = (item.material, item.thickness, size_label)
        group = grouped.setdefault(
            key,
            {
                "material": item.material,
                "thickness": item.thickness,
                "diameter": item.diameter,
                "usable_size": size_label,
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
                "latest": _latest_time(item),
            },
        )
        group["quantity"] += item.quantity
        group["batch_count"] += 1
        if item.location and item.location not in ("待入库", "未入库"):
            group["locations"].add(item.location)
        item_time = _latest_time(item)
        if item_time and (not group["latest"] or item_time > group["latest"]):
            group["latest"] = item_time
    return list(grouped.values())


def product_summary_sort_key_map() -> SummarySortMap:
    return {
        "code": lambda row: natural_sort_key(row["code"] or ""),
        "material": lambda row: natural_sort_key(row["material"] or ""),
        "product_thickness": lambda row: _minimum_value(row["product_thicknesses"]),
        "plate_thickness": lambda row: _minimum_value(row["plate_thicknesses"]),
        "quantity": lambda row: row["quantity"],
        "batch_count": lambda row: row["batch_count"],
        "latest": lambda row: row["latest"],
    }


def raw_plate_summary_sort_key_map() -> SummarySortMap:
    return {
        "spec_name": lambda row: natural_sort_key(row["spec_name"] or ""),
        "material": lambda row: natural_sort_key(row["material"] or ""),
        "length": lambda row: row["length"],
        "width": lambda row: row["width"],
        "thickness": lambda row: row["thickness"],
        "quantity": lambda row: row["quantity"],
        "batch_count": lambda row: row["batch_count"],
        "latest": lambda row: row["latest"],
    }


def scrap_summary_sort_key_map() -> SummarySortMap:
    return {
        "material": lambda row: natural_sort_key(row["material"] or ""),
        "thickness": lambda row: row["thickness"],
        "usable_size": lambda row: natural_sort_key(row["usable_size"] or ""),
        "quantity": lambda row: row["quantity"],
        "batch_count": lambda row: row["batch_count"],
        "latest": lambda row: row["latest"],
    }
