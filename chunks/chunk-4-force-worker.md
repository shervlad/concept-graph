# Chunk 4: Force Simulation Worker

## Project Context

We're building "Knowledge Tree" — a web app that visualizes human knowledge as a force-directed graph. An LLM expands the graph from a seed word, the browser renders it in real time with WebGL (Deck.gl).

This chunk implements the **d3-force simulation running in a Web Worker**. The simulation computes node positions off the main thread, sending position updates back for Deck.gl to render at 60fps.

**Working directory:** `~/projects/knowledge-tree/`

**No server-side dependencies.** This chunk is pure client-side JavaScript.

## Files to Create

- `force-worker.js` — the Web Worker running d3-force
- `test-force-worker.html` — standalone visual test page (open via `file://`, no server needed)

## Why a Web Worker?

The force simulation is CPU-intensive. At 10K+ nodes with Barnes-Hut charge computation, each tick takes several milliseconds. Running this on the main thread would cause frame drops. The Worker decouples simulation from rendering.

## d3-force CDN

Load d3-force in the worker via `importScripts`:

```javascript
importScripts("https://cdn.jsdelivr.net/npm/d3-dispatch@3/dist/d3-dispatch.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-quadtree@3/dist/d3-quadtree.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-timer@3/dist/d3-timer.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-force@3/dist/d3-force.min.js");
```

This gives access to `d3.forceSimulation`, `d3.forceManyBody`, `d3.forceLink`, `d3.forceX`, `d3.forceY`, `d3.forceCollide`, etc.

## Message Protocol

### Main Thread → Worker

#### `init` — Initialize simulation with full graph

```javascript
{
  type: "init",
  nodes: [
    {id: "calculus", x: 400, y: 300, radius: 30, year: 1687, domain: "mathematics"},
    {id: "algebra",  x: 350, y: 280, radius: 25, year: 820,  domain: "mathematics"},
    ...
  ],
  edges: [
    {source: "algebra", target: "calculus", weight: 1.0},
    ...
  ],
  params: {
    charge: -120,
    linkDistance: 80,
    linkStrength: 0.3,
    timePull: 0.15,
    clusterGravity: 0.08,
    velocityDecay: 0.4,
    collisionMultiplier: 1.2
  },
  width: 1920,    // viewport width for time-axis scaling
  height: 1080    // viewport height for domain-axis scaling
}
```

- `x`, `y`: initial positions (optional — if omitted, random)
- `radius`: precomputed circle radius for this node (used for collision)
- `year`: used for time-axis X positioning (forceX target)
- `domain`: primary domain string, used for cluster Y positioning (forceY target)

#### `add` — Add nodes/edges incrementally (from SSE)

```javascript
{
  type: "add",
  nodes: [{id, x, y, radius, year, domain}, ...],
  edges: [{source, target, weight}, ...]
}
```

New nodes/edges are added to the running simulation without restarting it. The simulation reheats slightly (alpha bumped to 0.3).

#### `remove` — Remove nodes

```javascript
{
  type: "remove",
  nodeIds: ["node_id_1", "node_id_2"]
}
```

Removes nodes and any edges connected to them.

#### `params` — Update physics parameters

```javascript
{
  type: "params",
  charge: -200,         // optional, only include changed params
  linkDistance: 100,
  linkStrength: 0.5,
  timePull: 0.2,
  clusterGravity: 0.1,
  velocityDecay: 0.3,
  collisionMultiplier: 1.5
}
```

Applied immediately. Reheats simulation (alpha = 0.5).

#### `pin` — Fix a node's position

```javascript
{type: "pin", id: "calculus", x: 500, y: 300}
```

Sets `node.fx = x, node.fy = y` in d3-force (pins the node).

#### `unpin` — Release a pinned node

```javascript
{type: "unpin", id: "calculus"}
```

Sets `node.fx = null, node.fy = null`.

#### `reheat` — Restart simulation

```javascript
{type: "reheat"}
```

Sets `simulation.alpha(1).restart()`.

### Worker → Main Thread

#### `tick` — Position update

```javascript
{
  type: "tick",
  positions: Float32Array  // [x0, y0, x1, y1, x2, y2, ...] in same order as init nodes
}
```

Sent every tick (or every Nth tick if throttling is needed). Uses `Float32Array` for efficient transfer.

**Node order:** Positions correspond to nodes in the order they were added. When `add` messages arrive, new node positions are appended to the end. The main thread must maintain a parallel array mapping index → node ID.

#### `settled` — Simulation cooled down

```javascript
{type: "settled"}
```

Sent once when `alpha < 0.001` (simulation is essentially static). The main thread can stop requesting animation frames until the next change.

## Physics Parameter Mapping

| UI Slider | d3-force Call | Notes |
|-----------|--------------|-------|
| Charge (-400 to -10) | `forceManyBody().strength(value)` | Uses Barnes-Hut (theta=0.9), O(n log n) |
| Link Distance (20–300) | `forceLink().distance(value)` | |
| Link Strength (0.01–1.0) | `forceLink().strength(value)` | |
| Time Pull (0–0.5) | `forceX(yearToX).strength(value)` | Per-node target X based on year |
| Cluster Gravity (0–0.3) | `forceY(domainToY).strength(value)` | Per-node target Y based on domain |
| Velocity Decay (0.1–0.9) | `simulation.velocityDecay(value)` | |
| Collision Multiplier (0.5–3.0) | `forceCollide().radius(n => n.radius * value)` | Variable per node |

### Time-axis Mapping

