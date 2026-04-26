// builder.js — wires Drawflow to canonical-graph JSON via fetch.
(async function () {
  'use strict';

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

  function renderPaletteSection(title, items, refPrefix) {
    if (!items || items.length === 0) return;
    const h = document.createElement('div');
    h.className = 'text-xs font-semibold text-neutral-400 uppercase tracking-wider mt-2 mb-1 px-1';
    h.textContent = title;
    paletteEl.appendChild(h);
    for (const item of items) {
      const name = item.name || item;
      const ref = refPrefix + name;
      const btn = document.createElement('button');
      btn.className = 'w-full text-left px-2 py-1 rounded text-xs text-neutral-200 hover:bg-neutral-700 hover:text-teal-300 truncate';
      btn.textContent = name;
      btn.title = (item.description || '') + '\nRef: ' + ref;
      btn.onclick = () => addNodeToCanvas(ref, 100 + Math.random() * 200, 100 + Math.random() * 200);
      paletteEl.appendChild(btn);
    }
  }

  renderPaletteSection('Pipeline', palette.pipeline, 'pipeline.');
  renderPaletteSection('Commands', palette.commands, 'command.');
  renderPaletteSection('Workflows', palette.workflows, 'workflow.');
  renderPaletteSection('Control', Object.keys(palette.control || {}).map(k => ({ name: k })), 'control.');
  renderPaletteSection('Value', Object.keys(palette.value || {}).map(k => ({ name: k })), 'value.');

  // ---------- Port resolution ----------
  function resolvePortsForRef(ref, graphInputs) {
    // graphInputs: array of {name, type} from existing graph or page data
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
      const args = desc.args || desc.inputs || [];
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

  // ---------- Add node to canvas ----------
  // Returns the Drawflow numeric node ID
  function addNodeToCanvas(ref, x, y, canonicalId, kwargsData) {
    const id = canonicalId || ('n' + Date.now());
    const kwargVals = kwargsData || {};
    const { inPorts, outPorts } = resolvePortsForRef(ref, getGraphInputs());

    // Build kwargs HTML for inline node display
    const desc = paletteByRef[ref];
    let kwargsHtml = '';
    if (desc) {
      const args = desc.args || desc.inputs || [];
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

    const html = `<div class="df-node-wrap">
  <div class="df-node-title">${safeShortName}</div>
  <div class="df-node-id">${safeId}</div>
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
    return dfId;
  }

  // ---------- Canonical → Drawflow (hydration) ----------
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
  }

  // ---------- Drawflow → canonical (export) ----------
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
    const args = desc ? (desc.args || desc.inputs || []) : [];
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
