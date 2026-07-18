from collections.abc import Iterable


ParameterValue = tuple[str, str]
ParameterDisplay = tuple[str, str, bool]


def _present(values: Iterable[ParameterValue]) -> list[ParameterValue]:
    return [
        (str(label).strip(), str(value).strip())
        for label, value in values
        if str(label).strip() and str(value).strip()
    ]


def keyword_parameter_matches(
    keyword: str,
    values: list[ParameterValue],
) -> list[ParameterValue]:
    needle = keyword.strip().casefold()
    if not needle:
        return []
    return [
        (label, value)
        for label, value in _present(values)
        if needle in value.casefold()
    ]


def build_parameter_summary(
    matched: list[ParameterValue],
    defaults: list[ParameterValue],
    limit: int = 5,
) -> list[ParameterDisplay]:
    result: list[ParameterDisplay] = []
    seen: set[str] = set()
    for is_matched, values in ((True, matched), (False, defaults)):
        for label, value in _present(values):
            if label in seen:
                continue
            seen.add(label)
            result.append((label, value, is_matched))
            if len(result) >= limit:
                return result
    return result
