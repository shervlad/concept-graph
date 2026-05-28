# Chunk 5: Frontend Rendering (index.html)

## Project Context

We're building "Knowledge Tree" — a web app that visualizes human knowledge as a force-directed graph. An LLM expands the graph from a seed word, the browser renders it in real time with WebGL (Deck.gl).

This chunk implements the **entire frontend** as a single `index.html` file. It replaces the existing Canvas 2D prototype with a high-performance Deck.gl WebGL renderer, integrates the force-simulation Web Worker (Chunk 4), and connects to the server API + SSE stream (Chunk 3).

**Working directory:** `~/projects/knowledge-tree/`

**Prerequisite chunks:**
- Chunk 3 (`server.py`) — REST API and SSE streaming
- Chunk 4 (`force-worker.js`) — d3-force Web Worker

## Files to Create

- `index.html` — complete frontend (HTML + CSS + JS in one file)

## CDN Dependencies

Load these in `<script>` tags (no bundler):

```html
<!-- Deck.gl standalone bundle -->
<script src="https://unpkg.com/deck.gl@9.1.4/dist.min.js"></script>
<!-- luma.gl (Deck.gl's WebGL engine — included in deck.gl bundle) -->
```

Deck.gl's standalone bundle exposes `deck.DeckGL`, `deck.OrthographicView`, `deck.ScatterplotLayer`, `deck.LineLayer`, `deck.TextLayer`, `deck.OrthographicController`, etc.

## Architecture Overview

```
index.html
    │
    ├── Data Layer
    │   ├── fetch /api/graph on load → init graph state
    │   ├── EventSource /api/stream → receive expand/status/error/done events
    │   └── Manage local arrays: nodes[], edges[], nodeIndex{}
    │
    ├── Force Worker (force-worker.js)
    │   ├── postMessage("init", {nodes, edges, params})
    │   ├── postMessage("add", {nodes, edges}) on SSE expand events
    │   ├── postMessage("params", {...}) on slider changes
    │   ├── onmessage("tick", Float32Array) → update positions
    │   └── onmessage("settled") → stop animation loop
    │
    ├── Deck.gl Renderer
    │   ├── ScatterplotLayer — node circles (domain-colored, sized by radius)
    │   ├── LineLayer — edges (weighted opacity)
    │   ├── TextLayer (node labels) — text inside circles, multi-line wrapped
    │   └── TextLayer (cluster labels) — visible at low zoom
    │
    └── UI Panels
        ├── Physics controls (7 sliders)
        ├── Expansion controls (seed, backend, start/stop)
        ├── Display controls (render budget, label threshold, edge opacity)
        ├── Cluster naming (button + auto toggle)
        ├── Search input
        └── Status bar
```

## Data Model (Client-Side)

```javascript
// Parallel arrays — index position is stable and matches the worker's position array
let nodes = [];       // [{id, name, year, domains, desc, radius, x, y, color, pinned}, ...]
let edges = [];       // [{source, target, weight, sourceIdx, targetIdx}, ...]
let nodeIndex = {};   // id → index into nodes[]
let domains = {};     // {key: {color, label}}

// Derived state
let visibleNodeIndices = [];  // indices of nodes currently rendered (render budget + viewport)
let clusterLabels = [];       // [{text, x, y, domain}, ...] — computed from visible clusters
```

### Node Object Shape

```javascript
{
  id: "calculus",
  name: "Calculus",
  year: 1687,
  domains: ["mathematics"],
  desc: "Mathematics of change and motion",
  radius: 28,          // pre-computed from text + connection count
  x: 400, y: 300,      // updated every tick from worker
  color: [255, 138, 101],  // RGB array from domain color
  pinned: false
}
```

### Node Radius Computation

Radius depends on two factors:
1. **Text content** — longer names need bigger circles
2. **Connection count** — more connections → bigger node (font 10–13pt)

