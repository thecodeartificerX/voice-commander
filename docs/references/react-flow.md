# React Flow v11 — Quick Reference

**Package:** `reactflow` ^11.11.4  
**Docs:** https://reactflow.dev (v11)  
**Note:** This is v11 (`reactflow` package). v12 is a separate package (`@xyflow/react`) with breaking API changes — do **not** upgrade without reading the migration guide.

---

## Installation & Imports

```bash
pnpm add reactflow
```

```tsx
import ReactFlow, {
  Background, Controls, MiniMap,
  useNodesState, useEdgesState,
  addEdge, type Node, type Edge,
} from 'reactflow';
import 'reactflow/dist/style.css';  // REQUIRED — omitting causes invisible nodes
```

The CSS import is mandatory. Without it, nodes render without their default styling and handles are invisible.

---

## Core Component

```tsx
<ReactFlow
  nodes={nodes}
  edges={edges}
  onNodesChange={onNodesChange}
  onEdgesChange={onEdgesChange}
  onConnect={onConnect}
  nodeTypes={nodeTypes}       // custom node registry
  edgeTypes={edgeTypes}       // custom edge registry
  fitView                     // auto-fit on first render
  deleteKeyCode="Delete"
>
  <Background />
  <Controls />
  <MiniMap />
</ReactFlow>
```

React Flow requires a parent container with explicit `width` and `height` (or `100%` with a sized ancestor). Without it the canvas renders at 0×0.

---

## Node Types

### Built-in

| Type | Description |
|---|---|
| `default` | Node with one target handle (top) and one source handle (bottom) |
| `input` | Source-only — no target handle |
| `output` | Target-only — no source handle |
| `group` | Transparent grouping container; children nest inside |

### Custom Node Registration

```tsx
// Define
const ToolNode = ({ data }: NodeProps<ToolNodeData>) => (
  <div className="tool-node">
    <Handle type="target" position={Position.Top} />
    {data.label}
    <Handle type="source" position={Position.Bottom} />
  </div>
);

// Register
const nodeTypes = { tool: ToolNode, branch: BranchNode, foreach: ForeachNode };

// Use
<ReactFlow nodeTypes={nodeTypes} ... />
```

`nodeTypes` must be defined **outside** the component or wrapped in `useMemo` — recreating it on every render causes React Flow to remount all nodes.

---

## Edge Types

### Built-in

| Type | Description |
|---|---|
| `default` (bezier) | Smooth S-curve between nodes |
| `step` | Right-angle path with corner |
| `smoothstep` | Rounded corners on step path |
| `straight` | Direct line |

### Custom Edge

```tsx
const ControlEdge = ({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, data,
}: EdgeProps) => {
  const [edgePath] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  return (
    <>
      <BaseEdge id={id} path={edgePath} />
      <EdgeLabelRenderer>
        <div style={{ position: 'absolute', transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}>
          {data?.label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
};
```

---

## Handles

```tsx
import { Handle, Position } from 'reactflow';

// Source handle (output)
<Handle type="source" position={Position.Bottom} id="ok" />
<Handle type="source" position={Position.Bottom} id="error" />

// Target handle (input)
<Handle type="target" position={Position.Top} id="input" />
```

- `id` is optional when a node has one handle of each type. Required when multiple handles exist.
- `isValidConnection` prop: `(connection: Connection) => boolean` — guards against invalid wiring.

---

## State Hooks

### Controlled mode (used in this project)

```tsx
const [nodes, setNodes, onNodesChange] = useNodesState<NodeData>(initialNodes);
const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

const onConnect = useCallback(
  (params: Connection) => setEdges((eds) => addEdge(params, eds)),
  [setEdges],
);
```

`onNodesChange` / `onEdgesChange` handle position updates, selection, deletion. Pass them to `<ReactFlow>` to get interactive nodes/edges.

### Uncontrolled mode

Pass `defaultNodes` / `defaultEdges` instead of `nodes` / `edges`. React Flow manages state internally; you cannot read it without `useReactFlow()`.

---

## Hooks

| Hook | Returns | Use |
|---|---|---|
| `useReactFlow()` | `ReactFlowInstance` | Programmatic control (fit, zoom, add nodes) |
| `useNodes<T>()` | `Node<T>[]` | Read current nodes (within `ReactFlowProvider`) |
| `useEdges()` | `Edge[]` | Read current edges |
| `useNodeId()` | `string` | Current node's id (inside a custom node component) |
| `useUpdateNodeInternals()` | `fn(id)` | Force handle recalculation after DOM changes |

```tsx
const { fitView, setCenter, zoomTo, getNodes, project } = useReactFlow();

// Fit all nodes in view
fitView({ padding: 0.2, duration: 300 });

// Center on a node
setCenter(x, y, { zoom: 1.5, duration: 200 });

// Convert screen coords to flow coords (used for drag-and-drop)
const flowPos = project({ x: event.clientX, y: event.clientY });
```

`useReactFlow()` requires the component to be a **descendant** of `<ReactFlowProvider>` (or inside `<ReactFlow>` itself, which implicitly provides the context).

---

## Drag and Drop

Pattern used by the Builder palette:

```tsx
// Palette item — set drag data
const onDragStart = (event: DragEvent, nodeType: string) => {
  event.dataTransfer.setData('application/reactflow', nodeType);
  event.dataTransfer.effectAllowed = 'move';
};

// Canvas — accept drop
const onDragOver = (event: DragEvent) => {
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
};

const onDrop = (event: DragEvent) => {
  event.preventDefault();
  const type = event.dataTransfer.getData('application/reactflow');
  const position = reactFlowInstance.screenToFlowPosition({
    x: event.clientX,
    y: event.clientY,
  });
  const newNode: Node = { id: nanoid(), type, position, data: { label: type } };
  setNodes((nds) => nds.concat(newNode));
};
```

---

## Viewport Control

```tsx
const { fitView, setViewport, zoomIn, zoomOut, zoomTo } = useReactFlow();

fitView();                          // fit all nodes
fitView({ nodes: [{ id: 'n1' }] }); // fit specific nodes
zoomTo(1.5);                        // set zoom level
setViewport({ x: 0, y: 0, zoom: 1 });
```

---

## Data Flow (Node-to-Node via `data`)

React Flow does not have built-in data-wire semantics — those are implemented in `GraphRuntime`. React Flow just stores `data` on each node. `graphStore` serialises the React Flow state to the canonical `Graph` JSON schema when saving.

```tsx
// Node data type (project-specific)
interface ToolNodeData {
  toolName: string;
  kwargs: Record<string, unknown>;
  label: string;
}
```

---

## Project-Specific Usage

| File | Role |
|---|---|
| `web/builder-ui/src/canvas/Canvas.tsx` | Main canvas; mounts `<ReactFlow>`, wires store callbacks |
| `web/builder-ui/src/canvas/nodes/` | `ToolNode`, `BranchNode`, `ForeachNode`, `ValueNode`, etc. |
| `web/builder-ui/src/canvas/edges/` | `ControlEdge` (ok/error/true/false), `DataEdge` (data wires) |
| `web/builder-ui/src/store/graphStore.ts` | Zustand store; holds `nodes`, `edges`, serialises to Graph JSON |

The canvas uses **controlled mode** — all node/edge state lives in `graphStore`, not in React Flow's internal state. `onNodesChange`/`onEdgesChange` callbacks update the store; the store feeds back `nodes`/`edges` as props.

**ADR:** [`decisions/0071-builder-react-spa.md`](decisions/0071-builder-react-spa.md)
