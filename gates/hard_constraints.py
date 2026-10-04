"""Deterministic price and selectable-option checks; no model may override them."""

import re
from decimal import Decimal, InvalidOperation


NUMBER = r"\$?\s*(\d[\d,]*(?:\.\d+)?)"


def decimal(value):
    try:
        result = Decimal(str(value).replace(",", "").replace("$", "").strip())
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def money(value):
    return format(value.normalize(), "f")


def parse_price_constraint(text):
    """Return inclusive/exclusive bounds, preserving decimals and strict 'under'."""
    if not text:
        return None
    text = str(text).lower()
    between = re.search(r"(?:between|from)\s+" + NUMBER + r"\s*(?:and|to|-)\s*" + NUMBER, text)
    if between:
        low, high = map(decimal, between.groups())
        if low is not None and high is not None and low <= high:
            return {"min": money(low), "max": money(high), "min_inclusive": True, "max_inclusive": True}
        return None
    bounds = {"min": None, "max": None, "min_inclusive": True, "max_inclusive": True}
    patterns = [
        ("max", True, r"(?:at most|no more than|up to|<=)\s*" + NUMBER),
        ("max", False, r"(?:under|below|(?<!no )less than|lower than|<)\s*" + NUMBER),
        ("min", True, r"(?:at least|no less than|>=)\s*" + NUMBER),
        ("min", False, r"(?:over|above|(?<!no )more than|greater than|>)\s*" + NUMBER),
    ]
    for key, inclusive, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = decimal(match.group(1))
            if value is not None:
                bounds[key], bounds[key + "_inclusive"] = money(value), inclusive
    if bounds["min"] is None and bounds["max"] is None:
        match = re.search(r"(?:exactly|equal to|=)\s*" + NUMBER, text)
        if not match:
            return None
        value = decimal(match.group(1))
        if value is None:
            return None
        bounds["min"] = bounds["max"] = money(value)
    if bounds["min"] is not None and bounds["max"] is not None:
        if decimal(bounds["min"]) > decimal(bounds["max"]):
            return None
    return bounds


def price_satisfies(price, bounds):
    value = decimal(price)
    if value is None or not bounds:
        return False
    for key, direction in (("min", 1), ("max", -1)):
        if bounds.get(key) is None:
            continue
        limit = decimal(bounds[key])
        comparison = (value - limit) * direction
        if comparison < 0 or (comparison == 0 and not bounds.get(key + "_inclusive", True)):
            return False
    return True


def visible_price(text):
    # Prefer the actual price label. An unrelated $ amount in reviews is not a price.
    match = re.search(r"\bprice\s*:\s*\$?\s*(\d[\d,]*(?:\.\d+)?)", str(text), re.I)
    return str(decimal(match.group(1))) if match else None


def check_price(constraint, product_text):
    price = visible_price(product_text)
    if not constraint:
        return {"status": "NOT_REQUIRED", "visible_price": price}
    bounds = parse_price_constraint(constraint)
    if price is None or bounds is None:
        return {"status": "MISSING", "visible_price": price, "bounds": bounds}
    return {"status": "SUPPORTED" if price_satisfies(price, bounds) else "CONTRADICTED",
            "visible_price": price, "bounds": bounds}


def normalize_option(value):
    # Keep numbers and units intact: size 10 is not size 1; blue is not light blue.
    return re.sub(r"\s+", " ", str(value).strip().casefold())


def option_values(constraint):
    values = [constraint.get("value"), constraint.get("canonical"), constraint.get("source_text")]
    values += constraint.get("aliases", [])
    clean = set()
    for value in values:
        if value:
            clean.add(normalize_option(re.sub(
                r"^(?:color|size|style|scent|brand|pack|count|variant)\s*[:=]\s*", "", str(value), flags=re.I)))
    return clean


def check_option(constraint, option_groups, selected_options):
    kind = constraint.get("kind", "option")
    candidates = option_values(constraint)
    groups = {key: values for key, values in option_groups.items()
              if kind in ("option", "attribute") or normalize_option(key) == normalize_option(kind)}
    matching = [(key, value) for key, values in groups.items() for value in values
                if normalize_option(value) in candidates]
    selected = [(key, value) for key, value in selected_options.items()
                if any(key == group and normalize_option(value) == normalize_option(available)
                       for group, available in matching)]
    return {"constraint": constraint.get("canonical", ""), "kind": kind,
            "selected": bool(selected), "available": bool(matching),
            "matching_groups": list(dict.fromkeys(key for key, _ in matching)),
            "status": "SUPPORTED" if selected else "MISSING",
            "available_action": matching[0][1] if matching else None}
