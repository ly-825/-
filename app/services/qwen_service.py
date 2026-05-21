import json
from typing import Any

import requests

from app.config import settings


PROMPT = """你是一个齿轮产品机械图纸DXF解析助手。请根据输入的DXF候选文本、尺寸标注和几何摘要，提取产品用料和齿轮参数信息。
要求：
1. 只输出JSON，不要输出解释。
2. 如果字段无法确定，填 null。
3. 所有尺寸单位为 mm，角度单位为度。
4. product_code = 产品型号/图号。
5. product_name = 产品名称。
6. material = 材质/材料。
7. max_outer_diameter = 产品最大外径/齿顶圆直径。
8. inner_related_diameter = 内径/内孔直径。
9. product_thickness = 产品总厚度（含复合材料）。
10. plate_thickness = 钢板/基板厚度。
11. teeth_count = 齿数 z。
12. module = 模数 m。
13. pressure_angle = 压力角 α，常见值 20。
14. profile_shift_coefficient = 变位系数 x。
15. span_teeth_count = 跨齿数 k/n。
16. common_normal_length = 公法线长度 W/L。
17. pin_diameter = 标准量棒/量棒直径 dp。
18. pin_span = M值/棒间距 M（量棒测量值）。
19. expected_scrap_theoretical_size = 圈中间割下来的那一块中心圆料尺寸，按内孔/中心孔直径计算，不是产品外径。
20. expected_scrap_usable_size 应保守计算，通常比中心圆料理想直径小 0.5mm。
21. confidence 取 0 到 1。
22. 如果关键信息缺失，need_manual_review 为 true。

常见关键词对应：
- 齿数/z/Z → teeth_count
- 模数/m/M/模 → module
- 压力角/α → pressure_angle
- 变位系数/x → profile_shift_coefficient
- 跨齿数/k/n → span_teeth_count
- 公法线/W/Wk/L → common_normal_length
- 量棒直径/dp/棒径 → pin_diameter
- M值/棒间距/M/Md → pin_span
- 厚度/δ/t → product_thickness 或 plate_thickness
- 外径/Da/齿顶圆 → max_outer_diameter
- 内径/Di/内孔 → inner_related_diameter

保守识别规则：
- 如果 DXF 文本里只有裸数字，没有字段名称，不能随便猜。
- 裸整数 10~300 且出现在普通文本中，优先作为 teeth_count 候选；如果只有一个这样的整数，可以填 teeth_count。
- 带 ° 的 14.5/20/25/30 优先作为 pressure_angle。
- 0.2、0.35、+0.037 等小数通常可能是公差或槽深，不能直接当 product_thickness 或 plate_thickness，除非文字明确写了厚度/板厚/钢板厚度。
- 1.2、2.3、4.5、2.5 等裸尺寸没有标签时，不能直接填 module、pin_diameter、common_normal_length。
- max_outer_diameter 和 inner_related_diameter 优先使用 dimensions 中带 φ 的标注；没有 φ 时再参考 geometry_summary。
- 余料尺寸指圆环中间切下来的圆料，必须根据 inner_related_diameter/中心孔直径估计，不能用 max_outer_diameter。
- bounding_length/bounding_width 是图纸外接框，不是产品参数，除非用户明确要求不要作为识别结果置信来源。

输出字段：product_code, product_name, material, product_thickness, plate_thickness, max_outer_diameter, inner_related_diameter, teeth_count, module, pressure_angle, profile_shift_coefficient, span_teeth_count, common_normal_length, pin_diameter, pin_span, suggested_material_shape, minimum_material_size, expected_scrap_type, expected_scrap_theoretical_size, expected_scrap_usable_size, confidence, need_manual_review。
"""


