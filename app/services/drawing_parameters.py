import re


TOOTH_TYPES = ("IT", "IL", "IR", "OT", "OL", "OR")


def clean_text_value(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_tooth_type(value: object) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in TOOTH_TYPES else None


def first_int_value(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def plain_float_value(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def common_normal_value_from_text(value: object, tooth_type: object) -> float | None:
    text = str(value or "")
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0]
    normalized_type = normalize_tooth_type(tooth_type) or ""
    if normalized_type.startswith("O"):
        return max(numbers)
    if normalized_type.startswith("I"):
        return min(numbers)
    return numbers[0]


def normalize_teeth_text(value: object) -> str | None:
    text = clean_text_value(value)
    if not text:
        return None
    return re.sub(r"\s+", "", text)
