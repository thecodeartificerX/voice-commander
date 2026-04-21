# R5: Python Function Signature → OpenAI Tool-Calling JSON Schema

**Objective:** Reflect plain Python function signatures (with type hints) into OpenAI tool-calling JSON schema without manual schema writing. Schema must support `str`, `int`, `float`, `bool`, `typing.Literal[...]`, `X | None`, and sidecar TOML-sourced parameter descriptions.

**Date:** 2026-04-21

---

## Overview: Three Approaches

| Approach | Pros | Cons | Recommendation |
|----------|------|------|-----------------|
| **A: Pydantic `TypeAdapter`** | Minimal code; schema auto-generation | Requires pydantic v2.x; descriptions dropped on plain functions; post-processing needed for OpenAI format | **NO** — description limitation is dealbreaker |
| **B: Hand-rolled `inspect.signature`** | Zero new deps; full control; plain functions; custom description injection | ~150 LOC boilerplate; must handle all type cases manually | **YES** — chosen for Phase 6 |
| **C: OpenAI SDK wrapper** | Uses official SDK logic | Requires BaseModel per tool (verbose); loses declarative function syntax | **NO** — breaks code-as-declaration principle |

---

## Approach A: Pydantic `TypeAdapter.json_schema()`

### How It Works

```python
from pydantic import TypeAdapter
from typing import get_type_hints
import inspect

def open_file(name: str, folder: str | None = None) -> None:
    """Open a file."""
    pass

# Pydantic v2 approach:
sig = inspect.signature(open_file)
hints = get_type_hints(open_file)

# Create a parameter schema model
from pydantic import create_model, Field

fields = {}
for param_name, param in sig.parameters.items():
    if param_name in hints:
        hint = hints[param_name]
        default = param.default if param.default != inspect.Parameter.empty else ...
        fields[param_name] = (hint, Field(default=default))

ParamModel = create_model('OpenFileParams', **fields)
schema = ParamModel.model_json_schema()
```

### Limitations & Issues

1. **Description Loss:** TypeAdapter drops `description` metadata from `Annotated` fields when the adapted type is a plain function. Workaround: extract descriptions separately and post-process the schema.

2. **Not OpenAI-Native:** Pydantic's JSON schema output needs munging:
   - Pydantic includes `"title"` fields (unwanted by OpenAI).
   - Nullable handling via `anyOf` works but is verbose.
   - Required/optional distinction differs from OpenAI's expectation.

3. **Dependency Cost:** Adds `pydantic>=2.0` to project (currently absent). For description injection, still requires custom post-processing.

### Code Example (Full Pipeline)

```python
from pydantic import create_model, Field
from typing import get_type_hints, Literal
import inspect
import json

def build_pydantic_schema(func, param_descriptions: dict[str, str]):
    """Build OpenAI-compatible schema via Pydantic (requires post-processing)."""
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    
    fields = {}
    for name, param in sig.parameters.items():
        hint = hints[name]
        default = param.default if param.default != inspect.Parameter.empty else ...
        desc = param_descriptions.get(name, "")
        fields[name] = (hint, Field(default=default, description=desc))
    
    ParamModel = create_model(f'{func.__name__}_Params', **fields)
    pydantic_schema = ParamModel.model_json_schema()
    
    # Post-process for OpenAI: remove titles, fix nullable, etc.
    openai_params = {
        'type': 'object',
        'properties': {},
        'required': []
    }
    for prop_name, prop_schema in pydantic_schema.get('properties', {}).items():
        openai_params['properties'][prop_name] = prop_schema  # Still needs cleanup
        if 'default' not in prop_schema:
            openai_params['required'].append(prop_name)
    
    return openai_params
```

**Verdict:** Pydantic saves ~50 LOC but requires:
- Dependency addition
- Custom post-processing (still complex)
- Loss of plain-function elegance

---

## Approach B: Hand-Rolled `inspect.signature` + `typing` Introspection (RECOMMENDED)

### How It Works

Extract signature and type hints manually, walk the type tree via `typing.get_origin()` and `typing.get_args()`, map Python types → JSON schema types. Inject descriptions from TOML sidecar without mutating the function.

### Core Type-Switch

