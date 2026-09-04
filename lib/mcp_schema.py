"""mcp_schema — JSON-Schema validation for the MCP scaffold.

Split out of lib/mcp_scaffold.py at 496/500 lines (the next feature edit
would have tripped the file-length cap mid-change). Pure functions, no I/O:
_check_type (JSON type predicates), _validate_args (jsonschema when
installed, lightweight required/type/additionalProperties fallback with NO
coercion), _python_type_to_json_schema (tool_from_function annotations).
mcp_scaffold re-exports every name, so both `lib.mcp_schema` and
`lib.mcp_scaffold` import paths keep working.
"""
from __future__ import annotations

# ── validation helper (lightweight + jsonschema when available) ─────────────

def _check_type(value, type_str: str) -> bool:
    if type_str == "string":
        return isinstance(value, str)
    if type_str == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_str == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_str == "boolean":
        return isinstance(value, bool)
    if type_str == "object":
        return isinstance(value, dict)
    if type_str == "array":
        return isinstance(value, list)
    if type_str == "null":
        return value is None
    return True


def _validate_args(schema: dict, args: dict) -> str | None:
    """Validate *args* against JSON Schema *schema*.

    Returns None on success, or an error message string on failure.
    Prefers jsonschema when installed; falls back to a lightweight check
    covering required / type / additionalProperties (the cases the task
    requires). No coercion: year="2024" where integer is required fails.
    """
    if not isinstance(schema, dict):
        return None
    # Try jsonschema first (strict, no coercion)
    try:
        import jsonschema  # type: ignore
        import jsonschema.exceptions  # type: ignore

        jsonschema.validate(instance=args, schema=schema)
        return None
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            import jsonschema.exceptions as _exc  # type: ignore

            if isinstance(exc, _exc.ValidationError):
                return exc.message
        except ImportError:
            pass
        return str(exc)

    # Lightweight fallback: required / additionalProperties / type
    required = schema.get("required", [])
    if isinstance(required, list):
        for req in required:
            if req not in args:
                return f"missing required property: '{req}'"

    properties = schema.get("properties", {}) or {}
    additional = schema.get("additionalProperties", True)
    if additional is False:
        for key in args:
            if key not in properties:
                return f"additional property not allowed: '{key}'"

    for key, value in args.items():
        prop_schema = properties.get(key)
        if not isinstance(prop_schema, dict):
            continue
        expected = prop_schema.get("type")
        if expected is None:
            continue
        if isinstance(expected, list):
            if not any(_check_type(value, t) for t in expected):
                return f"property '{key}' is not of type {expected}"
            continue
        if not _check_type(value, expected):
            return f"property '{key}' expected {expected}, got {type(value).__name__}"

    return None


_PY_TYPE_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json_schema(py_type) -> dict:
    origin = getattr(py_type, "__origin__", None)
    if origin is not None:
        args = getattr(py_type, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            py_type = non_none[0]
    json_type = _PY_TYPE_TO_JSON.get(py_type)
    if json_type:
        return {"type": json_type}
    return {"type": "string"}
