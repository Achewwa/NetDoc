"""Skill abstractions and lightweight schema validation for NetDoc."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


JsonDict = dict[str, Any]


class SchemaValidationError(ValueError):
    """Raised when skill arguments do not match the declared schema."""


@dataclass(frozen=True)
class Skill:
    """A JSON-described callable capability exposed to the agent."""

    name: str
    skill_schema: JsonDict
    implement_function: Callable[..., JsonDict]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Skill name must not be empty.")
        if not isinstance(self.skill_schema, dict):
            raise TypeError("Skill schema must be a dictionary.")
        if not callable(self.implement_function):
            raise TypeError("Skill implementation must be callable.")

    @property
    def input_schema(self) -> JsonDict:
        """Return the JSON Schema object used to validate input arguments."""
        parameters = self.skill_schema.get("parameters")
        if isinstance(parameters, dict):
            return parameters
        return self.skill_schema

    def validate_arguments(self, arguments: JsonDict | None = None) -> JsonDict:
        """Validate and return JSON-compatible skill arguments."""
        normalized = arguments or {}
        if not isinstance(normalized, dict):
            raise SchemaValidationError("Skill arguments must be a JSON object.")
        validate_json_schema(normalized, self.input_schema)
        return normalized

    def run(self, arguments: JsonDict | None = None) -> JsonDict:
        """Execute the skill with JSON-compatible validated arguments."""
        return self.implement_function(**self.validate_arguments(arguments))


def validate_json_schema(value: Any, schema: JsonDict, path: str = "$") -> None:
    """Validate a useful JSON Schema subset without third-party dependencies.

    NetDoc only needs a small subset for skill inputs: object properties,
    required fields, scalar types, arrays, enums and numeric/string bounds.
    """
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: schema must be an object.")

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_type(value, expected_type):
        actual_type = type(value).__name__
        raise SchemaValidationError(f"{path}: expected {expected_type}, got {actual_type}.")

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(item) for item in schema["enum"])
        raise SchemaValidationError(f"{path}: expected one of {allowed}.")

    if isinstance(value, Mapping):
        _validate_object(value, schema, path)
    elif isinstance(value, list):
        _validate_array(value, schema, path)
    elif isinstance(value, str):
        _validate_string(value, schema, path)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        _validate_number(value, schema, path)


def _validate_object(value: Mapping[str, Any], schema: JsonDict, path: str) -> None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field in required:
        if field not in value:
            raise SchemaValidationError(f"{path}.{field}: required field is missing.")

    additional = schema.get("additionalProperties", True)
    if additional is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            raise SchemaValidationError(f"{path}: unexpected field(s): {', '.join(extra)}.")

    for field, field_value in value.items():
        field_schema = properties.get(field)
        if field_schema is None:
            if isinstance(additional, dict):
                validate_json_schema(field_value, additional, f"{path}.{field}")
            continue
        validate_json_schema(field_value, field_schema, f"{path}.{field}")


def _validate_array(value: list[Any], schema: JsonDict, path: str) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        raise SchemaValidationError(f"{path}: expected at least {schema['minItems']} item(s).")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        raise SchemaValidationError(f"{path}: expected at most {schema['maxItems']} item(s).")
    if schema.get("uniqueItems") and len(value) != len(set(_hashable(item) for item in value)):
        raise SchemaValidationError(f"{path}: expected unique items.")

    item_schema = schema.get("items", {})
    for index, item in enumerate(value):
        validate_json_schema(item, item_schema, f"{path}[{index}]")


def _validate_string(value: str, schema: JsonDict, path: str) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        minimum = schema["minLength"]
        raise SchemaValidationError(f"{path}: expected at least {minimum} character(s).")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise SchemaValidationError(f"{path}: expected at most {schema['maxLength']} character(s).")


def _validate_number(value: int | float, schema: JsonDict, path: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise SchemaValidationError(f"{path}: expected value >= {schema['minimum']}.")
    if "maximum" in schema and value > schema["maximum"]:
        raise SchemaValidationError(f"{path}: expected value <= {schema['maximum']}.")


def _matches_json_type(value: Any, expected_type: str | Sequence[str]) -> bool:
    if isinstance(expected_type, str):
        expected_types = [expected_type]
    else:
        expected_types = list(expected_type)
    return any(_matches_single_json_type(value, item) for item in expected_types)


def _matches_single_json_type(value: Any, expected_type: str) -> bool:
    match expected_type:
        case "object":
            return isinstance(value, Mapping)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
        case _:
            raise SchemaValidationError(f"Unsupported JSON Schema type: {expected_type}")


def _hashable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value
