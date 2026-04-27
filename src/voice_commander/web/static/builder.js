// @ts-check
// builder.js — wires Drawflow to canonical-graph JSON via fetch.
(async function () {
  'use strict';

  /**
   * @typedef {Object} GraphInput
   * @property {string} name
   * @property {string} type       - e.g. "str", "int", "bool", "float"
   * @property {boolean} required
   * @property {string} description
   */

  /**
   * @typedef {Object} CanonicalNode
   * @property {string} id          - Unique within graph, e.g. "n1713000000000"
   * @property {string} ref         - Node reference, e.g. "pipeline.fetch", "control.branch"
   * @property {Object<string, *>} kwargs - Argument values keyed by arg name
   * @property {[number, number]} pos    - Canvas position [x, y]
   */

  /**
   * @typedef {Object} PortRef
   * @property {string} node_id  - Canonical node ID, e.g. "n1713000000000"
   * @property {string} port     - Port name, e.g. "ok", "error", "in"
   */

  /**
   * @typedef {Object} CanonicalEdge
   * @property {PortRef} from  - Source port reference
   * @property {PortRef} to    - Destination port reference
   */
  // NOTE: Server wire format serialises from/to as flat strings "node_id.port"
  // (see graph_schema.py:101); builder.js uses objects internally.

  /**
   * @typedef {Object} CanonicalGraph
   * @property {number} schema_version        - Always 1
   * @property {string} name
   * @property {"command"|"workflow"} kind
   * @property {string} description
   * @property {string[]} synonyms
   * @property {GraphInput[]} inputs
   * @property {boolean} llm_visible
   * @property {boolean} strict
   * @property {boolean} enabled
   * @property {number} timeout_ms
   * @property {number} foreach_iteration_cap
   * @property {CanonicalNode[]} nodes
   * @property {CanonicalEdge[]} edges
   */

  /**
   * @typedef {Object} PortResolution
   * @property {string[]} inPorts   - Input port names
   * @property {string[]} outPorts  - Output port names
   */

  /**
   * @typedef {Object} ValidationError
   * @property {"error"|"warning"} [severity]  - Defaults to "error"
   * @property {string} message
   * @property {string} [node_id]              - Affected node ID
   */

  // ---------- Bootstrap ----------
  const root = document.getElementById('builder-root');
  if (!root) return;

  const existing = JSON.parse(root.dataset.existing || 'null');
  const initialKind = root.dataset.kind || 'command';

  // Populate cancel link href based on kind
  const cancelLink = document.getElementById('builder-cancel');
  if (cancelLink) cancelLink.href = '/page/' + initialKind + 's';

  // ---------- Drawflow init ----------
  const canvasEl = document.getElementById('builder-canvas');
  // Guard: Drawflow may not be loaded (e.g. in test environments)
  if (typeof Drawflow === 'undefined') {
    canvasEl.innerHTML = '<div class="p-4 text-neutral-400 text-sm">Drawflow not loaded.</div>';
    return;
  }

  const editor = new Drawflow(canvasEl);
  editor.reroute = true;
  editor.start();

  editor.on('connectionCreated', () => { queueMicrotask(tintAllConnections); });
  editor.on('connectionRemoved', () => { queueMicrotask(tintAllConnections); });

  // ---------- Load palette ----------
  let palette = { pipeline: [], commands: [], workflows: [], control: {}, value: {} };
  try {
    const resp = await fetch('/graph/palette');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    palette = await resp.json();
  } catch (e) {
    console.error('Failed to load palette', e);
    const errEl = document.getElementById('builder-errors');
    if (errEl) {
      errEl.textContent = 'Failed to load node palette: ' + e.message + '. Try refreshing.';
      errEl.classList.remove('hidden');
    }
  }

  // Build a lookup: ref -> descriptor
  const paletteByRef = {};
  for (const item of palette.pipeline) {
    paletteByRef['pipeline.' + item.name] = item;
  }
  for (const item of palette.commands) {
    paletteByRef['command.' + item.name] = item;
  }
  for (const item of palette.workflows) {
    paletteByRef['workflow.' + item.name] = item;
  }
  for (const [k, v] of Object.entries(palette.control || {})) {
    paletteByRef['control.' + k] = v;
  }
  for (const [k, v] of Object.entries(palette.value || {})) {
    paletteByRef['value.' + k] = v;
  }

  // ---------- Palette rendering ----------
  const paletteEl = document.getElementById('builder-palette');

  /**
   * Render a section of the node palette (left sidebar) as a collapsible
   * <details> element with one draggable button per item.
   *
   * @param {string} title        - Section heading, e.g. "Pipeline"
   * @param {Array<{name: string, description?: string}>} items
   *   Palette descriptors; empty array is a no-op (early return).
   * @param {string} refPrefix    - Prefix prepended to item name to form
   *   the node ref, e.g. "pipeline." → "pipeline.fetch"
   * @param {boolean} [defaultOpen] - Whether the section is open by default.
   * @returns {void}
   */
  function renderPaletteSection(title, items, refPrefix, defaultOpen = true) {
    if (!items || items.length === 0) return;
    const details = document.createElement('details');
    if (defaultOpen) details.open = true;
    const summary = document.createElement('summary');
    const titleSpan = document.createElement('span');
    titleSpan.className = 'palette-section-title';
    titleSpan.textContent = title;
    summary.appendChild(titleSpan);
    details.appendChild(summary);
    for (const item of items) {
      const name = item.name || item;
      const ref = refPrefix + name;
      const categoryClass = 'palette-' + refPrefix.replace('.', '');
      const btn = document.createElement('button');
      btn.className = 'w-full text-left px-2 py-1 rounded text-xs text-neutral-200 hover:bg-neutral-700 hover:text-white truncate ' + categoryClass;
      btn.dataset.paletteName = name.toLowerCase();
      btn.textContent = name;
      btn.title = (item.description || '') + '\nRef: ' + ref;
      btn.onclick = () => addNodeToCanvas(ref, 100 + Math.random() * 200, 100 + Math.random() * 200);
      details.appendChild(btn);
    }
    paletteEl.appendChild(details);
  }

  // Add search filter input at top of palette
  const filterInput = document.createElement('input');
  filterInput.type = 'search';
  filterInput.id = 'palette-filter';
  filterInput.placeholder = 'filter…';
  paletteEl.appendChild(filterInput);

  const pipelineCount = palette.pipeline ? palette.pipeline.length : 0;
  const workflowCount = palette.workflows ? palette.workflows.length : 0;
  renderPaletteSection('Pipeline', palette.pipeline, 'pipeline.', pipelineCount <= 8);
  renderPaletteSection('Commands', palette.commands, 'command.', true);
  renderPaletteSection('Workflows', palette.workflows, 'workflow.', workflowCount <= 8);
  renderPaletteSection('Control', Object.keys(palette.control || {}).map(k => ({ name: k })), 'control.', true);
  renderPaletteSection('Value', Object.keys(palette.value || {}).map(k => ({ name: k })), 'value.', true);

  // Search filter handler
  filterInput.addEventListener('input', () => {
    const query = filterInput.value.toLowerCase().trim();
    const allDetails = paletteEl.querySelectorAll('details');
    allDetails.forEach(det => {
      let anyVisible = false;
      const buttons = det.querySelectorAll('button');
      buttons.forEach(btn => {
        const match = !query || (btn.dataset.paletteName || '').includes(query);
        btn.style.display = match ? '' : 'none';
        if (match) anyVisible = true;
      });
      // Keep section header visible even if no buttons match, so user sees the category
      det.style.display = anyVisible || !query ? '' : 'none';
    });
  });

  // ---------- Port resolution ----------
  /**
   * Normalise the raw `args` or `inputs` value from the palette descriptor
   * to a uniform array of `{name, type, description, required}` objects.
   *
   * Pipeline descriptors expose `args` as a dict `{name: {type, description, required}}`.
   * Command/workflow descriptors expose `inputs` as an array `[{name, type, required}]`.
   * This helper converts both to the same array shape so all downstream code
   * can iterate uniformly.
   *
   * @param {Object|Array} rawArgs - Either a dict or array of arg descriptors.
   * @returns {Array<{name: string, type: string, description: string, required: boolean}>}
   */
  function normalizeArgs(rawArgs) {
    if (!rawArgs) return [];
    if (Array.isArray(rawArgs)) return rawArgs;
    // Dict shape: {name: {type, description, required}}
    return Object.entries(rawArgs).map(([name, meta]) => ({
      name,
      type: (meta && meta.type) || 'str',
      description: (meta && meta.description) || '',
      required: !!(meta && meta.required),
    }));
  }

  /**
   * Determine input and output port names for a node reference.
   *
   * Hard-coded port layouts exist for value.* and control.* nodes.
   * Pipeline / command / workflow nodes derive ports from the palette
   * descriptor's `args` (→ input param ports) and `returns` (→ output ports).
   * Unknown refs fall back to `{inPorts: ['in'], outPorts: ['ok', 'error']}`.
   *
   * @param {string} ref           - Node reference, e.g. "value.input",
   *   "control.branch", "pipeline.fetch"
   * @param {GraphInput[]} [graphInputs]
   *   Graph-level input definitions; only used when ref is "value.input"
   *   to derive output port names from input parameter names.
   * @returns {PortResolution} Object with `inPorts` and `outPorts` arrays.
   */
  function resolvePortsForRef(ref, graphInputs) {
    if (ref === 'value.input') {
      const ins = graphInputs || [];
      return { inPorts: [], outPorts: ins.map(i => i.name) };
    }
    if (ref === 'value.output') {
      return { inPorts: ['in', 'value'], outPorts: [] };
    }
    if (ref === 'value.constant') {
      return { inPorts: [], outPorts: ['value'] };
    }
    if (ref === 'control.branch') {
      return { inPorts: ['in', 'cond'], outPorts: ['true', 'false'] };
    }
    if (ref === 'control.foreach') {
      return { inPorts: ['in', 'list'], outPorts: ['item', 'after'] };
    }
    // pipeline, command, workflow
    const desc = paletteByRef[ref];
    if (desc) {
      const args = normalizeArgs(desc.args || desc.inputs || {});
      const inPorts = ['in', ...args.map(a => a.name)];
      const returnKeys = desc.returns ? Object.keys(desc.returns) : [];
      const outPorts = ['ok', 'error', ...returnKeys.filter(k => k !== 'ok' && k !== 'error')];
      return { inPorts, outPorts };
    }
    // fallback
    return { inPorts: ['in'], outPorts: ['ok', 'error'] };
  }

  function getGraphInputs() {
    if (existing && existing.inputs) return existing.inputs;
    return [];
  }

  // ---------- HTML escape for attribute / text context ----------
  // Neutralises HTML-special chars for attribute values, text nodes,
  // and custom attribute name fragments (prevents injection, not
  // spec-valid attribute names from arbitrary input).
  function escapeAttr(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // ---------- Port color map (mirrors builder.css) ----------
  const PORT_COLORS = {
    in: '#9ca3af',
    ok: '#22c55e',
    error: '#ef4444',
    true: '#22c55e',
    false: '#f97316',
    item: '#06b6d4',
    after: '#06b6d4',
  };
  const PORT_COLOR_DEFAULT = '#3b82f6';

  /**
   * Tint all SVG connection paths in the editor to match their source port color.
   * Called after connections are added (creation or hydration).
   */
  function tintAllConnections() {
    // Each connection SVG has class e.g. "connection node_in_node-2 node_out_node-1 output_1 input_1"
    const svgPaths = document.querySelectorAll('.drawflow svg.connection .main-path');
    svgPaths.forEach(path => {
      const parent = path.closest('svg.connection');
      if (!parent) return;
      // Extract source node id and output index from class list
      const classes = Array.from(parent.classList);
      const nodeOutClass = classes.find(c => c.startsWith('node_out_node-'));
      const outputClass = classes.find(c => c.startsWith('output_'));
      if (!nodeOutClass || !outputClass) return;
      const srcNodeId = nodeOutClass.replace('node_out_node-', '');
      const outIdx = parseInt(outputClass.replace('output_', ''), 10) - 1;
      const srcDom = document.getElementById('node-' + srcNodeId);
      if (!srcDom) return;
      const outEl = srcDom.querySelector('.output_' + (outIdx + 1));
      if (!outEl) return;
      const portName = outEl.getAttribute('data-port-name') || '';
      const color = PORT_COLORS[portName] || PORT_COLOR_DEFAULT;
      path.style.stroke = color;
    });
  }

  // ---------- Add node to canvas ----------
  /**
   * Create a node on the Drawflow canvas and bind canonical metadata.
   *
   * Resolves ports via {@link resolvePortsForRef}, looks up the palette
   * descriptor for the ref, builds kwargs HTML inputs, and registers
   * the node with Drawflow. Canonical metadata (`_canonical_id`, `_ref`,
   * `_in_ports`, `_out_ports`, `_kwargs`) is stored in the node's data
   * object for later retrieval by {@link exportCanonical}.
   *
   * @param {string} ref           - Node reference, e.g. "pipeline.fetch"
   * @param {number} x             - Canvas X coordinate (pixels)
   * @param {number} y             - Canvas Y coordinate (pixels)
   * @param {string} [canonicalId] - Canonical node ID; auto-generated
   *   as `"n" + Date.now()` when omitted.
   * @param {Object<string, *>} [kwargsData]
   *   Argument values keyed by arg name; defaults to `{}`.
   * @returns {number} Drawflow numeric node ID.
   */
  function addNodeToCanvas(ref, x, y, canonicalId, kwargsData) {
    const id = canonicalId || ('n' + Date.now());
    const kwargVals = kwargsData || {};
    const { inPorts, outPorts } = resolvePortsForRef(ref, getGraphInputs());

    // Build kwargs HTML for inline node display
    const desc = paletteByRef[ref];
    let kwargsHtml = '';
    if (desc) {
      const args = normalizeArgs(desc.args || desc.inputs || {});
      for (const arg of args) {
        const val = kwargVals[arg.name] !== undefined ? kwargVals[arg.name] : '';
        const safeName = escapeAttr(arg.name);
        const safeVal = escapeAttr(val);
        if (arg.type === 'bool' || arg.type === 'boolean') {
          kwargsHtml += `<label class="flex items-center gap-1"><input type="checkbox" df-kwarg-${safeName} ${val ? 'checked' : ''}><span class="text-xs">${safeName}</span></label>`;
        } else {
          const inputType = (arg.type === 'int' || arg.type === 'integer') ? 'number' : 'text';
          kwargsHtml += `<input type="${inputType}" df-kwarg-${safeName} value="${safeVal}" placeholder="${safeName}" class="df-input" title="${safeName}">`;
        }
      }
    }

    // Short display name for the node header
    const shortName = ref.includes('.') ? ref.split('.').slice(1).join('.') : ref;
    const safeShortName = escapeAttr(shortName);
    const safeId = escapeAttr(id);

    const html = `<div class="df-node-wrap" title="${safeId}">
  <div class="df-node-title">${safeShortName}</div>
  ${kwargsHtml ? `<div class="df-node-kwargs">${kwargsHtml}</div>` : ''}
</div>`;

    // Build initial data object
    const nodeData = {
      _canonical_id: id,
      _ref: ref,
      _in_ports: inPorts,
      _out_ports: outPorts,
      _kwargs: { ...kwargVals },
    };
    // Store kwargs flat for df-* binding
    for (const [k, v] of Object.entries(kwargVals)) {
      nodeData['kwarg-' + k] = String(v);
    }

    const dfId = editor.addNode(
      shortName.replace(/[^a-zA-Z0-9_]/g, '_'),  // name (no dots/special chars)
      inPorts.length,
      outPorts.length,
      x, y,
      'vc-node vc-node-' + (ref.split('.')[0] || 'pipeline'),
      nodeData,
      html,
    );

    // Tag each port DOM element with data-port-name for CSS attribute selectors.
    // Use queueMicrotask to ensure Drawflow has appended the DOM.
    queueMicrotask(() => {
      const dom = document.getElementById('node-' + dfId);
      if (dom) {
        inPorts.forEach((name, i) => {
          const el = dom.querySelector('.input_' + (i + 1));
          if (el) el.setAttribute('data-port-name', name);
        });
        outPorts.forEach((name, i) => {
          const el = dom.querySelector('.output_' + (i + 1));
          if (el) el.setAttribute('data-port-name', name);
        });
      }
    });

    return dfId;
  }

  // ---------- Canonical → Drawflow (hydration) ----------
  /**
   * Populate the Drawflow editor from a canonical graph.
   *
   * Clears the canvas, re-creates every node via {@link addNodeToCanvas},
   * builds a mapping from canonical node IDs to Drawflow numeric IDs,
   * then wires all edges by resolving port indices from each node's
   * `_in_ports` / `_out_ports` arrays.
   *
   * @param {CanonicalGraph} graph - Canonical graph object with `nodes`
   *   and `edges` arrays. May be the initial graph loaded from the server
   *   or a freshly parsed JSON payload.
   * @returns {void}
   */
  function hydrateFromCanonical(graph) {
    editor.clear();

    // Map canonical node id → Drawflow numeric id
    const idMap = {};  // canonical_id -> df_numeric_id

    // Add all nodes
    for (const node of graph.nodes || []) {
      const [px, py] = node.pos || [100, 100];
      const dfId = addNodeToCanvas(node.ref, px, py, node.id, node.kwargs || {});
      idMap[node.id] = dfId;
    }

    // Add connections
    for (const edge of graph.edges || []) {
      const fromNodeDfId = idMap[edge.from.node_id];
      const toNodeDfId = idMap[edge.to.node_id];
      if (fromNodeDfId == null || toNodeDfId == null) continue;

      // Find port indices
      const fromNode = editor.getNodeFromId(fromNodeDfId);
      const toNode = editor.getNodeFromId(toNodeDfId);
      if (!fromNode || !toNode) continue;

      const outPorts = fromNode.data._out_ports || [];
      const inPorts = toNode.data._in_ports || [];
      const outIdx = outPorts.indexOf(edge.from.port);
      const inIdx = inPorts.indexOf(edge.to.port);

      if (outIdx === -1 || inIdx === -1) continue;

      editor.addConnection(
        fromNodeDfId, toNodeDfId,
        'output_' + (outIdx + 1),
        'input_' + (inIdx + 1),
      );
    }

    // Tint connection lines after hydration
    queueMicrotask(tintAllConnections);
  }

  // ---------- Drawflow → canonical (export) ----------
  /**
   * Serialise the current Drawflow canvas state to canonical Graph JSON.
   *
   * Reads form inputs (name, kind, description, llm_visible, strict),
   * iterates every Drawflow node to build canonical {@link CanonicalNode}
   * entries, and maps Drawflow numeric port IDs back to named ports via
   * each node's stored `_out_ports` / `_in_ports` arrays.
   *
   * The returned object matches the Python `Graph` dataclass
   * (`src/voice_commander/commands/graph.py`) and the wire format
   * consumed by `/graph/validate` and `/graph/save`.
   *
   * @returns {CanonicalGraph} Canonical graph JSON ready for POST to
   *   the server. Shape: `{schema_version, name, kind, description,
   *   synonyms, inputs, llm_visible, strict, enabled, timeout_ms,
   *   foreach_iteration_cap, nodes, edges}`.
   *
   * @see CanonicalGraph
   */
  function exportCanonical() {
    const name = document.getElementById('builder-name').value.trim() || 'untitled';
    const kind = document.getElementById('builder-kind').value || initialKind;
    const llmVisible = document.getElementById('builder-llm-visible').checked;
    const strict = document.getElementById('builder-strict').checked;
    const description = document.getElementById('builder-description').value.trim();

    const dfData = editor.export().drawflow.Home.data;

    const nodes = [];
    const edges = [];

    for (const [dfIdStr, dfNode] of Object.entries(dfData)) {
      const d = dfNode.data || {};
      const canonicalId = d._canonical_id || ('n' + dfIdStr);
      const ref = d._ref || 'pipeline.unknown';
      const outPorts = d._out_ports || ['ok', 'error'];
      const inPorts = d._in_ports || ['in'];

      // Extract kwargs from df-kwarg-* fields in data
      const kwargs = {};
      for (const [k, v] of Object.entries(d)) {
        if (k.startsWith('kwarg-')) {
          kwargs[k.slice(6)] = v;
        }
      }

      nodes.push({
        id: canonicalId,
        ref,
        kwargs,
        pos: [Math.round(dfNode.pos_x), Math.round(dfNode.pos_y)],
      });

      // Emit edges from output ports
      for (const [outPortName, portData] of Object.entries(dfNode.outputs || {})) {
        const outIdx = parseInt(outPortName.replace('output_', ''), 10) - 1;
        const canonicalOutPort = outPorts[outIdx];
        if (canonicalOutPort == null) continue;

        for (const conn of portData.connections || []) {
          const targetDfId = String(conn.node);
          const targetInPortName = conn.input;  // e.g. 'input_2'
          const targetInIdx = parseInt(targetInPortName.replace('input_', ''), 10) - 1;

          // Find target node's canonical id and port name
          const targetDfNode = dfData[targetDfId];
          if (!targetDfNode) continue;
          const targetD = targetDfNode.data || {};
          const targetCanonicalId = targetD._canonical_id || ('n' + targetDfId);
          const targetInPorts = targetD._in_ports || ['in'];
          const canonicalInPort = targetInPorts[targetInIdx];
          if (canonicalInPort == null) continue;

          edges.push({
            from: { node_id: canonicalId, port: canonicalOutPort },
            to: { node_id: targetCanonicalId, port: canonicalInPort },
          });
        }
      }
    }

    // Derive inputs from value.input node's out ports if it exists
    let inputs = [];
    if (existing && existing.inputs) inputs = existing.inputs;
    const inputNode = nodes.find(n => n.ref === 'value.input');
    if (inputNode) {
      // Find the corresponding drawflow node entry
      const inputDfEntry = Object.values(dfData).find(
        n => (n.data || {})._canonical_id === inputNode.id
      );
      if (inputDfEntry) {
        const outPortsList = (inputDfEntry.data || {})._out_ports || [];
        inputs = outPortsList.map(p => ({ name: p, type: 'str', required: true, description: '' }));
      }
    }

    return {
      schema_version: 1,
      name,
      kind,
      description,
      synonyms: existing ? (existing.synonyms || []) : [],
      inputs,
      llm_visible: llmVisible,
      strict,
      enabled: existing ? (existing.enabled !== false) : true,
      timeout_ms: existing ? (existing.timeout_ms || 5000) : 5000,
      foreach_iteration_cap: existing ? (existing.foreach_iteration_cap || 50) : 50,
      nodes,
      edges,
    };
  }

  // ---------- Config rail ----------
  const configEl = document.getElementById('builder-config');
  let selectedDfId = null;

  /**
   * Update the right-sidebar config panel for the selected node.
   *
   * When a node is selected, renders its ref, canonical ID, kwargs
   * input fields (with live two-way binding to Drawflow node data),
   * port list, and a delete button. Passing `null` clears the panel.
   *
   * @param {number|null} dfId - Drawflow numeric node ID, or `null`
   *   to clear the config rail.
   * @returns {void}
   */
  function renderConfigRail(dfId) {
    selectedDfId = dfId;
    configEl.innerHTML = '';
    if (dfId == null) return;

    const node = editor.getNodeFromId(dfId);
    if (!node) return;

    const d = node.data || {};
    const ref = d._ref || '';
    const outPorts = d._out_ports || [];
    const inPorts = d._in_ports || [];

    // Header
    const header = document.createElement('div');
    header.className = 'font-semibold text-teal-300 mb-2 text-xs break-all';
    header.textContent = ref + ' [' + (d._canonical_id || dfId) + ']';
    configEl.appendChild(header);

    // kwargs section
    const desc = paletteByRef[ref];
    const args = desc ? normalizeArgs(desc.args || desc.inputs || {}) : [];
    if (args.length > 0) {
      const kh = document.createElement('div');
      kh.className = 'text-xs font-semibold text-neutral-400 uppercase tracking-wider mt-2 mb-1';
      kh.textContent = 'kwargs';
      configEl.appendChild(kh);

      for (const arg of args) {
        const currentVal = (d._kwargs || {})[arg.name] !== undefined
          ? (d._kwargs || {})[arg.name]
          : (d['kwarg-' + arg.name] || '');

        const row = document.createElement('div');
        row.className = 'mb-1';

        const label = document.createElement('label');
        label.className = 'block text-xs text-neutral-400 mb-0.5';
        label.textContent = arg.name + (arg.required ? ' *' : '') + ' (' + arg.type + ')';
        row.appendChild(label);

        let input;
        if (arg.type === 'bool' || arg.type === 'boolean') {
          input = document.createElement('input');
          input.type = 'checkbox';
          input.checked = !!currentVal;
          input.onchange = () => {
            editor.updateNodeDataFromId(dfId, { ['kwarg-' + arg.name]: input.checked });
          };
        } else {
          input = document.createElement('input');
          input.type = (arg.type === 'int' || arg.type === 'integer') ? 'number' : 'text';
          input.value = String(currentVal);
          input.className = 'w-full bg-neutral-800 border border-neutral-600 rounded px-1 py-0.5 text-xs text-neutral-200';
          input.onchange = () => {
            editor.updateNodeDataFromId(dfId, { ['kwarg-' + arg.name]: input.value });
          };
        }
        row.appendChild(input);
        configEl.appendChild(row);
      }
    }

    // Ports section
    const ph = document.createElement('div');
    ph.className = 'text-xs font-semibold text-neutral-400 uppercase tracking-wider mt-3 mb-1';
    ph.textContent = 'Ports';
    configEl.appendChild(ph);

    function portRow(name, direction) {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-1 text-xs py-0.5';
      const swatch = document.createElement('span');
      swatch.className = 'vc-port-swatch';
      swatch.setAttribute('data-port-name', name);
      row.appendChild(swatch);
      const nameEl = document.createElement('span');
      nameEl.className = direction === 'out' ? 'text-teal-400' : 'text-sky-400';
      nameEl.textContent = (direction === 'in' ? '→ ' : '← ') + name;
      row.appendChild(nameEl);
      configEl.appendChild(row);
    }
    for (const p of inPorts) portRow(p, 'in');
    for (const p of outPorts) portRow(p, 'out');

    // Delete button
    const delBtn = document.createElement('button');
    delBtn.className = 'mt-4 text-xs px-2 py-1 rounded bg-red-800 hover:bg-red-700 text-white';
    delBtn.textContent = 'Delete node';
    delBtn.onclick = () => {
      editor.removeNodeId('node-' + dfId);
      configEl.innerHTML = '';
      selectedDfId = null;
    };
    configEl.appendChild(delBtn);
  }

  editor.on('nodeSelected', (id) => renderConfigRail(id));
  editor.on('nodeUnselected', () => { configEl.innerHTML = ''; selectedDfId = null; });
  editor.on('nodeRemoved', () => { configEl.innerHTML = ''; selectedDfId = null; });

  // ---------- Header wiring ----------
  if (existing) {
    document.getElementById('builder-name').value = existing.name || '';
    document.getElementById('builder-kind').value = existing.kind || initialKind;
    document.getElementById('builder-description').value = existing.description || '';
    document.getElementById('builder-llm-visible').checked = existing.llm_visible !== false;
    document.getElementById('builder-strict').checked = existing.strict !== false;
  } else {
    document.getElementById('builder-kind').value = initialKind;
  }

  // Update cancel link when kind changes
  document.getElementById('builder-kind').onchange = function () {
    if (cancelLink) cancelLink.href = '/page/' + this.value + 's';
  };

  // ---------- Validation error display ----------
  /**
   * Display validation errors and warnings in the error banner.
   *
   * Separates hard errors from warnings, builds a DOM-only display
   * (no innerHTML with user data — XSS safe), and toggles the
   * `#builder-errors` element's visibility.
   *
   * @param {ValidationError[]|null} errors - Array of validation
   *   error objects, or `null` / empty array to hide the banner.
   * @returns {boolean} `true` if any hard errors exist, `false` otherwise.
   */
  function showErrors(errors) {
    const errEl = document.getElementById('builder-errors');
    if (!errors || errors.length === 0) {
      errEl.classList.add('hidden');
      errEl.textContent = '';
      return false;
    }
    const hardErrors = errors.filter(e => !e.severity || e.severity === 'error');
    const warnings = errors.filter(e => e.severity === 'warning');

    // Build error display using DOM methods only — never innerHTML with user data (XSS prevention).
    errEl.textContent = '';

    function appendErrorItems(items, labelText, labelClass) {
      if (!items.length) return;
      if (errEl.childNodes.length > 0) {
        errEl.appendChild(document.createTextNode(' | '));
      }
      const label = document.createElement('span');
      label.className = labelClass || 'font-semibold';
      label.textContent = labelText + ': ';
      errEl.appendChild(label);
      items.forEach((e, i) => {
        if (i > 0) errEl.appendChild(document.createTextNode('; '));
        let msg = e.message || String(e);
        if (e.node_id) msg = '[' + e.node_id + '] ' + msg;
        errEl.appendChild(document.createTextNode(msg));
      });
    }

    appendErrorItems(hardErrors, 'Errors', 'font-semibold');
    appendErrorItems(warnings, 'Warnings', 'font-semibold text-amber-400');

    errEl.classList.remove('hidden');
    return hardErrors.length > 0;
  }

  const statusEl = document.getElementById('builder-status');
  function setStatus(msg, color) {
    statusEl.textContent = msg;
    statusEl.style.color = color || '';
    if (msg) setTimeout(() => { if (statusEl.textContent === msg) statusEl.textContent = ''; }, 4000);
  }

  // ---------- Validate button ----------
  const saveBtn = document.getElementById('builder-save');

  document.getElementById('builder-validate').onclick = async () => {
    const canonical = exportCanonical();
    try {
      const r = await fetch('/graph/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(canonical),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        showErrors(body.errors || [{message: `Server error ${r.status}`}]);
        setStatus('Validate failed.', 'red');
        saveBtn.disabled = true;
        return;
      }
      const body = await r.json();
      const hasErrors = showErrors(body.errors || []);
      if (!hasErrors) setStatus('Valid.', '#4ade80');
      saveBtn.disabled = hasErrors;
    } catch (e) {
      setStatus('Validate failed: ' + e, 'red');
    }
  };

  // ---------- Save button ----------
  saveBtn.onclick = async () => {
    const canonical = exportCanonical();
    if (!canonical.name || canonical.name === 'untitled') {
      setStatus('Set a graph name first.', 'orange');
      return;
    }
    try {
      const r = await fetch('/graph/' + encodeURIComponent(canonical.name), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(canonical),
      });
      const body = await r.json();
      if (r.ok) {
        showErrors([]);
        setStatus('Saved.', '#4ade80');
        saveBtn.disabled = false;
      } else {
        const hasErrors = showErrors(body.errors || []);
        saveBtn.disabled = hasErrors;
        setStatus('Save failed.', 'red');
      }
    } catch (e) {
      setStatus('Save error: ' + e, 'red');
    }
  };

  // ---------- Hydrate existing graph ----------
  if (existing) {
    hydrateFromCanonical(existing);
  }

})();
