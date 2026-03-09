VISUALIZER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Knowledge Graph</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Consolas', 'Monaco', monospace; background: #1a1a2e; color: #e0e0e0; }

  #header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 20px; background: #16213e; border-bottom: 1px solid #0f3460;
  }
  #header h1 { font-size: 16px; color: #e94560; }
  #stats { font-size: 13px; color: #a0a0b0; }
  #controls { display: flex; gap: 10px; align-items: center; }
  #controls button, #controls select {
    background: #0f3460; color: #e0e0e0; border: 1px solid #1a3a6a;
    padding: 4px 10px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px;
  }
  #controls button:hover, #controls select:hover { background: #1a4a7a; }
  #controls button.active { background: #e94560; border-color: #e94560; }

  #filter-bar {
    display: flex; flex-wrap: wrap; gap: 8px; padding: 8px 20px;
    background: #1a1a2e; border-bottom: 1px solid #0f3460; min-height: 36px;
  }
  .filter-chip {
    display: flex; align-items: center; gap: 4px; padding: 3px 10px;
    border-radius: 12px; font-size: 12px; cursor: pointer; user-select: none;
    border: 1px solid #333;
  }
  .filter-chip.checked { opacity: 1; }
  .filter-chip.unchecked { opacity: 0.4; }
  .filter-dot { width: 8px; height: 8px; border-radius: 50%; }

  #main { display: flex; height: calc(100vh - 82px); }
  #graph-container { flex: 1; }
  #detail-panel {
    width: 340px; background: #16213e; border-left: 1px solid #0f3460;
    padding: 16px; overflow-y: auto; display: none; font-size: 13px;
  }
  #detail-panel.open { display: block; }
  #detail-panel h2 { font-size: 15px; color: #e94560; margin-bottom: 8px; word-break: break-word; }
  #detail-panel .labels { color: #a0a0b0; font-size: 11px; margin-bottom: 10px; }
  #detail-panel .summary { color: #c0c0d0; margin-bottom: 12px; line-height: 1.4; }
  #detail-panel table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
  #detail-panel td {
    padding: 3px 6px; border-bottom: 1px solid #0f3460; font-size: 12px;
    vertical-align: top; word-break: break-word;
  }
  #detail-panel td:first-child { color: #a0a0b0; white-space: nowrap; width: 35%; }
  #detail-panel h3 { font-size: 13px; color: #e94560; margin: 10px 0 6px; }
  #detail-panel ul { list-style: none; }
  #detail-panel li {
    padding: 4px 0; border-bottom: 1px solid #0f3460; font-size: 12px; cursor: pointer;
  }
  #detail-panel li:hover { color: #e94560; }
  #detail-close {
    position: absolute; top: 8px; right: 8px; background: none; border: none;
    color: #a0a0b0; cursor: pointer; font-size: 16px;
  }
  #detail-close:hover { color: #e94560; }
</style>
</head>
<body>

<div id="header">
  <h1>Knowledge Graph</h1>
  <div id="stats">Loading...</div>
  <div id="controls">
    <button id="btn-pause">Pause</button>
    <select id="sel-interval">
      <option value="2000">2s</option>
      <option value="5000" selected>5s</option>
      <option value="10000">10s</option>
      <option value="30000">30s</option>
    </select>
    <button id="btn-fit">Fit</button>
  </div>
</div>

<div id="filter-bar"></div>

<div id="main">
  <div id="graph-container"></div>
  <div id="detail-panel" style="position:relative;">
    <button id="detail-close">&times;</button>
    <div id="detail-content"></div>
  </div>
</div>