```javascript
function yearToX(node) {
  const MIN_YEAR = -8000, MAX_YEAR = 2025;
  const t = (node.year - MIN_YEAR) / (MAX_YEAR - MIN_YEAR);
  return 100 + t * (width - 200);
}
```

### Domain-axis Mapping

```javascript
// Build a domain → Y mapping based on unique domains seen
const domainSet = [...new Set(nodes.map(n => n.domain))].sort();
function domainToY(node) {
  const idx = domainSet.indexOf(node.domain);
  return ((idx + 0.5) / domainSet.length) * height;
}
```

When new nodes arrive with new domains, rebuild `domainSet` and update forceY targets.

## Worker Implementation Outline

```javascript
// force-worker.js
importScripts(...d3 CDN URLs...);

let simulation = null;
let nodes = [];
let edges = [];
let nodeIndex = {};  // id → array index
let params = { charge: -120, linkDistance: 80, ... };
let width = 1920, height = 1080;

function setupSimulation() {
  simulation = d3.forceSimulation(nodes)
    .force("charge", d3.forceManyBody().strength(params.charge))
    .force("link", d3.forceLink(edges).id(d => d.id)
      .distance(params.linkDistance).strength(params.linkStrength))
    .force("x", d3.forceX(n => yearToX(n)).strength(params.timePull))
    .force("y", d3.forceY(n => domainToY(n)).strength(params.clusterGravity))
    .force("collide", d3.forceCollide().radius(n => n.radius * params.collisionMultiplier))
    .velocityDecay(params.velocityDecay)
    .on("tick", onTick)
    .on("end", () => postMessage({type: "settled"}));
}

function onTick() {
  const positions = new Float32Array(nodes.length * 2);
  for (let i = 0; i < nodes.length; i++) {
    positions[i * 2] = nodes[i].x;
    positions[i * 2 + 1] = nodes[i].y;
  }
  postMessage({type: "tick", positions}, [positions.buffer]);
  // ^ Transferable for zero-copy
}

self.onmessage = function(e) {
  const msg = e.data;
  switch (msg.type) {
    case "init": handleInit(msg); break;
    case "add": handleAdd(msg); break;
    case "remove": handleRemove(msg); break;
    case "params": handleParams(msg); break;
    case "pin": handlePin(msg); break;
    case "unpin": handleUnpin(msg); break;
    case "reheat": simulation?.alpha(1).restart(); break;
  }
};
```

**Key implementation details for `handleAdd`:**
- Append new nodes to the `nodes` array
- Append new edges to the `edges` array
- Update `nodeIndex`
- Call `simulation.nodes(nodes)` and `simulation.force("link").links(edges)` to update
- Bump alpha to 0.3 with `simulation.alpha(0.3).restart()`

**Key implementation details for `handleParams`:**
- For each param in the message, update the corresponding force
- Bump alpha to 0.5

## Test Page (`test-force-worker.html`)

A standalone HTML file that tests the worker visually. **No server required** — can be opened directly as `file:///path/to/test-force-worker.html`.

### UI Layout

```
┌────────────────────────────────────────────────────────────────┐
│  [FPS: 60]  [Nodes: 200]  [Settled: No]                       │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                                                          │  │
│  │              (Canvas: circles drawn at positions          │  │
│  │               from the worker, colored by domain)        │  │
│  │                                                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  Charge:     [========●========] -120                          │
│  Link Dist:  [====●============] 80                            │
│  Link Str:   [===●=============] 0.3                           │
│  Time Pull:  [==●==============] 0.15                          │
│  Cluster G:  [=●===============] 0.08                          │
│  Vel Decay:  [=====●===========] 0.4                           │
│  Collision:  [===●=============] 1.2                           │
│                                                                │
│  [Add 50 Nodes]  [Reheat]  [Pin Random]  [Unpin All]          │
└────────────────────────────────────────────────────────────────┘
```

### Test Page Features

1. **Canvas rendering**: Plain Canvas 2D (no Deck.gl). Draws circles at positions received from worker. Colors by domain. Includes simple pan/zoom.

2. **FPS counter**: Shows actual frames-per-second, updated every second.

3. **Node counter**: Shows current node count.

4. **Settled indicator**: Shows whether the simulation has settled.

5. **Physics sliders**: All 7 parameters, matching the ranges in the table above. Changing a slider sends a `params` message to the worker immediately.

6. **"Add 50 Nodes" button**: Generates 50 random nodes with random edges to existing nodes. Sends an `add` message. Useful for testing incremental addition.

7. **"Reheat" button**: Sends a `reheat` message.

8. **"Pin Random" button**: Picks a random node, sends a `pin` message fixing it at its current position.

9. **"Unpin All" button**: Sends `unpin` for all currently pinned nodes.

### Initial data

Generate 200 nodes on load:
- IDs: `n0` through `n199`
- Domains: randomly pick from `["math", "physics", "biology", "computing", "philosophy"]`
- Years: random between -500 and 2020
- Radius: random between 15 and 40
- 300 random edges between nodes (weight random 0.2–1.0)

## Acceptance Criteria

Open `test-force-worker.html` in a browser. Verify:

1. **Nodes appear and converge** — circles move from random positions toward a stable layout
2. **FPS stays above 30** with 200 nodes
3. **Sliders work** — changing charge makes nodes repel more/less, etc.
4. **"Add 50 Nodes"** — new circles appear and integrate into the layout smoothly
5. **After adding nodes 3 times (350 total)**, FPS is still above 20
6. **Pin** — pinned node stays fixed while others move around it
7. **Unpin** — node starts moving again
8. **Reheat** — layout re-animates after settling
9. **No console errors**

Optional stretch: try with 2000 nodes (click "Add 50" many times). Should still work, though FPS may drop.
