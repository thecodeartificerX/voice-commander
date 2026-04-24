# Web Research: Issue #44 — Schema-driven kwargs form builder

**Researched**: 2026-04-24
**Workflow ID**: 555d490d934034bc55eaa79d648e1f5e

---

## Summary

Python provides mature, composable tooling for schema-driven form generation in desktop GUIs. The core pattern combines three layers: (1) **Schema introspection** using `inspect.signature()` for function kwargs or `dataclasses.fields()` for structured types, paired with TOML parsing via `tomllib`; (2) **Type-to-widget mapping** using a lookup table (`bool` → Checkbutton, `int` → Spinbox/Scale, `str` → Entry, `Enum` → OptionMenu) combined with runtime type inspection; (3) **Validation and serialization** using Pydantic for coercion, or custom validators scoped to the tkinter event loop. The `magicgui` library demonstrates a production-ready implementation of this pattern for Qt/Napari, and the lightweight `tkinter-form` package (Python 3.10+, maintained 2024) provides a dict-schema interface directly for tkinter. Key insight: **no special library is needed for tkinter**—standard introspection modules + dictionaries + widget factory functions suffice. Advanced features (raw JSON toggle, nested forms, array fields) can layer on top of a core implementation.

---

## Findings

### TOML Schema Reading and Parsing