```javascript
function computeNodeRadius(name, connectionCount) {
  // Font size: 10pt base, +1pt per 3 connections, max 13pt
  const fontSize = Math.min(13, 10 + Math.floor(connectionCount / 3));
  
  // Wrap text into lines (max ~12 chars per line for readability)
  const lines = wrapText(name, 12);
  
  // Width: longest line * char width estimate
  const charWidth = fontSize * 0.6;
  const maxLineWidth = Math.max(...lines.map(l => l.length)) * charWidth;
  
  // Height: number of lines * line height
  const lineHeight = fontSize * 1.3;
  const textHeight = lines.length * lineHeight;
  
  // Circle must contain the text block — use the diagonal
  const padding = fontSize;
  const w = maxLineWidth + padding;
  const h = textHeight + padding;
  const radius = Math.ceil(Math.sqrt(w * w + h * h) / 2);
  
  return Math.max(15, Math.min(50, radius));
}

function wrapText(text, maxChars) {
  const words = text.split(/\s+/);
  const lines = [];
  let current = "";
  for (const word of words) {
    if (current && (current.length + 1 + word.length) > maxChars) {
      lines.push(current);
      current = word;
    } else {
      current = current ? current + " " + word : word;
    }
  }
  if (current) lines.push(current);
  return lines;
}
```

### Color Parsing

Domain colors come from the server as hex strings (`"#ff8a65"`). Convert to RGB arrays for Deck.gl:

```javascript
function hexToRgb(hex) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return [r, g, b];
}
```

A node's color is determined by its first domain: `domains[node.domains[0]]?.color`. If the domain has no color yet, use a fallback gray `[150, 150, 150]`.

## Deck.gl Setup

### View Configuration

```javascript
const deckgl = new deck.DeckGL({
  container: 'deck-container',
  views: new deck.OrthographicView({
    controller: {
      scrollZoom: {speed: 0.01, smooth: true},
      dragPan: true,
      doubleClickZoom: false,  // we use double-click for unpin
      keyboard: true
    }
  }),
  initialViewState: {
    target: [960, 540, 0],
    zoom: 0,       // log2 scale — 0 = 1:1, -1 = 50%, 1 = 200%
    minZoom: -4,
    maxZoom: 6
  },
  onViewStateChange: ({viewState}) => {
    currentViewState = viewState;
    updateVisibleNodes();
    debouncedAutoNameClusters();
    return viewState;
  },
  getCursor: ({isHovering}) => isHovering ? 'pointer' : 'grab',
  layers: []  // updated in render loop
});
```

### Layer Stack

