"""Strict JSON-schema subset used by plugin parameter manifests."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


class SchemaValidationError(ValueError):
    """A configuration value violates a trusted plugin schema."""


def validate_schema_value(
    value: object,
    schema: Mapping[str, object],
    *,
    path: str,
) -> None:
    """Validate the schema keywords supported by platform manifests."""

    if not schema:
        return
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, str(expected_type)):
        raise SchemaValidationError(
            f"{path} must be {_type_description(str(expected_type))}"
        )

    enum = schema.get("enum")
    if enum is not None and value not in tuple(enum):
        raise SchemaValidationError(f"{path} must be one of {tuple(enum)!r}")
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{path} must equal {schema['const']!r}")

    if isinstance(value, Mapping):
        _validate_object(value, schema, path)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        _validate_array(value, schema, path)
    elif isinstance(value, str):
        _validate_string(value, schema, path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, path)


def _validate_object(
    value: Mapping[object, object],
    schema: Mapping[str, object],
    path: str,
) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise TypeError("trusted object schema properties must be an object")
    required = tuple(schema.get("required", ()))
    missing = tuple(name for name in required if name not in value)
    if missing:
        raise SchemaValidationError(f"{path} is missing required fields {missing}")

    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        if not isinstance(key, str):
            raise SchemaValidationError(f"{path} keys must be strings")
        child_path = f"{path}.{key}"
        child_schema = properties.get(key)
        if child_schema is not None:
            validate_schema_value(item, child_schema, path=child_path)
        elif additional is False:
            raise SchemaValidationError(f"{child_path} is not declared by the schema")
        elif isinstance(additional, Mapping):
            validate_schema_value(item, additional, path=child_path)


def _validate_array(
    value: Sequence[object],
    schema: Mapping[str, object],
    path: str,
) -> None:
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if minimum is not None and len(value) < int(minimum):
        raise SchemaValidationError(f"{path} must contain at least {minimum} items")
    if maximum is not None and len(value) > int(maximum):
        raise SchemaValidationError(f"{path} must contain at most {maximum} items")
    item_schema = schema.get("items")
    if isinstance(item_schema, Mapping):
        for index, item in enumerate(value):
            validate_schema_value(item, item_schema, path=f"{path}[{index}]")


def _validate_string(
    value: str,
    schema: Mapping[str, object],
    path: str,
) -> None:
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    if minimum is not None and len(value) < int(minimum):
        raise SchemaValidationError(
            f"{path} must contain at least {minimum} characters"
        )
    if maximum is not None and len(value) > int(maximum):
        raise SchemaValidationError(
            f"{path} must contain at most {maximum} characters"
        )


def _validate_number(
    value: int | float,
    schema: Mapping[str, object],
    path: str,
) -> None:
    number = float(value)
    if not math.isfinite(number):
        raise SchemaValidationError(f"{path} must be finite")
    minimum = schema.get("minimum")
    exclusive_minimum = schema.get("exclusiveMinimum")
    maximum = schema.get("maximum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if minimum is not None and number < float(minimum):
        raise SchemaValidationError(f"{path} must be at least {minimum}")
    if exclusive_minimum is not None and number <= float(exclusive_minimum):
        raise SchemaValidationError(f"{path} must be greater than {exclusive_minimum}")
    if maximum is not None and number > float(maximum):
        raise SchemaValidationError(f"{path} must be at most {maximum}")
    if exclusive_maximum is not None and number >= float(exclusive_maximum):
        raise SchemaValidationError(f"{path} must be less than {exclusive_maximum}")


def _matches_type(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray)),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": type(value) is bool,
        "null": value is None,
    }.get(expected, False)


def _type_description(value: str) -> str:
    return {
        "object": "an object",
        "array": "an array",
        "string": "a string",
        "integer": "an integer",
        "number": "a number",
        "boolean": "a boolean",
        "null": "null",
    }.get(value, value)

