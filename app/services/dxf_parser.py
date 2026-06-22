import math
import re
from pathlib import Path
from typing import Any

import ezdxf

from app.services.drawing_parameters import (
    TOOTH_TYPES,
    common_normal_value_from_text,
    first_int_value,
    normalize_teeth_text,
    normalize_tooth_type,
    plain_float_value,
)


def _clean_mtext(raw: str) -> str:
    s = raw
    s = re.sub(r"\\[Tt]\d+(?:\.\d+)?;", "", s)
    s = re.sub(r"\\[Aa]\d+;", "", s)
    s = re.sub(r"\\[Hh][^;]*;", "", s)
    s = re.sub(r"\\[Ss][^;]*;", "", s)
    s = re.sub(r"\\[Ff][^;]*;", "", s)
    s = re.sub(r"\\[Cc]\d+;", "", s)
    s = re.sub(r"\\[Ww][^;]*;", "", s)
    s = re.sub(r"\\[Oo]", "", s)
    s = re.sub(r"\\[Ll]", "", s)
    s = re.sub(r"[{}]", "", s)
    s = s.replace("\\P", " ")
    s = s.replace("%%D", "°")
    s = s.replace("%%C", "φ").replace("%%c", "φ")
    s = s.replace("%%P", "±")
    return s.strip()


def _entity_text(entity: Any) -> str | None:
    if entity.dxftype() == "TEXT":
        return _clean_mtext(entity.dxf.text or "")
    if entity.dxftype() == "MTEXT":
        return _clean_mtext(entity.text or "")
    return None


def _parse_number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _parse_signed_number(text: str) -> float | None:
    match = re.search(r"[+-]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _numbers_from_text(text: str) -> list[float]:
    return [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]


def _dimension_text_value(entity: Any) -> tuple[str, float | None]:
    raw_text = entity.dxf.text or ""
    clean = _clean_mtext(raw_text)
    num = _parse_number(clean) if clean else None
    if num is not None:
        return clean, num
    measurement = getattr(entity, "get_measurement", lambda: None)()
    return clean or str(measurement), measurement


def _bbox_from_points(points: list[tuple[float, float]]) -> dict[str, float | None]:
    if not points:
        return {"min_x": None, "min_y": None, "max_x": None, "max_y": None, "width": None, "height": None}
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y, "width": max_x - min_x, "height": max_y - min_y}


