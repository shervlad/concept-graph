importScripts("https://cdn.jsdelivr.net/npm/d3-dispatch@3/dist/d3-dispatch.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-quadtree@3/dist/d3-quadtree.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-octree@1/dist/d3-octree.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-binarytree@1/dist/d3-binarytree.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-timer@3/dist/d3-timer.min.js");
importScripts("https://cdn.jsdelivr.net/npm/d3-force-3d@3/dist/d3-force-3d.min.js");

let simulation = null;
let nodes = [];
let edges = [];
let nodeIndex = {};
let params = {
  charge: -250,
  linkDistance: 120,
  linkStrength: 0.2,
  timePull: 0.1,
  clusterGravity: 0.05,
  velocityDecay: 0.4,
  collisionMultiplier: 1.5
};
let width = 1920, height = 1080;
let domainSet = [];
let domainYMap = new Map();
let waitingForAck = false;
let lastSendTime = 0;
let pendingPositions = null;

const MIN_YEAR = -8000, MAX_YEAR = 2025;
const ACK_TIMEOUT = 100;

function yearToX(node) {
  const t = (node.year - MIN_YEAR) / (MAX_YEAR - MIN_YEAR);
  return 100 + t * (width - 200);
}

function rebuildDomainSet() {
  domainSet = [...new Set(nodes.map(n => n.domain))].sort();
  domainYMap.clear();
  for (let i = 0; i < domainSet.length; i++) {
    domainYMap.set(domainSet[i], ((i + 0.5) / domainSet.length) * height);
  }
}

function domainToY(node) {
  return domainYMap.get(node.domain) || height / 2;
}

function rebuildNodeIndex() {
  nodeIndex = {};
  for (let i = 0; i < nodes.length; i++) {
    nodeIndex[nodes[i].id] = i;
  }
}

function setupSimulation() {
  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(nodes, 3)
    .force("charge", d3.forceManyBody().strength(params.charge))
    .force("link", d3.forceLink(edges).id(d => d.id)
      .distance(params.linkDistance).strength(params.linkStrength))
    .force("x", d3.forceX(n => yearToX(n)).strength(params.timePull))
    .force("y", d3.forceY(0).strength(0.01))
    .force("z", d3.forceZ(n => domainToY(n)).strength(params.clusterGravity))
    .force("collide", d3.forceCollide().radius(n => n.radius * params.collisionMultiplier))
    .velocityDecay(params.velocityDecay)
    .on("tick", onTick)
    .on("end", () => postMessage({ type: "settled" }));
}

function onTick() {
  const positions = new Float32Array(nodes.length * 3);
  for (let i = 0; i < nodes.length; i++) {
    positions[i * 3] = nodes[i].x;
    positions[i * 3 + 1] = nodes[i].y;
    positions[i * 3 + 2] = nodes[i].z || 0;
  }

  const now = performance.now();
  if (waitingForAck && (now - lastSendTime) < ACK_TIMEOUT) {
    pendingPositions = positions;
    return;
  }

  postMessage({ type: "tick", positions }, [positions.buffer]);
  waitingForAck = true;
  lastSendTime = now;
  pendingPositions = null;
}

function handleInit(msg) {
  nodes = msg.nodes;
  edges = msg.edges;
  if (msg.params) Object.assign(params, msg.params);
  if (msg.width) width = msg.width;
  if (msg.height) height = msg.height;
  rebuildNodeIndex();
  rebuildDomainSet();
  setupSimulation();
}

function handleAdd(msg) {
  if (msg.nodes) {
    for (const n of msg.nodes) {
      nodes.push(n);
    }
  }
  if (msg.edges) {
    for (const e of msg.edges) {
      edges.push(e);
    }
  }
  rebuildNodeIndex();
  rebuildDomainSet();

  simulation.nodes(nodes);
  simulation.force("link").links(edges);
  simulation.force("x", d3.forceX(n => yearToX(n)).strength(params.timePull));
  simulation.force("z", d3.forceZ(n => domainToY(n)).strength(params.clusterGravity));
  simulation.force("collide").radius(n => n.radius * params.collisionMultiplier);
  simulation.alpha(0.3).restart();
}

function handleRemove(msg) {
  const removeSet = new Set(msg.nodeIds);
  nodes = nodes.filter(n => !removeSet.has(n.id));
  edges = edges.filter(e => {
    const sid = typeof e.source === "object" ? e.source.id : e.source;
    const tid = typeof e.target === "object" ? e.target.id : e.target;
    return !removeSet.has(sid) && !removeSet.has(tid);
  });
  rebuildNodeIndex();
  rebuildDomainSet();

  simulation.nodes(nodes);
  simulation.force("link").links(edges);
  simulation.force("x", d3.forceX(n => yearToX(n)).strength(params.timePull));
  simulation.force("z", d3.forceZ(n => domainToY(n)).strength(params.clusterGravity));
  simulation.force("collide").radius(n => n.radius * params.collisionMultiplier);
  simulation.alpha(0.3).restart();
}

function handleParams(msg) {
  if (msg.charge !== undefined) {
    params.charge = msg.charge;
    simulation.force("charge").strength(msg.charge);
  }
  if (msg.linkDistance !== undefined) {
    params.linkDistance = msg.linkDistance;
    simulation.force("link").distance(msg.linkDistance);
  }
  if (msg.linkStrength !== undefined) {
    params.linkStrength = msg.linkStrength;
    simulation.force("link").strength(msg.linkStrength);
  }
  if (msg.timePull !== undefined) {
    params.timePull = msg.timePull;
    simulation.force("x").strength(msg.timePull);
  }
  if (msg.clusterGravity !== undefined) {
    params.clusterGravity = msg.clusterGravity;
    simulation.force("z").strength(msg.clusterGravity);
  }
  if (msg.velocityDecay !== undefined) {
    params.velocityDecay = msg.velocityDecay;
    simulation.velocityDecay(msg.velocityDecay);
  }
  if (msg.collisionMultiplier !== undefined) {
    params.collisionMultiplier = msg.collisionMultiplier;
    simulation.force("collide").radius(n => n.radius * msg.collisionMultiplier);
  }
  simulation.alpha(0.5).restart();
}

function handlePin(msg) {
  const idx = nodeIndex[msg.id];
  if (idx !== undefined) {
    nodes[idx].fx = msg.x;
    nodes[idx].fy = msg.y;
    nodes[idx].fz = msg.z;
    simulation.alpha(0.1).restart();
  }
}

function handleUnpin(msg) {
  const idx = nodeIndex[msg.id];
  if (idx !== undefined) {
    nodes[idx].fx = null;
    nodes[idx].fy = null;
    nodes[idx].fz = null;
    simulation.alpha(0.1).restart();
  }
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
    case "resize":
      if (msg.width) width = msg.width;
      if (msg.height) height = msg.height;
      if (simulation) {
        rebuildDomainSet();
        simulation.force("x", d3.forceX(n => yearToX(n)).strength(params.timePull));
        simulation.force("z", d3.forceZ(n => domainToY(n)).strength(params.clusterGravity));
        simulation.alpha(0.3).restart();
      }
      break;
    case "reheat": if (simulation) simulation.alpha(1).restart(); break;
    case "frame":
      waitingForAck = false;
      if (pendingPositions) {
        const p = pendingPositions;
        pendingPositions = null;
        postMessage({ type: "tick", positions: p }, [p.buffer]);
        waitingForAck = true;
        lastSendTime = performance.now();
      }
      break;
  }
};
