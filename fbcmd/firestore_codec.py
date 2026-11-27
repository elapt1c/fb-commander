"""Firestore REST value decoding (typed values -> plain Python)."""

from __future__ import annotations

import base64
from typing import Any


def decode_value(value: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        try:
            return int(value["integerValue"])
        except (TypeError, ValueError):
            return value["integerValue"]
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "timestampValue" in value:
        return value["timestampValue"]
    if "stringValue" in value:
        return value["stringValue"]
    if "bytesValue" in value:
        try:
            return {"$bytes_base64": base64.b64decode(value["bytesValue"]).decode("latin-1")}
        except Exception:  # noqa: BLE001 - show the raw value rather than fail
            return value["bytesValue"]
    if "referenceValue" in value:
        return {"$ref": value["referenceValue"]}
    if "geoPointValue" in value:
        return {"$geo": value["geoPointValue"]}
    if "arrayValue" in value:
        return [decode_value(v) for v in (value["arrayValue"] or {}).get("values", [])]
    if "mapValue" in value:
        return decode_fields((value["mapValue"] or {}).get("fields", {}))
    return value


def decode_fields(fields: dict[str, Any] | None) -> dict[str, Any]:
    return {name: decode_value(value) for name, value in (fields or {}).items()}


def decode_document(doc: dict[str, Any]) -> dict[str, Any]:
    body = decode_fields(doc.get("fields"))
    meta = {
        "_name": doc.get("name", ""),
        "_createTime": doc.get("createTime"),
        "_updateTime": doc.get("updateTime"),
    }
    if doc.get("readTime"):
        meta["_readTime"] = doc["readTime"]
    return {"data": body, "meta": {k: v for k, v in meta.items() if v is not None}}


def doc_id(name: str) -> str:
    return str(name).rsplit("/", 1)[-1]