_GEAR_PATTERNS: list[tuple[str, str, list[str]]] = [
    ("teeth_count", "int", [r"[Zz]\s*[=:：]\s*(\d+)", r"齿数\s*[=:：]?\s*(\d+)"]),
    ("module", "float", [r"[Mm]\s*[=:：]\s*(\d+(?:\.\d+)?)", r"模数\s*[=:：]?\s*(\d+(?:\.\d+)?)"]),
    ("pressure_angle", "float", [r"[αa]\s*[=:：]\s*(\d+(?:\.\d+)?)", r"压力角\s*[=:：]?\s*(\d+(?:\.\d+)?)"]),
    ("span_teeth_count", "int", [r"跨齿数\s*[=:：]?\s*(\d+)", r"[Kk]\s*[=:：]\s*(\d+)"]),
    ("common_normal_length", "float", [r"公法线\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"[Ww][Kk]?\s*[=:：]\s*(\d+(?:\.\d+)?)"]),
    ("pin_diameter", "float", [r"量棒直径\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"棒径\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"[Dd][Pp]\s*[=:：]\s*(\d+(?:\.\d+)?)"]),
    ("pin_span", "float", [r"棒间距\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"[Mm][Dd]\s*[=:：]\s*(\d+(?:\.\d+)?)"]),
    ("product_thickness", "float", [r"产品厚度\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"总厚\s*[=:：]?\s*(\d+(?:\.\d+)?)"]),
    ("plate_thickness", "float", [r"钢板厚度\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"板厚\s*[=:：]?\s*(\d+(?:\.\d+)?)", r"基板\s*[=:：]?\s*(\d+(?:\.\d+)?)"]),
]


def _nearest_right_text(positioned: list[dict[str, Any]], label: dict[str, Any], max_dx: float = 90, max_dy: float = 4.5) -> str | None:
    label_x = float(label["x"])
    label_y = float(label["y"])
    candidates: list[tuple[float, str]] = []
    for item in positioned:
        item_raw = str(item.get("raw", "")).strip()
        if not item_raw:
            continue
        dx = float(item["x"]) - label_x
        dy = abs(float(item["y"]) - label_y)
        if 0 < dx <= max_dx and dy <= max_dy:
            candidates.append((dx + dy * 4, item_raw))
    return sorted(candidates, key=lambda pair: pair[0])[0][1] if candidates else None


def _split_tooth_type_and_count(value: str) -> tuple[str | None, str | None]:
    text = value.strip().upper().replace(" ", "")
    match = re.search(rf"\b({'|'.join(TOOTH_TYPES)})(\d+(?:\(\d+\))?)\b", text)
    if match:
        return normalize_tooth_type(match.group(1)), normalize_teeth_text(match.group(2))
    match = re.search(r"\d+(?:\(\d+\))?", text)
    return None, normalize_teeth_text(match.group()) if match else None


def _extract_table_pairs(text_entities: list[dict[str, Any]]) -> dict[str, Any]:
    label_map = {
        "z": ("teeth_count", "int"),
        "齿  数": ("teeth_count", "int"),
        "齿数": ("teeth_count", "int"),
        "m": ("module", "float"),
        "模  数": ("module", "float"),
        "模数": ("module", "float"),
        "a": ("pressure_angle", "float"),
        "α": ("pressure_angle", "float"),
        "压力角": ("pressure_angle", "float"),
        "压力角α": ("pressure_angle", "float"),
        "压力角a": ("pressure_angle", "float"),
        "x": ("profile_shift_coefficient", "float"),
        "变位系数": ("profile_shift_coefficient", "float"),
        "M": ("pin_span", "float"),
        "M  值": ("pin_span", "float"),
        "M值": ("pin_span", "float"),
        "L": ("common_normal_length", "float"),
        "l": ("common_normal_length", "float"),
        "公法线长度": ("common_normal_length", "float"),
        "n": ("span_teeth_count", "int"),
        "跨齿数": ("span_teeth_count", "int"),
        "dp": ("pin_diameter", "float"),
        "标准量棒": ("pin_diameter", "float"),
        "标准量棒dp": ("pin_diameter", "float"),
    }
    result: dict[str, Any] = {}
    positioned = [item for item in text_entities if item.get("x") is not None and item.get("y") is not None]
    for label in positioned:
        raw = str(label.get("raw", "")).strip()
        if raw not in label_map:
            continue
        field, field_type = label_map[raw]
        raw_value = _nearest_right_text(positioned, label)
        if raw_value:
            if field == "teeth_count":
                tooth_type, teeth_text = _split_tooth_type_and_count(raw_value)
                if tooth_type:
                    result["tooth_type"] = tooth_type
                if teeth_text:
                    result["teeth_count_text"] = teeth_text
                    result["teeth_count"] = first_int_value(teeth_text)
            elif field == "module":
                result["module_text"] = raw_value.strip()
                module_value = plain_float_value(raw_value)
                if module_value is not None and 0.5 <= module_value <= 20:
                    result["module"] = module_value
            elif field == "common_normal_length":
                result["common_normal_length_text"] = raw_value.strip()
                normal_value = common_normal_value_from_text(raw_value, result.get("tooth_type"))
                if normal_value is not None:
                    result["common_normal_length"] = normal_value
        candidates = []
        label_x = float(label["x"])
        label_y = float(label["y"])
        for item in positioned:
            item_raw = str(item.get("raw", "")).strip()
            value = _parse_signed_number(item_raw)
            if value is None:
                continue
            dx = float(item["x"]) - label_x
            dy = abs(float(item["y"]) - label_y)
            if 0 < dx <= 70 and dy <= 4.5:
                if field_type == "int" and not float(value).is_integer():
                    continue
                if field == "pressure_angle" and "°" not in item_raw and value not in (14.5, 20, 25, 30):
                    continue
                if field == "module" and not 0.5 <= value <= 20:
                    continue
                if field == "pin_diameter" and not 0.5 <= value <= 50:
                    continue
                if field == "pin_span" and not 5 <= value <= 500:
                    continue
                if field == "span_teeth_count" and (value < 1 or value > 50 or item_raw.startswith(("+", "-"))):
                    continue
                candidates.append((dx + dy * 4, value))
        if candidates:
            value = sorted(candidates, key=lambda pair: pair[0])[0][1]
            result[field] = int(value) if field_type == "int" else value
            if field == "teeth_count":
                result.setdefault("teeth_count_text", str(int(value)))
            elif field == "module":
                result.setdefault("module_text", f"{value:g}")
            elif field == "common_normal_length":
                result.setdefault("common_normal_length_text", f"{value:g}")
    if result.get("common_normal_length_text"):
        normal_value = common_normal_value_from_text(result["common_normal_length_text"], result.get("tooth_type"))
        if normal_value is not None:
            result["common_normal_length"] = normal_value
    return result


def _extract_gear_candidates(texts: list[str], text_entities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if text_entities:
        result.update(_extract_table_pairs(text_entities))
    joined = "  ".join(texts)
    if "teeth_count_text" not in result:
        tooth_match = re.search(rf"\b({'|'.join(TOOTH_TYPES)})\s*(\d+\s*(?:\(\s*\d+\s*\))?)\b", joined, re.IGNORECASE)
        if tooth_match:
            result["tooth_type"] = normalize_tooth_type(tooth_match.group(1))
            result["teeth_count_text"] = normalize_teeth_text(tooth_match.group(2))
            result["teeth_count"] = first_int_value(result["teeth_count_text"])
    if "module_text" not in result:
        module_match = re.search(r"(?:模数|[Mm]\s*[=:：])\s*([A-Za-z]{1,6}\d*|\d+(?:\.\d+)?)", joined)
        if module_match:
            module_text = module_match.group(1).strip()
            result["module_text"] = module_text
            module_value = plain_float_value(module_text)
            if module_value is not None and 0.5 <= module_value <= 20:
                result["module"] = module_value
    if "common_normal_length_text" not in result:
        normal_match = re.search(
            r"(?:公法线(?:长度)?|[Ww][Kk]?\s*[=:：]|[Ll]\s*[=:：])\s*(\d+(?:\.\d+)?\s*(?:[-~—至]\s*\d+(?:\.\d+)?)?)",
            joined,
        )
        if normal_match:
            normal_text = normal_match.group(1).strip()
            result["common_normal_length_text"] = normal_text
            normal_value = common_normal_value_from_text(normal_text, result.get("tooth_type"))
            if normal_value is not None:
                result["common_normal_length"] = normal_value
    for field, field_type, patterns in _GEAR_PATTERNS:
        if field in result:
            continue
        for pattern in patterns:
            match = re.search(pattern, joined)
            if match:
                try:
                    result[field] = int(match.group(1)) if field_type == "int" else float(match.group(1))
                    if field == "teeth_count":
                        result.setdefault("teeth_count_text", str(result[field]))
                    elif field == "module":
                        result.setdefault("module_text", match.group(1))
                    elif field == "common_normal_length":
                        result.setdefault("common_normal_length_text", match.group(1))
                except ValueError:
                    pass
                break
    if "pressure_angle" not in result:
        for text in texts:
            if "°" in text:
                value = _parse_number(text)
                if value in (14.5, 20, 25, 30):
                    result["pressure_angle"] = value
                    break
    if "teeth_count" not in result:
        integers = []
        for text in texts:
            if re.fullmatch(r"\d+", text.strip()):
                value = int(text)
                if 10 <= value <= 300:
                    integers.append(value)
        if integers:
            result["teeth_count"] = integers[0]
            result.setdefault("teeth_count_text", str(integers[0]))
    if result.get("common_normal_length_text"):
        normal_value = common_normal_value_from_text(result["common_normal_length_text"], result.get("tooth_type"))
        if normal_value is not None:
            result["common_normal_length"] = normal_value
    return result


def _numeric_candidates(texts: list[str], dimensions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    text_numbers = []
    for text in texts:
        for value in _numbers_from_text(text):
            text_numbers.append(
                {
                    "raw": text,
                    "value": value,
                    "is_integer": float(value).is_integer(),
                    "has_degree": "°" in text,
                    "is_range": "-" in text or "~" in text,
                }
            )
    dimension_numbers = []
    for dim in dimensions:
        value = dim.get("value")
        if value is not None:
            dimension_numbers.append({"raw": dim.get("raw"), "value": round(float(value), 6), "type": dim.get("type")})
    return {"text_numbers": text_numbers, "dimension_numbers": dimension_numbers}


def _infer_part_type(file_path: str, texts: list[str]) -> str:
    file_name = Path(file_path).name
    if "外钢片" in file_name:
        return "outer_steel_ring"
    if "内纸片" in file_name:
        return "inner_paper_steel_core"
    if "纸片" in file_name:
        return "paper_ring"
    source = " ".join(texts[:50])
    if "外钢片" in source:
        return "outer_steel_ring"
    if "内纸片" in source:
        return "inner_paper_steel_core"
    if "纸片" in source:
        return "paper_ring"
    return "unknown"


def _extract_thickness_candidates(part_type: str, dimensions: list[dict[str, Any]]) -> dict[str, float | None]:
    values = []
    for dim in dimensions:
        raw = str(dim.get("raw", ""))
        value = dim.get("value")
        if value is None or "φ" in raw or "-" in raw:
            continue
        try:
            number = round(float(value), 6)
        except (TypeError, ValueError):
            continue
        if 0.5 <= number <= 6:
            values.append(number)
    unique_values = sorted(set(round(value, 3) for value in values))
    if not unique_values:
        return {}
    if part_type == "inner_paper_steel_core":
        return {
            "plate_thickness": unique_values[0],
            "product_thickness": unique_values[-1],
        }
    if part_type == "outer_steel_ring":
        return {
            "plate_thickness": unique_values[-1],
            "product_thickness": unique_values[-1],
        }
    if part_type == "paper_ring":
        return {
            "product_thickness": unique_values[-1],
        }
    return {"product_thickness": unique_values[-1]}


def parse_dxf(file_path: str) -> dict[str, Any]:
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    texts: list[str] = []
    text_entities: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    circles: list[dict[str, float]] = []
    points: list[tuple[float, float]] = []

    def append_text(value: str, entity: Any, source: str) -> None:
        clean = _clean_mtext(value or "")
        if not clean:
            return
        texts.append(clean)
        insert = getattr(entity.dxf, "insert", None)
        text_entities.append(
            {
                "raw": clean,
                "source": source,
                "x": round(float(insert.x), 6) if insert is not None else None,
                "y": round(float(insert.y), 6) if insert is not None else None,
                "layer": getattr(entity.dxf, "layer", None),
                "type": entity.dxftype(),
            }
        )

    def process_entity(entity: Any, depth: int = 0) -> None:
        dxftype = entity.dxftype()
        text = _entity_text(entity)
        if text:
            texts.append(text)
            insert = getattr(entity.dxf, "insert", None)
            text_entities.append(
                {
                    "raw": text,
                    "source": "entity",
                    "x": round(float(insert.x), 6) if insert is not None else None,
                    "y": round(float(insert.y), 6) if insert is not None else None,
                    "layer": getattr(entity.dxf, "layer", None),
                    "type": dxftype,
                }
            )

        if dxftype == "DIMENSION":
            dim_text, dim_value = _dimension_text_value(entity)
            dimensions.append({"raw": dim_text, "value": dim_value, "type": "dimension"})
            if depth < 4:
                for virtual_entity in entity.virtual_entities():
                    process_entity(virtual_entity, depth + 1)

        if dxftype == "INSERT" and depth < 4:
            for attrib in getattr(entity, "attribs", []):
                append_text(attrib.dxf.text or "", attrib, "attribute")
            for virtual_entity in entity.virtual_entities():
                process_entity(virtual_entity, depth + 1)

        if dxftype == "CIRCLE":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            diameter = radius * 2
            circles.append({"center_x": float(center.x), "center_y": float(center.y), "radius": radius, "diameter": diameter})
            points.extend([(center.x - radius, center.y - radius), (center.x + radius, center.y + radius)])

        if dxftype == "ARC":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            points.extend([(center.x - radius, center.y - radius), (center.x + radius, center.y + radius)])

        if dxftype == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            points.extend([(float(start.x), float(start.y)), (float(end.x), float(end.y))])

        if dxftype == "LWPOLYLINE":
            points.extend([(float(p[0]), float(p[1])) for p in entity.get_points()])

        if dxftype == "POLYLINE":
            points.extend([(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices])

        if dxftype in {"SPLINE", "ELLIPSE"}:
            try:
                points.extend([(float(point.x), float(point.y)) for point in entity.flattening(0.5)])
            except Exception:
                pass

    for entity in msp:
        process_entity(entity)

    diameter_dimensions = []
    for t in texts:
        if "φ" in t or "%%c" in t.lower():
            value = _parse_number(t)
            if value:
                diameter_dimensions.append({"raw": t, "value": value, "type": "diameter"})
    for dim in dimensions:
        raw = dim.get("raw", "")
        if "φ" in raw:
            value = _parse_number(raw)
            if value and value not in [d["value"] for d in diameter_dimensions]:
                diameter_dimensions.append({"raw": raw, "value": value, "type": "diameter"})

    all_diameters = [circle["diameter"] for circle in circles] + [item["value"] for item in diameter_dimensions]
    max_diameter = max(all_diameters) if all_diameters else None
    inner_candidates = sorted(set([round(value, 6) for value in all_diameters if max_diameter and value < max_diameter]))
    bbox = {key: (round(value, 6) if isinstance(value, float) else value) for key, value in _bbox_from_points(points).items()}

    gear_candidates = _extract_gear_candidates(texts, text_entities)
    numeric_candidates = _numeric_candidates(texts, dimensions)
    part_type = _infer_part_type(file_path, texts)
    for key, value in _extract_thickness_candidates(part_type, dimensions).items():
        gear_candidates.setdefault(key, value)

    return {
        "file_name": Path(file_path).name,
        "part_type": part_type,
        "texts": texts[:200],
        "text_entities": text_entities[:300],
        "dimensions": dimensions[:200] + diameter_dimensions[:100],
        "gear_candidates": gear_candidates,
        "numeric_candidates": numeric_candidates,
        "geometry_summary": {
            "circle_count": len(circles),
            "max_circle_diameter": round(max([circle["diameter"] for circle in circles], default=0), 6) if circles else None,
            "max_detected_diameter": round(max_diameter, 6) if max_diameter else None,
            "inner_diameter_candidates": inner_candidates,
            "bounding_box": bbox,
        },
    }