```python
from typing import get_type_hints, get_origin, get_args, Literal, Union
import inspect

def py_type_to_json_schema(
    py_type,
    param_name: str,
    func_name: str,
) -> dict:
    """Convert a Python type to OpenAI tool JSON schema property.
    
    Raises ValueError with (tool_name, param_name) on unsupported type.
    """
    
    # Handle None / NoneType
    if py_type is type(None):
        return {'type': 'null'}
    
    # Handle Literal["a", "b", ...]
    origin = get_origin(py_type)
    if origin is Literal:
        values = get_args(py_type)
        # Check all values are same type (str, int, etc.)
        if not values:
            raise ValueError(f'{func_name}.{param_name}: Literal must have >=1 values')
        first_type = type(values[0])
        if not all(isinstance(v, first_type) for v in values):
            raise ValueError(
                f'{func_name}.{param_name}: Literal values must be same type; '
                f'got {set(type(v) for v in values)}'
            )
        # Map to JSON schema enum
        json_type = 'string' if first_type is str else 'integer'
        return {'type': json_type, 'enum': list(values)}
    
    # Handle Union / X | None
    if origin is Union:
        args = get_args(py_type)
        # Unpack X | None → (X, NoneType)
        non_none_args = [arg for arg in args if arg is not type(None)]
        has_none = len(non_none_args) < len(args)
        
        if len(non_none_args) == 1:
            # X | None case: recurse on X, mark nullable
            inner_schema = py_type_to_json_schema(non_none_args[0], param_name, func_name)
            if has_none:
                # OpenAI format: use type array ["<type>", "null"] OR anyOf
                # Prefer: {"type": ["string", "null"]} if single type
                if 'type' in inner_schema and 'enum' not in inner_schema:
                    inner_schema['type'] = [inner_schema['type'], 'null']
                else:
                    # For enum or complex: use anyOf
                    inner_schema = {'anyOf': [inner_schema, {'type': 'null'}]}
            return inner_schema
        else:
            # Multi-type Union (e.g., str | int | None) — not supported
            raise ValueError(
                f'{func_name}.{param_name}: Union with >1 non-None types unsupported; '
                f'got {non_none_args}'
            )
    
    # Handle basic types
    if py_type is str:
        return {'type': 'string'}
    if py_type is int:
        return {'type': 'integer'}
    if py_type is float:
        return {'type': 'number'}
    if py_type is bool:
        return {'type': 'boolean'}
    
    # Unsupported
    raise ValueError(
        f'{func_name}.{param_name}: unsupported type {py_type}; '
        f'supported: str, int, float, bool, Literal[...], X|None'
    )
```

### Building the Full Tool Schema

```python
def build_tool_schema(
    func,
    param_descriptions: dict[str, str],
) -> dict:
    """Build {"type": "function", "function": {...}} OpenAI tool schema.
    
    Args:
        func: Plain Python function with type hints.
        param_descriptions: Map of param_name -> description string (from TOML).
    
    Returns:
        OpenAI tool-calling JSON schema dict.
    
    Raises:
        ValueError: if any parameter has unsupported type (includes func + param name).
    """
    
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    
    properties = {}
    required = []
    
    for param_name, param in sig.parameters.items():
        if param_name not in hints:
            raise ValueError(
                f'{func.__name__}.{param_name}: missing type hint (all params must be annotated)'
            )
        
        py_type = hints[param_name]
        prop_schema = py_type_to_json_schema(py_type, param_name, func.__name__)
        
        # Inject description from TOML
        if param_name in param_descriptions:
            prop_schema['description'] = param_descriptions[param_name]
        
        properties[param_name] = prop_schema
        
        # Mark as required if no default
        if param.default == inspect.Parameter.empty:
            required.append(param_name)
    
    return {
        'type': 'function',
        'function': {
            'name': func.__name__,
            'description': param_descriptions.get('__doc__', func.__doc__ or ''),
            'parameters': {
                'type': 'object',
                'properties': properties,
                'required': required,
            },
        },
    }
```

### Error Handling

Unsupported types fail with:
```
ValueError: open_file.search_path: unsupported type <class 'pathlib.Path'>; 
            supported: str, int, float, bool, Literal[...], X|None
```

On invalid Literal:
```
ValueError: open_file.mode: Literal values must be same type; got {<class 'str'>, <class 'int'>}
```

### Pros & Cons

**Pros:**
- Zero external dependencies (Python 3.10+ stdlib only).
- Full control: easy to add new types later (e.g., `dict[str, str]`).
- Plain functions remain plain; sidecar TOML is injected post-declaration.
- Explicit error messages with tool + param name.
- ~150 LOC, all in one module.