Layers are rebuilt every frame (Deck.gl's reactive model). Order matters — later layers render on top.

```javascript
function buildLayers() {
  const zoom = currentViewState.zoom;
  const showNodeLabels = zoom > labelZoomThreshold;  // default threshold: -0.5
  const showClusterLabels = zoom < labelZoomThreshold;
  
  const layers = [];
  
  // 1. Edges
  layers.push(new deck.LineLayer({
    id: 'edges',
    data: visibleEdges,
    getSourcePosition: d => [nodes[d.sourceIdx].x, nodes[d.sourceIdx].y],
    getTargetPosition: d => [nodes[d.targetIdx].x, nodes[d.targetIdx].y],
    getColor: d => [255, 255, 255, Math.floor(d.weight * edgeOpacity * 255)],
    getWidth: 1,
    widthMinPixels: 1,
    pickable: false,
    updateTriggers: {
      getSourcePosition: [tickCounter],
      getTargetPosition: [tickCounter]
    }
  }));
  
  // 2. Node circles
  layers.push(new deck.ScatterplotLayer({
    id: 'nodes',
    data: visibleNodes,
    getPosition: d => [d.x, d.y],
    getRadius: d => d.radius,
    getFillColor: d => [...d.color, 200],
    getLineColor: d => d.pinned ? [255, 255, 100, 255] : [...d.color, 255],
    getLineWidth: d => d.pinned ? 3 : 1,
    lineWidthMinPixels: 1,
    radiusUnits: 'common',
    pickable: true,
    autoHighlight: true,
    highlightColor: [255, 255, 255, 40],
    onClick: (info) => handleNodeClick(info),
    onHover: (info) => handleNodeHover(info),
    updateTriggers: {
      getPosition: [tickCounter],
      getLineColor: [pinVersion]
    }
  }));
  
  // 3. Node labels (only when zoomed in enough)
  if (showNodeLabels) {
    layers.push(new deck.TextLayer({
      id: 'node-labels',
      data: visibleNodes,
      getPosition: d => [d.x, d.y],
      getText: d => d.wrappedName,  // pre-computed with \n separators
      getSize: d => d.fontSize,
      getColor: [255, 255, 255, 230],
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      fontWeight: 600,
      sizeUnits: 'common',
      pickable: false,
      updateTriggers: {
        getPosition: [tickCounter]
      }
    }));
  }
  
  // 4. Cluster labels (only when zoomed out)
  if (showClusterLabels && clusterLabels.length > 0) {
    layers.push(new deck.TextLayer({
      id: 'cluster-labels',
      data: clusterLabels,
      getPosition: d => [d.x, d.y],
      getText: d => d.text,
      getSize: 24,
      getColor: d => [...(d.color || [200, 200, 200]), 160],
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      fontWeight: 700,
      sizeUnits: 'common',
      pickable: false
    }));
  }
  
  return layers;
}
```

## Render Budget & Viewport Culling

Not all nodes are rendered — the user can control a "render budget" (default 500).

```javascript
let renderBudget = 500;

function updateVisibleNodes() {
  if (nodes.length <= renderBudget) {
    visibleNodeIndices = nodes.map((_, i) => i);
  } else {
    // Score each node: in-viewport gets a bonus, more connections = higher score
    const viewport = getViewportBounds();  // {minX, maxX, minY, maxY} from currentViewState
    const scored = nodes.map((n, i) => {
      let score = (n.connectionCount || 0);
      if (n.x >= viewport.minX && n.x <= viewport.maxX &&
          n.y >= viewport.minY && n.y <= viewport.maxY) {
        score += 1000;  // strong preference for in-viewport nodes
      }
      return {idx: i, score};
    });
    scored.sort((a, b) => b.score - a.score);
    visibleNodeIndices = scored.slice(0, renderBudget).map(s => s.idx);
  }
  
  visibleNodes = visibleNodeIndices.map(i => nodes[i]);
  visibleEdges = edges.filter(e =>
    visibleNodeIndices.includes(e.sourceIdx) && visibleNodeIndices.includes(e.targetIdx)
  );
  
  updateStatusBar();
}
```

### Viewport Bounds from View State

```javascript
function getViewportBounds() {
  const canvas = document.getElementById('deck-container').querySelector('canvas');
  const w = canvas.width, h = canvas.height;
  const scale = Math.pow(2, currentViewState.zoom);
  const [cx, cy] = currentViewState.target;
  const halfW = (w / 2) / scale;
  const halfH = (h / 2) / scale;
  return {minX: cx - halfW, maxX: cx + halfW, minY: cy - halfH, maxY: cy + halfH};
}
```

## Force Worker Integration

### Initialization

```javascript
const worker = new Worker('force-worker.js');

async function initGraph() {
  const resp = await fetch('/api/graph');
  const data = await resp.json();
  
  domains = data.domains || {};
  
  // Build nodes array
  nodes = data.nodes.map((n, i) => {
    const conns = data.links.filter(l => l.source === n.id || l.target === n.id).length;
    const radius = computeNodeRadius(n.name, conns);
    const domainKey = (n.domains || [])[0];
    const color = domainKey && domains[domainKey]
      ? hexToRgb(domains[domainKey].color)
      : [150, 150, 150];
    const lines = wrapText(n.name, 12);
    return {
      ...n,
      radius,
      x: 0, y: 0,
      color,
      pinned: false,
      connectionCount: conns,
      fontSize: Math.min(13, 10 + Math.floor(conns / 3)),
      wrappedName: lines.join('\n')
    };
  });
  
  // Build node index
  nodeIndex = {};
  nodes.forEach((n, i) => nodeIndex[n.id] = i);
  
  // Build edges with index references
  edges = data.links.map(l => ({
    source: l.source,
    target: l.target,
    weight: l.weight || 0.5,
    sourceIdx: nodeIndex[l.source],
    targetIdx: nodeIndex[l.target]
  })).filter(e => e.sourceIdx !== undefined && e.targetIdx !== undefined);
  
  // Send to worker
  worker.postMessage({
    type: "init",
    nodes: nodes.map(n => ({
      id: n.id, x: n.x, y: n.y, radius: n.radius,
      year: n.year, domain: (n.domains || [])[0] || "unknown"
    })),
    edges: edges.map(e => ({source: e.source, target: e.target, weight: e.weight})),
    params: getCurrentParams(),
    width: window.innerWidth,
    height: window.innerHeight
  });
  
  updateVisibleNodes();
  updateStatusBar();
}
```

### Receiving Position Updates

```javascript
let tickCounter = 0;
let settled = false;

worker.onmessage = function(e) {
  const msg = e.data;
  if (msg.type === "tick") {
    const positions = msg.positions;  // Float32Array [x0,y0, x1,y1, ...]
    for (let i = 0; i < nodes.length && i * 2 + 1 < positions.length; i++) {
      nodes[i].x = positions[i * 2];
      nodes[i].y = positions[i * 2 + 1];
    }
    tickCounter++;
    settled = false;
    requestRender();
  } else if (msg.type === "settled") {
    settled = true;
    updateSettledIndicator();
  }
};

let renderPending = false;
function requestRender() {
  if (!renderPending) {
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      updateVisibleNodes();
      deckgl.setProps({layers: buildLayers()});
      updateFPS();
    });
  }
}
```

## SSE Integration

```javascript
function connectSSE() {
  const source = new EventSource('/api/stream');
  
  source.addEventListener('expand', (e) => {
    const data = JSON.parse(e.data);
    addNodesFromExpansion(data.new_nodes, data.new_edges);
    updateStatsDisplay(data.stats);
    showStatusMessage(`Round ${data.round}: expanded "${data.focal}" (+${data.new_nodes.length} nodes)`);
  });
  
  source.addEventListener('status', (e) => {
    const data = JSON.parse(e.data);
    showStatusMessage(data.message);
  });
  
  source.addEventListener('error', (e) => {
    const data = JSON.parse(e.data);
    showStatusMessage(`Error: ${data.message}`, 'warn');
  });
  
  source.addEventListener('done', (e) => {
    const data = JSON.parse(e.data);
    updateStatsDisplay(data.stats);
    showStatusMessage('Expansion complete.');
    setExpansionRunning(false);
  });
  
  source.addEventListener('stopped', (e) => {
    const data = JSON.parse(e.data);
    updateStatsDisplay(data.stats);
    showStatusMessage('Expansion stopped.');
    setExpansionRunning(false);
  });
  
  source.addEventListener('stats', (e) => {
    const data = JSON.parse(e.data);
    updateStatsDisplay(data);
  });
  
  source.onerror = () => {
    showStatusMessage('SSE connection lost, reconnecting...', 'warn');
  };
}
```

### Adding Nodes from Expansion

```javascript
function addNodesFromExpansion(newNodes, newEdges) {
  const workerNodes = [];
  const workerEdges = [];
  
  for (const n of newNodes) {
    if (nodeIndex[n.id] !== undefined) continue;  // skip duplicates
    
    const conns = newEdges.filter(e => e.source === n.id || e.target === n.id).length;
    const radius = computeNodeRadius(n.name, conns);
    const domainKey = (n.domains || [])[0];
    
    // Auto-add domain if new
    if (domainKey && !domains[domainKey]) {
      domains[domainKey] = {color: generateDomainColor(domainKey), label: domainKey.replace(/_/g, ' ')};
    }
    
    const color = domainKey && domains[domainKey]
      ? hexToRgb(domains[domainKey].color)
      : [150, 150, 150];
    const lines = wrapText(n.name, 12);
    
    const idx = nodes.length;
    const node = {
      ...n,
      radius,
      x: nodes.length > 0 ? nodes[0].x + (Math.random() - 0.5) * 200 : 0,
      y: nodes.length > 0 ? nodes[0].y + (Math.random() - 0.5) * 200 : 0,
      color,
      pinned: false,
      connectionCount: conns,
      fontSize: Math.min(13, 10 + Math.floor(conns / 3)),
      wrappedName: lines.join('\n')
    };
    nodes.push(node);
    nodeIndex[n.id] = idx;
    
    workerNodes.push({
      id: n.id, x: node.x, y: node.y, radius,
      year: n.year, domain: domainKey || "unknown"
    });
  }
  
  for (const e of newEdges) {
    const srcIdx = nodeIndex[e.source];
    const tgtIdx = nodeIndex[e.target];
    if (srcIdx === undefined || tgtIdx === undefined) continue;
    
    edges.push({
      source: e.source, target: e.target,
      weight: e.weight || 0.5,
      sourceIdx: srcIdx, targetIdx: tgtIdx
    });
    
    workerEdges.push({source: e.source, target: e.target, weight: e.weight || 0.5});
  }
  
  if (workerNodes.length > 0 || workerEdges.length > 0) {
    worker.postMessage({type: "add", nodes: workerNodes, edges: workerEdges});
  }
}
```

## Interactions

### Hover Tooltip

```javascript
function handleNodeHover(info) {
  const tooltip = document.getElementById('tooltip');
  if (info.object) {
    const n = info.object;
    tooltip.innerHTML = `
      <div class="tt-name">${n.name}</div>
      <div class="tt-year">${n.year > 0 ? n.year + ' AD' : Math.abs(n.year) + ' BC'}</div>
      <div class="tt-domain">${(n.domains || []).join(' • ')}</div>
      ${n.desc ? `<div class="tt-desc">${n.desc}</div>` : ''}
    `;
    tooltip.style.left = (info.x + 16) + 'px';
    tooltip.style.top = (info.y + 16) + 'px';
    tooltip.style.display = 'block';
  } else {
    tooltip.style.display = 'none';
  }
}
```

### Click to Pin / Double-Click to Unpin

```javascript
let lastClickTime = 0;
let lastClickId = null;

function handleNodeClick(info) {
  if (!info.object) return;
  const node = info.object;
  const now = Date.now();
  
  if (lastClickId === node.id && now - lastClickTime < 400) {
    // Double-click → unpin
    node.pinned = false;
    worker.postMessage({type: "unpin", id: node.id});
    pinVersion++;
    lastClickId = null;
  } else {
    // Single click → pin at current position
    node.pinned = true;
    worker.postMessage({type: "pin", id: node.id, x: node.x, y: node.y});
    pinVersion++;
  }
  
  lastClickTime = now;
  lastClickId = node.id;
}
```

### Drag to Pin

Deck.gl doesn't have built-in drag for scatter points. Implement via pointer events on the container:

```javascript
let dragNode = null;
let isDragging = false;

// On the DeckGL onDragStart / onDrag / onDragEnd callbacks:
// 1. Pick node under cursor using deck.pickObject
// 2. If node found, set dragNode, disable pan, send pin messages on each drag move
// 3. On drag end, re-enable pan

// Alternative simpler approach: use onClick for pin, double-click for unpin (as above).
// Drag-to-move can be omitted in v1 — the pin/unpin via click is sufficient.
```

**Implementation note:** For v1, use click-to-pin and double-click-to-unpin. Full drag-to-move can be added later since it requires intercepting Deck.gl's controller events.

## Cluster Labels

### Domain-Based Clusters (Default)

Compute cluster centroids from visible nodes grouped by their primary domain:

```javascript
function computeClusterLabels() {
  const groups = {};
  for (const n of visibleNodes) {
    const d = (n.domains || [])[0];
    if (!d) continue;
    if (!groups[d]) groups[d] = {sumX: 0, sumY: 0, count: 0, domain: d};
    groups[d].sumX += n.x;
    groups[d].sumY += n.y;
    groups[d].count++;
  }
  
  clusterLabels = Object.values(groups)
    .filter(g => g.count >= 3)  // only label clusters with 3+ visible nodes
    .map(g => ({
      text: domains[g.domain]?.label || g.domain.replace(/_/g, ' '),
      x: g.sumX / g.count,
      y: g.sumY / g.count,
      color: domains[g.domain] ? hexToRgb(domains[g.domain].color) : [200, 200, 200]
    }));
}
```

### LLM-Named Clusters (On-Demand)

When the user clicks "Name Clusters", gather visible node names by spatial proximity (using a simple grid-based grouping or by domain), send to the server, and display returned names:

```javascript
async function nameClustersByLLM() {
  const groups = {};
  for (const n of visibleNodes) {
    const d = (n.domains || [])[0] || 'unknown';
    if (!groups[d]) groups[d] = {names: []};
    groups[d].names.push(n.name);
  }
  
  const clusterList = Object.values(groups).filter(g => g.names.length >= 3);
  if (clusterList.length === 0) return;
  
  const backend = document.getElementById('backend-select').value;
  const resp = await fetch('/api/cluster-names', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({clusters: clusterList, backend})
  });
  
  if (resp.ok) {
    const data = await resp.json();
    // Replace cluster label texts with LLM-generated names
    const domainKeys = Object.keys(groups).filter(d => (groups[d].names || []).length >= 3);
    for (let i = 0; i < Math.min(data.names.length, clusterLabels.length); i++) {
      clusterLabels[i].text = data.names[i];
    }
    requestRender();
  }
}
```

### Auto-Naming (Toggle)

When enabled, cluster naming triggers automatically 3 seconds after the user stops panning/zooming:

```javascript
let autoNameEnabled = false;
let autoNameTimer = null;

function debouncedAutoNameClusters() {
  if (!autoNameEnabled) return;
  clearTimeout(autoNameTimer);
  autoNameTimer = setTimeout(() => nameClustersByLLM(), 3000);
}
```

## Search

```javascript
let searchQuery = '';
let searchResults = new Set();

function handleSearch(query) {
  searchQuery = query.toLowerCase().trim();
  if (!searchQuery) {
    searchResults.clear();
    requestRender();
    return;
  }
  searchResults = new Set(
    nodes
      .filter(n => n.name.toLowerCase().includes(searchQuery) || n.id.includes(searchQuery))
      .map(n => n.id)
  );
  requestRender();
}
```

When `searchResults` is non-empty, modify the ScatterplotLayer to highlight matching nodes:

```javascript
// In buildLayers(), for the ScatterplotLayer:
getFillColor: d => {
  if (searchResults.size > 0 && !searchResults.has(d.id)) {
    return [...d.color, 40];  // dim non-matching
  }
  return [...d.color, 200];
}
```

## UI Layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│  [Search: ___________]     Showing 500 / 12,340 concepts • 34,500 edges  │
│                                                                            │
│                                                                            │
│              ┌──────────────────────────────────────────┐                   │
│              │                                          │                   │
│              │           Deck.gl Canvas                 │   ┌────────────┐ │
│              │        (full viewport)                   │   │  Physics   │ │
│              │                                          │   │  7 sliders │ │
│              │                                          │   │            │ │
│              │                                          │   │  Display   │ │
│              │                                          │   │  3 sliders │ │
│              │                                          │   │            │ │
│              │                                          │   │  Expansion │ │
│              │                                          │   │  seed+run  │ │
│              │                                          │   │            │ │
│              │                                          │   │  Clusters  │ │
│              └──────────────────────────────────────────┘   └────────────┘ │
│                                                                            │
│  Scroll to zoom • Drag to pan • Click to pin • Double-click to unpin      │
└────────────────────────────────────────────────────────────────────────────┘
```

### HTML Structure

```html
<body>
  <div id="deck-container"></div>
  <div id="tooltip"></div>
  
  <div id="search">
    <input type="text" placeholder="Search concepts..." id="search-input">
  </div>
  
  <div id="status-bar">
    <span id="status-text">Showing 0 / 0 concepts • 0 edges</span>
    <span id="fps-counter">FPS: --</span>
    <span id="settled-indicator"></span>
  </div>
  
  <div id="controls">
    <h2>Physics</h2>
    <!-- 7 sliders: charge, linkDist, linkStr, timePull, clusterGravity, velDecay, collision -->
    <!-- Each slider: label + value display + range input -->
    <!-- Ranges match Chunk 4 table: charge -400..-10, linkDist 20-300, etc. -->
    
    <div class="separator"></div>
    <h2>Display</h2>
    <!-- Render Budget: 50-5000, default 500 -->
    <!-- Label Zoom Threshold: -2 to 2, default -0.5 -->
    <!-- Edge Opacity: 0 to 1, default 0.15 -->
    
    <div class="separator"></div>
    <h2>Expansion</h2>
    <!-- Seed input: text field -->
    <!-- Backend: dropdown with 5 options -->
    <!-- Rounds: number input (0 = unlimited) -->
    <!-- Start/Stop button -->
    <!-- Status message area -->
    
    <div class="separator"></div>
    <h2>Clusters</h2>
    <!-- "Name Clusters" button -->
    <!-- "Auto-name" toggle checkbox -->
    
    <div class="separator"></div>
    <h2>Import</h2>
    <!-- "Import JSON" file input / paste area -->
  </div>
  
  <div id="help-text">
    Scroll to zoom &middot; Drag to pan &middot; Click to pin &middot; Double-click to unpin
  </div>
</body>
```

### CSS Requirements

- **Dark theme** — background `#0a0a0f`, text `#ccc`, accent `#6af`
- **Controls panel** — fixed, top-right, 280px wide, scrollable, semi-transparent with backdrop blur
- **Tooltip** — fixed position, follows mouse, semi-transparent dark background
- **Status bar** — fixed, top-center or bottom
- **Search** — fixed, top-left
- **Help text** — fixed, bottom-center, subtle
- **Deck container** — absolute, fills viewport (`inset: 0`)
- Match the existing UI style from the original `index.html` (dark glassmorphism look)

### Physics Sliders

Each slider sends a `params` message to the worker when changed:

```javascript
function setupSliders() {
  const sliders = {
    charge:     {id: 'charge',     param: 'charge',              min: -400, max: -10,  step: 5,    default: -120},
    linkDist:   {id: 'linkdist',   param: 'linkDistance',        min: 20,   max: 300,  step: 5,    default: 80},
    linkStr:    {id: 'linkstr',    param: 'linkStrength',        min: 0.01, max: 1,    step: 0.01, default: 0.3},
    timePull:   {id: 'timepull',   param: 'timePull',            min: 0,    max: 0.5,  step: 0.01, default: 0.15},
    cluster:    {id: 'cluster',    param: 'clusterGravity',      min: 0,    max: 0.3,  step: 0.01, default: 0.08},
    decay:      {id: 'decay',      param: 'velocityDecay',       min: 0.1,  max: 0.9,  step: 0.02, default: 0.4},
    collision:  {id: 'collision',  param: 'collisionMultiplier',  min: 0.5,  max: 3,    step: 0.1,  default: 1.2},
  };
  
  for (const [key, cfg] of Object.entries(sliders)) {
    const el = document.getElementById(cfg.id);
    const val = document.getElementById('v-' + cfg.id);
    el.addEventListener('input', () => {
      val.textContent = el.value;
      worker.postMessage({type: "params", [cfg.param]: parseFloat(el.value)});
    });
  }
}
```

### Expansion Controls

```javascript
let expansionRunning = false;

async function startExpansion() {
  const seed = document.getElementById('seed-input').value.trim();
  const backend = document.getElementById('backend-select').value;
  const rounds = parseInt(document.getElementById('rounds-input').value) || 0;
  
  if (!seed) { showStatusMessage('Enter a seed concept', 'warn'); return; }
  
  const resp = await fetch('/api/expand', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({seed, backend, rounds})
  });
  
  const data = await resp.json();
  if (resp.ok) {
    setExpansionRunning(true);
    showStatusMessage(`Expanding from "${seed}" using ${backend}...`);
  } else {
    showStatusMessage(`Error: ${data.error}`, 'error');
  }
}

async function stopExpansion() {
  await fetch('/api/stop', {method: 'POST'});
  showStatusMessage('Stopping...');
}

function setExpansionRunning(running) {
  expansionRunning = running;
  document.getElementById('expand-btn').textContent = running ? 'Stop' : 'Start';
  document.getElementById('expand-btn').onclick = running ? stopExpansion : startExpansion;
}
```

### Backend Dropdown

Populated from `/api/backends` on load:

```javascript
async function loadBackends() {
  const resp = await fetch('/api/backends');
  const data = await resp.json();
  const select = document.getElementById('backend-select');
  for (const [name, info] of Object.entries(data)) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = `${name} (${info.model})`;
    opt.disabled = !info.configured;
    if (name === 'openai' && info.configured) opt.selected = true;
    select.appendChild(opt);
  }
}
```

### Import JSON

```javascript
async function importJSON() {
  const input = document.getElementById('import-file');
  if (!input.files.length) return;
  
  const text = await input.files[0].text();
  const data = JSON.parse(text);
  
  const resp = await fetch('/api/import', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  
  if (resp.ok) {
    const result = await resp.json();
    showStatusMessage(`Imported ${result.nodes} nodes, ${result.edges} edges`);
    // Reload entire graph since import may add many nodes
    await initGraph();
  }
}
```

### FPS Counter

```javascript
let frameCount = 0;
let lastFPSTime = performance.now();
let currentFPS = 0;

function updateFPS() {
  frameCount++;
  const now = performance.now();
  if (now - lastFPSTime >= 1000) {
    currentFPS = Math.round(frameCount * 1000 / (now - lastFPSTime));
    document.getElementById('fps-counter').textContent = `FPS: ${currentFPS}`;
    frameCount = 0;
    lastFPSTime = now;
  }
}
```

### Status Bar

```javascript
function updateStatusBar() {
  const total = nodes.length;
  const visible = visibleNodes.length;
  const edgeCount = edges.length;
  document.getElementById('status-text').textContent =
    `Showing ${visible} / ${total} concepts • ${edgeCount} edges`;
}

function updateSettledIndicator() {
  document.getElementById('settled-indicator').textContent = settled ? '• Settled' : '';
}
```

## Initialization Sequence

```javascript
document.addEventListener('DOMContentLoaded', async () => {
  setupSliders();
  await loadBackends();
  await initGraph();
  connectSSE();
  
  // Search
  document.getElementById('search-input').addEventListener('input', (e) => {
    handleSearch(e.target.value);
  });
  
  // Compute initial cluster labels
  computeClusterLabels();
});
```

## FPS Optimization Notes

- **Transferable objects**: Worker sends `Float32Array` via transferable (zero-copy).
- **Layer update triggers**: Use `updateTriggers` with `tickCounter` so Deck.gl knows when data actually changed.
- **Minimize allocations**: Reuse position arrays where possible.
- **Viewport culling**: Only render nodes within the viewport + a small margin.
- **Render budget**: Configurable cap prevents GPU overload at large scales.
- **requestAnimationFrame**: Coalesce multiple ticks into a single render if they arrive faster than 60fps.
- **Avoid re-sorting**: `visibleNodeIndices` only recalculated when viewport changes or nodes are added.

## Generating Domain Colors Client-Side

When new nodes arrive via SSE with domains not yet in the `domains` dict, generate colors client-side (matching the server's algorithm):

```javascript
function generateDomainColor(key) {
  // Simple hash → hue (matches server's MD5-based approach approximately)
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash) + key.charCodeAt(i);
    hash |= 0;
  }
  const hue = Math.abs(hash) % 360;
  // HSL to hex with s=65%, l=60%
  return hslToHex(hue, 65, 60);
}

function hslToHex(h, s, l) {
  s /= 100; l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    const color = l - a * Math.max(Math.min(k - 3, 9 - k, 1), -1);
    return Math.round(255 * color).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}
```

**Note:** The client-side hash won't match the server's MD5-based color exactly, but that's fine — the authoritative colors come from `/api/graph`. The client fallback is only used for brand-new domains arriving via SSE before a full refresh.

## Acceptance Criteria (Manual Checklist)

Open `http://localhost:8080` with the server running. Verify:

1. **Empty state** — page loads without errors, shows expansion panel, status bar says "0 / 0 concepts"
2. **Import seed data** — use Import JSON to load `seed_data.json` → nodes appear with physics animation
3. **Zoom in** — text is readable inside circles at 10pt+, labels appear when zoomed past threshold
4. **Zoom out** — cluster labels appear, individual node labels disappear
5. **Hover** — tooltip appears with name, year, domain(s), description
6. **Pin/Unpin** — click a node to pin it (yellow border), double-click to unpin
7. **Search** — type "calc" → matching nodes highlighted, non-matching dimmed
8. **Physics sliders** — each slider visibly changes the simulation behavior
9. **Render budget** — changing the slider updates "Showing X / Y" and changes visible node count
10. **SSE expansion** — enter a seed word, click Start → new nodes appear in real time as LLM generates them
11. **Cluster naming** — click "Name Clusters" → labels update to LLM-generated names
12. **Auto-name toggle** — enable, pan/zoom, labels update after 3s debounce
13. **FPS** — stays above 30 with 500 visible nodes
14. **Persistence** — restart the server, reload page → graph is still there from DB

## Implementation Notes

- **Single file**: All HTML, CSS, and JS go into `index.html`. No build step.
- **Deck.gl container**: Create `<div id="deck-container">` with absolute positioning filling the viewport. Deck.gl creates its canvas inside this div.
- **Dark theme**: Match the existing prototype's visual style (dark background, glassmorphism panels, blue accent color).
- **Error handling**: Show user-visible errors in the status message area, not just console.
- **Responsive**: The Deck.gl canvas auto-resizes. Controls panel scrolls if viewport is small.
- **No dependencies beyond CDN**: Everything loads from unpkg/jsdelivr CDN.