<script>
(function() {
  const nodesDS = new vis.DataSet();
  const edgesDS = new vis.DataSet();

  const container = document.getElementById('graph-container');
  const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, {
    nodes: {
      shape: 'dot', size: 14,
      font: { size: 12, face: 'Consolas, Monaco, monospace', color: '#e0e0e0' },
      borderWidth: 1.5,
      shadow: { enabled: true, size: 4, color: 'rgba(0,0,0,0.3)' }
    },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.5 } },
      font: { size: 9, face: 'Consolas, Monaco, monospace', color: '#a0a0b0', align: 'middle' },
      color: { color: '#334', highlight: '#e94560', opacity: 0.6 },
      smooth: { type: 'continuous' }
    },
    physics: {
      solver: 'forceAtlas2Based',
      forceAtlas2Based: { gravitationalConstant: -60, centralGravity: 0.008, springLength: 120 },
      stabilization: { iterations: 120 }
    },
    interaction: { hover: true, tooltipDelay: 200 },
    groups: {}
  });

  let colorMap = {};
  let filterState = {};
  let pollTimer = null;
  let paused = false;
  let previousNodeIds = new Set();

  // --- Polling ---

  async function fetchGraph() {
    const checkedLabels = Object.entries(filterState)
      .filter(([, v]) => v)
      .map(([k]) => k);
    const params = new URLSearchParams({ limit: '500' });
    if (checkedLabels.length > 0) params.set('labels', checkedLabels.join(','));

    const resp = await fetch('/api/graph?' + params);
    const data = await resp.json();
    colorMap = Object.assign(colorMap, data.colorMap || {});

    applyGroupColors();

    const incomingNodeIds = new Set(data.nodes.map(n => n.id));
    const incomingEdgeIds = new Set(data.edges.map(e => e.id));
    const existingNodeIds = new Set(nodesDS.getIds());
    const existingEdgeIds = new Set(edgesDS.getIds());

    // Nodes to add
    const toAdd = data.nodes.filter(n => !existingNodeIds.has(n.id));
    // Nodes to update
    const toUpdate = data.nodes.filter(n => existingNodeIds.has(n.id));
    // Nodes to remove
    const toRemove = [...existingNodeIds].filter(id => !incomingNodeIds.has(id));

    // Highlight new nodes
    const isFirstLoad = previousNodeIds.size === 0;
    toAdd.forEach(n => {
      n.color = { background: colorMap[n.group] || '#999', border: isFirstLoad ? undefined : '#e94560' };
      n.borderWidth = isFirstLoad ? 1.5 : 4;
      if (!isFirstLoad) {
        n.shapeProperties = { borderDashes: [5, 5] };
      }
    });

    if (toAdd.length > 0) nodesDS.add(toAdd);
    if (toUpdate.length > 0) {
      toUpdate.forEach(n => {
        n.color = { background: colorMap[n.group] || '#999' };
      });
      nodesDS.update(toUpdate);
    }
    if (toRemove.length > 0) nodesDS.remove(toRemove);

    // Remove highlight after delay
    if (!isFirstLoad && toAdd.length > 0) {
      const newIds = toAdd.map(n => n.id);
      setTimeout(() => {
        newIds.forEach(id => {
          if (nodesDS.get(id)) {
            nodesDS.update({ id, borderWidth: 1.5, shapeProperties: { borderDashes: false } });
          }
        });
      }, 8000);
    }

    // Edges
    const edgesToAdd = data.edges.filter(e => !existingEdgeIds.has(e.id));
    const edgesToUpdate = data.edges.filter(e => existingEdgeIds.has(e.id));
    const edgesToRemove = [...existingEdgeIds].filter(id => !incomingEdgeIds.has(id));

    if (edgesToAdd.length > 0) edgesDS.add(edgesToAdd);
    if (edgesToUpdate.length > 0) edgesDS.update(edgesToUpdate);
    if (edgesToRemove.length > 0) edgesDS.remove(edgesToRemove);

    previousNodeIds = incomingNodeIds;
    document.getElementById('stats').textContent =
      data.nodes.length + ' nodes, ' + data.edges.length + ' edges';
  }

  function applyGroupColors() {
    const groups = {};
    for (const [label, color] of Object.entries(colorMap)) {
      groups[label] = { color: { background: color, border: color } };
    }
    network.setOptions({ groups });
  }

  function startPolling() {
    const interval = parseInt(document.getElementById('sel-interval').value, 10);
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { if (!paused) fetchGraph(); }, interval);
  }

  // --- Filters ---

  async function initFilters() {
    const resp = await fetch('/api/graph/labels');
    const data = await resp.json();
    colorMap = Object.assign(colorMap, data.colorMap || {});
    const defaultUnchecked = new Set(data.defaultUnchecked || []);

    const bar = document.getElementById('filter-bar');
    bar.innerHTML = '';
    data.labels.forEach(label => {
      const checked = !defaultUnchecked.has(label);
      filterState[label] = checked;

      const chip = document.createElement('div');
      chip.className = 'filter-chip ' + (checked ? 'checked' : 'unchecked');
      chip.innerHTML =
        '<span class="filter-dot" style="background:' + (colorMap[label] || '#999') + '"></span>' +
        '<span>' + label + '</span>';
      chip.addEventListener('click', () => {
        filterState[label] = !filterState[label];
        chip.className = 'filter-chip ' + (filterState[label] ? 'checked' : 'unchecked');
        previousNodeIds = new Set();
        fetchGraph();
      });
      bar.appendChild(chip);
    });
  }

  // --- Detail panel ---

  network.on('click', async function(params) {
    if (params.nodes.length === 0) {
      closeDetail();
      return;
    }
    const uuid = params.nodes[0];
    const resp = await fetch('/api/graph/node/' + encodeURIComponent(uuid));
    const detail = await resp.json();
    if (detail.error) return;
    showDetail(detail);
  });

  document.getElementById('detail-close').addEventListener('click', closeDetail);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetail(); });

  function showDetail(d) {
    const panel = document.getElementById('detail-panel');
    const content = document.getElementById('detail-content');

    let attrRows = '';
    if (d.attributes) {
      for (const [k, v] of Object.entries(d.attributes)) {
        if (v === null || v === undefined || v === '') continue;
        const display = typeof v === 'object' ? JSON.stringify(v) : String(v);
        attrRows += '<tr><td>' + esc(k) + '</td><td>' + esc(display) + '</td></tr>';
      }
    }

    let connHtml = '';
    if (d.connections && d.connections.length > 0) {
      connHtml = '<h3>Connections (' + d.connections.length + ')</h3><ul>';
      d.connections.forEach(c => {
        const arrow = c.direction === 'outgoing' ? '&rarr;' : '&larr;';
        connHtml += '<li data-uuid="' + esc(c.connectedUuid) + '">' +
          arrow + ' <b>' + esc(c.edgeName) + '</b> &mdash; ' + esc(c.connectedName || c.connectedUuid) +
          '</li>';
      });
      connHtml += '</ul>';
    }

    content.innerHTML =
      '<h2>' + esc(d.name || d.uuid) + '</h2>' +
      '<div class="labels">' + (d.labels || []).join(', ') + '</div>' +
      (d.summary ? '<div class="summary">' + esc(d.summary) + '</div>' : '') +
      '<table>' +
      '<tr><td>UUID</td><td>' + esc(d.uuid) + '</td></tr>' +
      '<tr><td>Created</td><td>' + esc(String(d.created_at || '')) + '</td></tr>' +
      attrRows +
      '</table>' +
      connHtml;

    // Click on connection to navigate
    content.querySelectorAll('li[data-uuid]').forEach(li => {
      li.addEventListener('click', () => {
        const targetUuid = li.getAttribute('data-uuid');
        network.selectNodes([targetUuid]);
        network.focus(targetUuid, { scale: 1.2, animation: true });
        fetch('/api/graph/node/' + encodeURIComponent(targetUuid))
          .then(r => r.json())
          .then(detail => { if (!detail.error) showDetail(detail); });
      });
    });

    panel.classList.add('open');
  }

  function closeDetail() {
    document.getElementById('detail-panel').classList.remove('open');
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // --- Controls ---

  document.getElementById('btn-pause').addEventListener('click', function() {
    paused = !paused;
    this.textContent = paused ? 'Resume' : 'Pause';
    this.classList.toggle('active', paused);
  });

  document.getElementById('sel-interval').addEventListener('change', startPolling);

  document.getElementById('btn-fit').addEventListener('click', () => {
    network.fit({ animation: true });
  });

  // --- Init ---

  initFilters().then(() => fetchGraph()).then(() => startPolling());
})();
</script>
</body>
</html>
"""