def _fallback_from_candidates(candidates: dict[str, Any]) -> dict[str, Any]:
    summary = candidates.get("geometry_summary", {})
    gear = candidates.get("gear_candidates", {})
    part_diameters = _part_diameters(candidates)
    max_diameter = part_diameters.get("outer") or summary.get("max_detected_diameter")
    inner = part_diameters.get("inner")
    if inner is None:
        inner_candidates = summary.get("inner_diameter_candidates") or []
        inner = inner_candidates[-1] if inner_candidates else None
    bbox = summary.get("bounding_box") or {}
    return {
        "product_code": None,
        "product_name": None,
        "material": None,
        "thickness": None,
        "product_thickness": None,
        "plate_thickness": None,
        "max_outer_diameter": max_diameter,
        "inner_related_diameter": inner,
        "teeth_count": gear.get("teeth_count"),
        "module": gear.get("module"),
        "pressure_angle": gear.get("pressure_angle"),
        "profile_shift_coefficient": gear.get("profile_shift_coefficient"),
        "span_teeth_count": gear.get("span_teeth_count"),
        "common_normal_length": gear.get("common_normal_length"),
        "pin_diameter": gear.get("pin_diameter"),
        "pin_span": gear.get("pin_span"),
        "suggested_material_shape": "circle_or_rectangle" if max_diameter else None,
        "minimum_material_size": f"φ{max_diameter:g} 或 {max_diameter:g}×{max_diameter:g}" if max_diameter else None,
        "expected_scrap_type": "中心余料" if inner else None,
        "expected_scrap_theoretical_size": f"φ{inner:g}" if inner else None,
        "expected_scrap_usable_size": f"φ{inner - 0.5:g}" if inner else None,
        "bounding_length": bbox.get("width"),
        "bounding_width": bbox.get("height"),
        "confidence": 0.45,
        "need_manual_review": True,
    }


def _diameter_values(candidates: dict[str, Any]) -> list[float]:
    values = []
    for item in candidates.get("dimensions", []):
        raw = str(item.get("raw", ""))
        value = item.get("value")
        if value is None:
            continue
        if "φ" in raw or item.get("type") == "diameter":
            try:
                values.append(round(float(value), 6))
            except (TypeError, ValueError):
                pass
    return sorted(set(values))


def _part_diameters(candidates: dict[str, Any]) -> dict[str, float | None]:
    values = _diameter_values(candidates)
    if not values:
        return {"outer": None, "inner": None}
    return {"outer": max(values), "inner": min(values) if len(values) > 1 else None}


def _center_scrap_diameter(candidates: dict[str, Any], result: dict[str, Any]) -> float | None:
    part_inner = _part_diameters(candidates).get("inner")
    if part_inner:
        return part_inner
    outer = result.get("max_outer_diameter") or candidates.get("geometry_summary", {}).get("max_detected_diameter")
    diameter_values = _diameter_values(candidates)
    if outer:
        diameter_values = [value for value in diameter_values if value < float(outer)]
    if diameter_values:
        return min(diameter_values)
    inner = result.get("inner_related_diameter")
    try:
        return float(inner) if inner else None
    except (TypeError, ValueError):
        return None


def _apply_conservative_rules(candidates: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    gear = candidates.get("gear_candidates", {})
    part_diameters = _part_diameters(candidates)
    if part_diameters.get("outer"):
        result["max_outer_diameter"] = part_diameters["outer"]
    if part_diameters.get("inner"):
        result["inner_related_diameter"] = part_diameters["inner"]
    for field in ("product_thickness", "plate_thickness", "teeth_count", "module", "pressure_angle", "profile_shift_coefficient", "span_teeth_count", "common_normal_length", "pin_diameter", "pin_span"):
        if gear.get(field) is not None:
            result[field] = gear[field]

    if not gear.get("product_thickness") and result.get("product_thickness") is not None:
        try:
            if float(result["product_thickness"]) <= 0.5:
                result["product_thickness"] = None
        except (TypeError, ValueError):
            result["product_thickness"] = None
    if not gear.get("plate_thickness") and result.get("plate_thickness") is not None:
        try:
            if float(result["plate_thickness"]) <= 0.5:
                result["plate_thickness"] = None
        except (TypeError, ValueError):
            result["plate_thickness"] = None
    scrap_diameter = _center_scrap_diameter(candidates, result)
    if scrap_diameter:
        usable_diameter = max(scrap_diameter - 0.5, 0)
        result["expected_scrap_type"] = "中心圆料"
        result["expected_scrap_theoretical_size"] = f"φ{scrap_diameter:g}"
        result["expected_scrap_usable_size"] = f"φ{usable_diameter:g}"
    return result


def recognize_drawing(candidates: dict[str, Any]) -> dict[str, Any]:
    if not settings.dashscope_api_key:
        return _apply_conservative_rules(candidates, _fallback_from_candidates(candidates))

    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings.dashscope_api_key}", "Content-Type": "application/json"}
    payload = {
        "model": settings.qwen_model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(candidates, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    result.setdefault("confidence", 0.5)
    result.setdefault("need_manual_review", True)
    return _apply_conservative_rules(candidates, result)
