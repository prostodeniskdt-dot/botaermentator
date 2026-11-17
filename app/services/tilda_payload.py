"""Bounded expansion of Tilda's PHP-style form keys; no inferred payment values."""

import re
from copy import deepcopy

_BRACKET_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[A-Za-z0-9_]+\])+$")


def normalize_tilda_payload(payload: dict) -> dict:
    result = deepcopy({key: value for key, value in payload.items() if "[" not in key})
    roots = set(result)
    for key, value in payload.items():
        if "[" not in key:
            continue
        if not _BRACKET_KEY.fullmatch(key):
            raise ValueError("unsupported_form_key")
        parts = key.replace("]", "").replace("[", ".").split(".")
        if parts[0] in roots or len(parts) > 6:
            raise ValueError("ambiguous_form_fields")
        node = result
        for part in parts[:-1]:
            if part not in node:
                node[part] = {}
            if not isinstance(node[part], dict):
                raise ValueError("ambiguous_form_fields")
            node = node[part]
        if parts[-1] in node:
            raise ValueError("ambiguous_form_fields")
        node[parts[-1]] = value
    payment = result.get("payment")
    if isinstance(payment, dict) and isinstance(payment.get("products"), dict):
        products = payment["products"]
        if (
            not products
            or len(products) > 20
            or set(products) != {str(n) for n in range(len(products))}
        ):
            raise ValueError("unsupported_product_indices")
        payment["products"] = [products[str(n)] for n in range(len(products))]
    return result