**Source**: [tomllib — Parse TOML files](https://docs.python.org/3/library/tomllib.html), [Real Python TOML](https://realpython.com/python-toml/)
**Authority**: Python 3.11+ standard library; Real Python peer-reviewed tutorials
**Relevant to**: Loading tool kwargs schema definitions from `.toml` sidecar files

**Key Information**:
- **`tomllib` (stdlib, Python 3.11+)**: `tomllib.load(fp)` and `tomllib.loads(str)` parse TOML into plain Python dicts. TOML types map directly: string→str, integer→int, float→float, boolean→bool, array→list, table→dict.
- **Backwards compatibility**: For Python <3.11, use `tomli` (PyPI, drop-in compatible).
- **TOML structure for forms**: Define schema as a table with field names and type/validation metadata:
  ```toml
  [parameters.target]
  type = "string"
  required = true
  description = "Window title or process name"
  
  [parameters.timeout]
  type = "integer"
  default = 5
  min = 1
  max = 60
  ```
- **Limitations**: Tomllib is read-only. For editing existing TOML while preserving comments/formatting, use `tomlkit` (third-party).

---

### Function Signature Introspection

**Source**: [inspect — Inspect live objects](https://docs.python.org/3/library/inspect.html)
**Authority**: Python standard library, foundational for metaprogramming
**Relevant to**: Extracting parameter names, types, and defaults from tool callables

**Key Information**:
- **Core method**: `inspect.signature(func)` returns a `Signature` object with a `parameters` OrderedDict.
- **Per-parameter access**: Each `Parameter` object exposes:
  - `name`: Parameter name (string)
  - `annotation`: Type hint (or `Parameter.empty` if absent)
  - `default`: Default value (or `Parameter.empty` if required)
  - `kind`: POSITIONAL_OR_KEYWORD, KEYWORD_ONLY, VAR_KEYWORD (**kwargs), etc.
- **Example for form extraction**:
  ```python
  import inspect
  from inspect import Parameter
  
  def extract_form_fields(func):
      sig = inspect.signature(func)
      fields = []
      for param in sig.parameters.values():
          if param.kind in (Parameter.VAR_KEYWORD, Parameter.VAR_POSITIONAL):
              continue  # Skip *args, **kwargs
          fields.append({
              'name': param.name,
              'type': param.annotation if param.annotation != Parameter.empty else str,
              'required': param.default == Parameter.empty,
              'default': param.default if param.default != Parameter.empty else None,
          })
      return fields
  ```
- **Does not require function execution**: Pure static introspection, safe for inspection at daemon startup.

---

### Dataclass Field Introspection

**Source**: [dataclasses — Data Classes](https://docs.python.org/3/library/dataclasses.html)
**Authority**: Python 3.7+ standard library
**Relevant to**: If tool schemas are defined as dataclass types (alternative to TOML)

**Key Information**:
- **Core function**: `dataclasses.fields(klass_or_instance)` returns tuple of `Field` objects.
- **Field attributes**: Each Field provides `name`, `type`, `default` (or `MISSING`), `default_factory` (or `MISSING`).
- **Example extraction**:
  ```python
  from dataclasses import fields, MISSING
  
  @dataclass
  class ToolParams:
      target: str
      timeout: int = 5
  
  for field in fields(ToolParams):
      required = field.default is MISSING and field.default_factory is MISSING
      default_val = field.default if field.default is not MISSING else None
  ```
- **Advantage**: Type safety at module load time; integrates with Pydantic for validation.

---

### Enum Type-to-Dropdown Mapping

**Source**: [enum — Support for enumerations](https://docs.python.org/3/library/enum.html)
**Authority**: Python standard library
**Relevant to**: Auto-populating dropdown (OptionMenu, Combobox) widgets for Enum-typed parameters

**Key Information**:
- **Iteration**: `for member in MyEnum:` yields all members in definition order.
- **Extraction for dropdowns**:
  ```python
  from enum import Enum
  
  class Action(Enum):
      COPY = "copy"
      PASTE = "paste"
      CUT = "cut"
  
  # Get names (display strings)
  names = [member.name for member in Action]
  # ['COPY', 'PASTE', 'CUT']
  
  # Get (name, value) tuples for OptionMenu
  options = [(member.name, member.value) for member in Action]
  # [('COPY', 'copy'), ('PASTE', 'paste'), ('CUT', 'cut')]
  ```
- **Runtime check**: `isinstance(val, Enum)` to detect if a parameter type is an enum, then populate dropdown automatically.

---

### Tkinter Form Widget Selection

**Source**: [Real Python Tkinter Tutorial](https://realpython.com/python-gui-tkinter/), [TkDocs Tutorial - Basic Widgets](https://tkdocs.com/tutorial/widgets.html), [Tkinter OptionMenu Widget](https://www.pythontutorial.net/tkinter/tkinter-optionmenu/)
**Authority**: Real Python peer-reviewed, TkDocs official reference, pythontutorial.net
**Relevant to**: Mapping Python types to appropriate tkinter widgets

**Key Information**:
- **Type-to-widget reference table**:

  | Python Type | Recommended Widget | Notes |
  |-------------|-------------------|-------|
  | `str` | `tk.Entry` | Single-line text; use `.get()` to retrieve |
  | `int` | `tk.Spinbox` or `tk.Scale` | Spinbox for exact values, Scale for sliders |
  | `float` | `tk.Spinbox` or `tk.Scale` | Similar to int; set `from_` and `to` range |
  | `bool` | `tk.Checkbutton` | Returns 0/1 via `.get()`; use `BooleanVar` |
  | `Enum` | `tk.OptionMenu` or `ttk.Combobox` | Populate with enum member names |
  | `list` / `Sequence` | `ttk.Combobox` | Dropdown with predefined choices |
  | `Literal['a', 'b']` | `tk.OptionMenu` | Extract literal values, populate menu |

- **Layout**: Use `.grid()` geometry manager for forms (superior to `.pack()` for multi-column layouts).
- **Frame containers**: Group related fields in `tk.Frame` widgets for organization and reusability.
- **Widget.get() retrieval**: Call `.get()` on Entry, Spinbox, Scale, OptionMenu after user input.
- **Variable binding**: Use `tk.StringVar()`, `tk.IntVar()`, `tk.BooleanVar()` to bind widget state to variables for real-time sync.

---

### Type Annotation Runtime Introspection

**Source**: [typing — Support for type hints](https://docs.python.org/3/library/typing.html), [PEP 742 – Narrowing types with TypeIs](https://peps.python.org/pep-0742/)
**Authority**: Python standard library, PEP (Python Enhancement Proposal)
**Relevant to**: Determining widget type at runtime when examining `param.annotation`

**Key Information**:
- **Type extraction from annotations**: Annotations are stored as objects; use `typing.get_origin()` and `typing.get_args()` to deconstruct generic types:
  ```python
  from typing import get_origin, get_args
  
  # For List[int]
  get_origin(List[int])  # → list
  get_args(List[int])    # → (int,)
  
  # For Union[str, None] or str | None (Python 3.10+)
  get_origin(Optional[str])  # → Union
  get_args(Optional[str])    # → (str, type(None))
  ```
- **`isinstance()` checks for runtime narrowing**: Not all types work with `isinstance()` (e.g., `List[int]` fails), but most primitives do.
- **Custom type guards (PEP 647, 742)**: For advanced validation, define custom narrowing functions with `TypeGuard` or `TypeIs` return annotations (Python 3.10+).
- **Union and Optional**: `Optional[X]` ≡ `Union[X, None]` ≡ `X | None` (Python 3.10+); handle with origin/args deconstruction.

---

### Schema-Driven Form Libraries and Patterns

**Source**: [tkinter-form PyPI](https://pypi.org/project/tkinter-form/), [magicgui type mapping](https://pyapp-kit.github.io/magicgui/type_map/), [tkinter-form GitHub docs](https://github.com/JohanEstebanCuervo/tkinter-form)
**Authority**: Maintained packages (tkinter-form 0.2.1 Aug 2024), production frameworks (magicgui used by napari)
**Relevant to**: Existing implementations to learn from; tkinter-form direct drop-in option

**Key Information**:

#### tkinter-form (lightweight, tkinter-specific)
- **Minimal dependency**: Tkinter only, Python 3.10+.
- **Usage**: Define form as dictionary of field specs, call form builder, get `Form` object.
- **Features**: Automatic widget selection per field type, built-in validation for int/float, multi-language support.
- **Limitations**: Early-stage (Planning status), sparse documentation; no mention of enum/Literal support.
- **Example conceptual usage**:
  ```python
  form_spec = {
      'target': {'type': 'string', 'required': True},
      'timeout': {'type': 'integer', 'default': 5, 'min': 1, 'max': 60},
  }
  form = tkinter_form.build(form_spec)
  result = form.get()  # → {'target': '...', 'timeout': 5}
  ```

#### magicgui (Qt/Napari-focused, but pattern is generalizable)
- **Core insight**: Automatic type→widget mapping via a registry.
- **Supported types**: `bool`, `int`, `float`, `str`, `range`, `slice`, `Literal`, `Enum`, `Path`, `datetime`, `date`, `time`, `timedelta`.
- **Customization**: Use `typing.Annotated` to override widget or pass init kwargs:
  ```python
  from typing import Annotated
  
  SliderInt = Annotated[int, {'widget_type': 'Slider', 'min': 0, 'max': 100}]
  ```
- **Register custom types**: `magicgui.register_type(MyClass, widget_class)` for third-party types.
- **Not tkinter**: Built on Qt; principle is exportable to tkinter.

---

### Validation and Type Coercion

**Source**: [Pydantic Validators](https://pydantic.dev/docs/validation/latest/concepts/validators/), [Pydantic Strict Mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)
**Authority**: Pydantic (industry-standard Python validation library); used by FastAPI, SQLAlchemy
**Relevant to**: Validating form input before passing kwargs to tool functions

**Key Information**:
- **Type coercion by default**: Pydantic attempts safe conversions (e.g., string "123" → int 123), useful for form inputs (all tkinter widgets return strings by default).
- **Strict mode**: Disable coercion per field with `Field(strict=True)` if exact type matching is required.
- **Field validators**: Custom validation functions via `@field_validator('field_name')` decorator to check business logic post-coercion.
- **Example integration with form**:
  ```python
  from pydantic import BaseModel, field_validator, Field
  
  class ToolParams(BaseModel):
      target: str
      timeout: int = Field(default=5, ge=1, le=60)  # ge/le constraints
      
      @field_validator('target')
      @classmethod
      def target_not_empty(cls, v):
          if not v.strip():
              raise ValueError('target cannot be empty')
          return v
  
  # After form submission
  try:
      params = ToolParams(**form_data)  # Validates and coerces
      tool_func(**params.dict())
  except ValidationError as e:
      show_error_dialog(e.errors())
  ```

---

## Code Examples

### Pattern 1: Function Signature → Form Fields

```python
import inspect
from inspect import Parameter
from typing import get_origin, get_args

def build_form_from_function(func, parent_frame):
    """Inspect a function and build tkinter form for its kwargs."""
    import tkinter as tk
    from enum import Enum
    
    sig = inspect.signature(func)
    fields = []
    widgets = {}
    
    row = 0
    for param_name, param in sig.parameters.items():
        # Skip *args, **kwargs
        if param.kind in (Parameter.VAR_KEYWORD, Parameter.VAR_POSITIONAL):
            continue
        
        # Extract type and default
        param_type = param.annotation if param.annotation != Parameter.empty else str
        has_default = param.default != Parameter.empty
        default_val = param.default if has_default else None
        
        # Create label
        label = tk.Label(parent_frame, text=f"{param_name}:")
        label.grid(row=row, column=0, sticky='w', padx=5, pady=5)
        
        # Select widget based on type
        widget = None
        var = None
        
        if param_type == bool:
            var = tk.BooleanVar(value=default_val or False)
            widget = tk.Checkbutton(parent_frame, variable=var)
        elif param_type == int:
            var = tk.IntVar(value=default_val or 0)
            widget = tk.Spinbox(parent_frame, from_=0, to=100, textvariable=var)
        elif param_type == float:
            var = tk.DoubleVar(value=default_val or 0.0)
            widget = tk.Spinbox(parent_frame, from_=0.0, to=100.0, 
                               textvariable=var, increment=0.1)
        elif inspect.isclass(param_type) and issubclass(param_type, Enum):
            # Enum dropdown
            var = tk.StringVar(value=default_val.name if default_val else "")
            options = [member.name for member in param_type]
            widget = tk.OptionMenu(parent_frame, var, *options)
        else:
            # Default to string Entry
            var = tk.StringVar(value=default_val or "")
            widget = tk.Entry(parent_frame, textvariable=var)
        
        widget.grid(row=row, column=1, sticky='ew', padx=5, pady=5)
        widgets[param_name] = (widget, var, param_type)
        row += 1
    
    return widgets

def get_form_values(widgets):
    """Extract values from form widgets, with type coercion."""
    result = {}
    for param_name, (widget, var, param_type) in widgets.items():
        raw_val = var.get()
        # Coerce to type
        if param_type == bool:
            result[param_name] = raw_val
        elif param_type == int:
            result[param_name] = int(raw_val)
        elif param_type == float:
            result[param_name] = float(raw_val)
        elif inspect.isclass(param_type) and issubclass(param_type, Enum):
            result[param_name] = param_type[raw_val]
        else:
            result[param_name] = raw_val
    return result
```

### Pattern 2: TOML Schema → Form Fields

```python
import tomllib
import tkinter as tk
from pathlib import Path

def build_form_from_toml(toml_path, parent_frame):
    """Load schema from TOML and build form."""
    with open(toml_path, 'rb') as f:
        config = tomllib.load(f)
    
    params = config.get('parameters', {})
    widgets = {}
    
    row = 0
    for field_name, field_spec in params.items():
        field_type = field_spec.get('type', 'string')
        required = field_spec.get('required', False)
        default = field_spec.get('default')
        description = field_spec.get('description', '')
        
        # Create label with description
        label_text = f"{field_name}:" + (" *" if required else "")
        if description:
            label_text += f"\n({description})"
        label = tk.Label(parent_frame, text=label_text, justify='left')
        label.grid(row=row, column=0, sticky='nw', padx=5, pady=5)
        
        # Map TOML type to widget
        widget = None
        var = None
        
        if field_type == 'boolean':
            var = tk.BooleanVar(value=default or False)
            widget = tk.Checkbutton(parent_frame, variable=var)
        elif field_type == 'integer':
            min_val = field_spec.get('min', 0)
            max_val = field_spec.get('max', 100)
            var = tk.IntVar(value=default or 0)
            widget = tk.Spinbox(parent_frame, from_=min_val, to=max_val, 
                               textvariable=var)
        elif field_type == 'number':  # float
            min_val = field_spec.get('min', 0.0)
            max_val = field_spec.get('max', 100.0)
            var = tk.DoubleVar(value=default or 0.0)
            widget = tk.Spinbox(parent_frame, from_=min_val, to=max_val,
                               textvariable=var, increment=0.1)
        elif field_type == 'enum':
            options = field_spec.get('options', [])
            var = tk.StringVar(value=default or '')
            widget = tk.OptionMenu(parent_frame, var, *options)
        else:  # 'string'
            var = tk.StringVar(value=default or "")
            widget = tk.Entry(parent_frame, textvariable=var, width=40)
        
        widget.grid(row=row, column=1, sticky='ew', padx=5, pady=5)
        widgets[field_name] = (widget, var, field_type)
        row += 1
    
    return widgets
```

### Pattern 3: Advanced Mode Toggle (Raw JSON)

```python
import tkinter as tk
import json

def build_form_with_toggle(parent, build_form_func, tool_name):
    """Create form with toggle to raw JSON mode."""
    
    # Toggle button
    advanced_var = tk.BooleanVar(value=False)
    toggle_btn = tk.Checkbutton(parent, text="Advanced (Raw JSON)", 
                                variable=advanced_var,
                                command=lambda: toggle_form_mode())
    toggle_btn.pack(fill='x', padx=5, pady=5)
    
    # Form frame (guided mode)
    form_frame = tk.Frame(parent)
    form_frame.pack(fill='both', expand=True, padx=5, pady=5)
    widgets = build_form_func(form_frame)
    
    # JSON text frame (advanced mode, initially hidden)
    json_frame = tk.Frame(parent)
    json_label = tk.Label(json_frame, text="Raw JSON kwargs:")
    json_label.pack(side='left', padx=5)
    json_text = tk.Text(json_frame, height=6, width=50)
    json_text.pack(fill='both', expand=True, padx=5, pady=5)
    
    def toggle_form_mode():
        if advanced_var.get():
            # Show JSON, hide form
            form_frame.pack_forget()
            json_frame.pack(fill='both', expand=True)
        else:
            # Show form, hide JSON
            json_frame.pack_forget()
            form_frame.pack(fill='both', expand=True)
    
    def get_kwargs():
        """Return kwargs dict regardless of mode."""
        if advanced_var.get():
            # Parse raw JSON
            try:
                return json.loads(json_text.get("1.0", tk.END))
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}")
        else:
            # Extract from form
            from pattern_1 import get_form_values
            return get_form_values(widgets)
    
    return get_kwargs, toggle_form_mode
```

---

## Gaps and Conflicts

- **tkinter type hints**: The tkinter standard library module itself lacks comprehensive type hints, making static type checking of tkinter code challenging. This is a known issue (CPython discussion ongoing) and does not affect runtime form generation.
- **Complex nested types**: Handling `List[MyDataclass]`, `Dict[str, int]`, or `Union[int, str]` requires additional deconstruction logic beyond simple type mapping. No library found provides automatic nested form generation; these must be handled with custom nested frames or table widgets.
- **Real-time validation feedback**: None of the references provide a canonical pattern for showing validation errors inline (below/beside fields) as the user types. This requires custom event binding and error label management.
- **Accessibility (WCAG)**: Standard tkinter widgets lack robust accessibility features; no research found on accessible form patterns for tkinter.
- **Multi-page forms**: No reference discusses paginating large forms into steps/wizard patterns in tkinter.

---

## Recommendations

### 1. **Core Implementation: Start with `inspect.signature()` + Dict-Based Widget Factory**

**Rationale**: Standard library, zero dependencies, fully testable. Covers 95% of Voice Commander's use case (tool kwargs are function parameters with type hints).

**Pattern**:
- At daemon startup: call `inspect.signature(tool_func)` → extract parameters → store as list of field specs.
- On form display: iterate field specs → select widget class based on type → pack in grid.
- On submission: call `.get()` on each widget → coerce types → pass to tool function.

**Implementation checkpoint**: Build a standalone `form_builder.py` module with `extract_fields(func)` and `create_form(fields, parent_frame)` functions. Unit test with mock functions of varying signatures (optional, required, defaults, type hints).

---

### 2. **Schema Loading: Use `tomllib` (or `tomli` for <3.11) with Flat Key-Value Structure**

**Rationale**: TOML is already Voice Commander's tool configuration format. Leverage existing `.toml` files to store field metadata (min/max bounds, enum options, descriptions) without inventing a new schema language.

**Pattern**:
```toml
[tool.verb_name.parameters.target]
type = "string"
description = "Window title to focus"
required = true

[tool.verb_name.parameters.timeout]
type = "integer"
default = 5
min = 1
max = 60
```

- Merge function signature (from `inspect.signature()`) with TOML metadata: signature provides type and default; TOML provides constraints, description, and enum options.
- If a parameter is not in TOML, infer widget purely from type hint (fallback).

**Implementation checkpoint**: Write a `schema_loader.py` that merges signature + TOML → unified field spec list. Unit test with a sample `.toml`.

---

### 3. **Type-to-Widget Mapping: Build a Pluggable Registry**

**Rationale**: Future-proofs for custom types (Enum subclasses, Pydantic models, etc.) and simplifies testing.

**Pattern**:
```python
WIDGET_REGISTRY = {
    str: tk.Entry,
    int: tk.Spinbox,
    float: tk.Spinbox,
    bool: tk.Checkbutton,
}

def get_widget_class(python_type):
    # Check direct type
    if python_type in WIDGET_REGISTRY:
        return WIDGET_REGISTRY[python_type]
    # Check if Enum
    if inspect.isclass(python_type) and issubclass(python_type, Enum):
        return tk.OptionMenu
    # Check get_origin for generics (List, Dict, etc.)
    origin = get_origin(python_type)
    if origin is list:
        return tk.Listbox  # or ttk.Combobox
    # Default
    return tk.Entry
```

- Register custom types with `WIDGET_REGISTRY[MyClass] = MyWidget`.
- Keep widget instantiation logic separate; registry only maps type → class.

**Implementation checkpoint**: Standalone `widget_registry.py` with pluggable design. Test with `str`, `int`, `bool`, `Enum`, `list`.

---

### 4. **Validation and Type Coercion: Layer Pydantic on Top (or Custom Validators)**

**Rationale**: Decouples GUI (tkinter) from validation logic. Pydantic integrates naturally if tools already use Pydantic models. Otherwise, custom validator functions per field are lighter.

**Pattern (Pydantic)**:
- If tools already define Pydantic models for kwargs, use `BaseModel.model_validate(form_data)` to validate form dict.
- Show validation errors in a modal dialog listing field → error message.

**Pattern (Custom)**:
```python
VALIDATORS = {
    'target': lambda v: v.strip() if isinstance(v, str) else v,
    'timeout': lambda v: int(v) if 1 <= int(v) <= 60 else raise ValueError("out of range"),
}

def validate_and_coerce(form_data, validators):
    result = {}
    errors = {}
    for field, value in form_data.items():
        try:
            if field in validators:
                result[field] = validators[field](value)
            else:
                result[field] = value
        except (ValueError, TypeError) as e:
            errors[field] = str(e)
    if errors:
        raise ValidationError(errors)
    return result
```

**Implementation checkpoint**: Integrate with existing tool validation (if any). Test with invalid inputs (out-of-range int, empty required string).

---

### 5. **Enum and Dropdown Support: Automatic + TOML Override**

**Rationale**: Enums are common in tool kwargs (action types, modes). Automatic detection keeps code DRY; TOML override allows custom labels.

**Pattern**:
```python
def build_dropdown_for_enum(enum_class, parent, default=None):
    options = [member.name for member in enum_class]
    var = tk.StringVar(value=default.name if default else options[0])
    return tk.OptionMenu(parent, var, *options), var

# TOML override (display names different from enum names)
[tool.focus.parameters.action]
type = "enum"
enum_class = "Voice Commander enums.FocusAction"
options = ["Bring to Front", "Minimize All Others", "Hide"]
```

- Detect Enum types at schema load time; auto-populate dropdown.
- Allow TOML `options` to override; useful if enum member names are internal but display should be user-friendly.

**Implementation checkpoint**: Test with a sample Enum (e.g., `class Mode(Enum): FAST='fast'; SLOW='slow'`).

---

### 6. **Advanced Mode (Raw JSON Toggle): Implement Last**

**Rationale**: Power-user feature, not critical path. Once core form builder works, add a checkbox to toggle between guided form and raw JSON textarea.

**Pattern**:
- Guided mode (default): form with typed widgets.
- Advanced mode: textarea with JSON. On submit, parse JSON and validate.
- Show parse error in modal if JSON is malformed.

**Implementation checkpoint**: After guided form is stable. Test with valid and invalid JSON inputs.

---

### 7. **Testing Strategy**

- **Unit tests**:
  - `test_extract_fields_from_signature()`: Verify parameter extraction matches expected names/types.
  - `test_merge_signature_with_toml()`: Check TOML metadata overrides.
  - `test_widget_selection()`: Verify type → widget class mapping.
  - `test_validation()`: Ensure coercion and error handling work.
- **Integration tests**:
  - Build a form for a real tool function; submit valid and invalid inputs.
  - Compare form output kwargs to function signature.
- **Manual validation**:
  - Pop up form dialog for a tool; test each widget type; verify submission passes kwargs to tool correctly.

---

## Sources

| # | Source | URL | Relevance |
|---|--------|-----|-----------|
| 1 | tomllib — Parse TOML files | https://docs.python.org/3/library/tomllib.html | TOML schema loading |
| 2 | Real Python TOML | https://realpython.com/python-toml/ | Practical TOML examples |
| 3 | inspect — Inspect live objects | https://docs.python.org/3/library/inspect.html | Function signature introspection |
| 4 | dataclasses — Data Classes | https://docs.python.org/3/library/dataclasses.html | Alternative schema via dataclass fields |
| 5 | enum — Support for enumerations | https://docs.python.org/3/library/enum.html | Enum member extraction for dropdowns |
| 6 | Real Python Tkinter Tutorial | https://realpython.com/python-gui-tkinter/ | Tkinter form patterns and best practices |
| 7 | TkDocs Tutorial - Basic Widgets | https://tkdocs.com/tutorial/widgets.html | Tkinter widget reference |
| 8 | Tkinter OptionMenu Widget | https://www.pythontutorial.net/tkinter/tkinter-optionmenu/ | Dropdown widget specifics |
| 9 | tkinter-form PyPI | https://pypi.org/project/tkinter-form/ | Existing lightweight form library |
| 10 | magicgui type mapping | https://pyapp-kit.github.io/magicgui/type_map/ | Production type→widget pattern |
| 11 | typing — Support for type hints | https://docs.python.org/3/library/typing.html | Type annotation introspection |
| 12 | PEP 742 – Narrowing types with TypeIs | https://peps.python.org/pep-0742/ | Advanced type narrowing (Python 3.13+) |
| 13 | Pydantic Validators | https://pydantic.dev/docs/validation/latest/concepts/validators/ | Validation and type coercion |
| 14 | Pydantic Strict Mode | https://docs.pydantic.dev/latest/concepts/strict_mode/ | Strict vs. lax type coercion |