**Cons:**
- Must handle each type case manually (limited to the six above for MVP).
- No automatic nested object support (but not needed for Phase 6).

---

## Approach C: OpenAI SDK `pydantic_function_tool`

Not recommended. Requires declaring a `BaseModel` per tool:

```python
from pydantic import BaseModel
from openai import OpenAI

class OpenFileParams(BaseModel):
    name: str
    folder: str | None = None

# Then convert to tool schema... but this loses the "single function = single file" principle
```

Cost: extra boilerplate, loses the `@tool(phrases=[...])` decorator elegance, description still sidelined.

---

## Handling Nullable & Defaults: OpenAI Format

**Key rule:** Parameters with defaults (including `None`) are excluded from `required[]`.

### Example: `str | None` with default

```python
def cmd(target: str | None = None) -> None: ...
```

**Generated schema:**
```json
{
  "parameters": {
    "type": "object",
    "properties": {
      "target": {
        "type": ["string", "null"],
        "description": "..."
      }
    },
    "required": []
  }
}
```

Note: `required` is empty because `target` has a default. OpenAI does NOT list parameters with defaults as required, even if nullable.

### Example: `str | None` without default (required nullable)

```python
def cmd(target: str | None) -> None: ...
```

**Generated schema:**
```json
{
  "parameters": {
    "type": "object",
    "properties": {
      "target": {
        "type": ["string", "null"],
        "description": "..."
      }
    },
    "required": ["target"]
  }
}
```

Here `target` is required but can be null.

---

## Description Injection from Sidecar TOML

Each tool's TOML file (e.g., `tools/browser.toml`) contains parameter descriptions:

```toml
[tool]
name = "browser"
description = "Browser control commands"

[tool.params.url]
description = "Target URL to navigate to"
examples = ["https://example.com"]

[tool.params.folder]
description = "Optional folder path (uses home if omitted)"
```

At schema-build time, load the TOML and pass `param_descriptions` dict:

```python
import tomli

def load_tool_descriptions(tool_name: str) -> dict[str, str]:
    """Load param descriptions from tools/<tool_name>.toml."""
    toml_path = Path(f'src/voice_commander/tools/{tool_name}.toml')
    with open(toml_path, 'rb') as f:
        config = tomli.load(f)
    return config.get('tool', {}).get('params', {})

# When building schema:
param_descs = load_tool_descriptions('open_file')
schema = build_tool_schema(open_file, param_descs)
```

The post-build schema includes descriptions without mutating the function.

---

## Recommendation: Approach B (Hand-Rolled)

### Reasoning

1. **No new dependencies:** Project already has Python 3.11+; `typing` and `inspect` are stdlib.
2. **Plain function principle:** Tools remain declarative; schema is a build-time artifact, not a code-time concern.
3. **Description injection:** TOML sidecar is the single source of truth; no docstring scraping.
4. **Explicit errors:** Unsupported types fail loudly with (tool_name, param_name) context.
5. **Extensibility:** Adding new type support (e.g., `dict[str, str]`, `list[int]`) is straightforward; no Pydantic magic to debug.
6. **Testing:** Each type case can be unit-tested independently; no framework coupling.

### Implementation Plan

1. **Module:** `src/voice_commander/schema_builder.py`
2. **Core:** `py_type_to_json_schema()` (type walker) + `build_tool_schema()` (orchestrator)
3. **Error class:** `UnsupportedTypeError(tool_name: str, param_name: str, py_type, supported_types: list)`
4. **Tests:** `tests/unit/test_schema_builder.py` covering all type cases, defaults, Literal, nullable.
5. **Integration:** Registry discovery auto-builds schema at daemon startup; cached in `ToolEntry.schema`.

---

## Sources

- [Pydantic JSON Schema — TypeAdapter](https://pydantic.dev/docs/validation/latest/concepts/json_schema/)
- [The Anatomy of Tool Calling — Amit Chaudhary](https://amitness.com/posts/function-calling-schema/)
- [OpenAI Agents SDK — Function Schema](https://openai.github.io/openai-agents-python/ref/function_schema/)
- [OpenAI Tool JSON Schema Explained — Laurent Kubaski](https://medium.com/@laurentkubaski/openai-tool-schema-explained-05a5ce0e80f8)
- [Null in OpenAPI Best Practices — Speakeasy](https://www.speakeasy.com/openapi/schemas/null)
- [Python typing.Literal — Official Typing Spec](https://typing.python.org/en/latest/spec/literal.html)
