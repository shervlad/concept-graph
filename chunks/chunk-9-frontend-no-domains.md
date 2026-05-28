# Chunk 9: Frontend — Remove Domains, Add Year Coloring & Count Input

## Goal

Update the frontend to work without domains: replace domain-based node coloring with year-based coloring, add "Concepts per round" config input, and clean up domain UI remnants.

**Prerequisite:** Chunks 7–8

## Changes

### `index.html`

**Remove:**
- `let domains = {};` global variable
- All `domains[domainKey]` lookups for coloring
- `tt-domain` CSS class and tooltip domain line (`n.domains.join(' • ')`)
- Domain-based color assignment in `updateGraph()` and SSE `expand` handler

**Replace node coloring** with year-based hue mapping:
```javascript
function yearToColor(year) {
  // Map year range (-8000 to 2030) to hue (0-300)
  // Ancient = warm (red/orange), Modern = cool (blue/purple)
  const minYear = -8000, maxYear = 2030;
  const t = Math.max(0, Math.min(1, (year - minYear) / (maxYear - minYear)));
  const hue = (1 - t) * 0 + t * 270; // red(0) → purple(270)
  return hslToRgb(hue, 0.65, 0.55);
}
```

Use `yearToColor(node.year)` everywhere domain color was used (`getNodeColor`, `updateGraph`, SSE expand handler).

**Add "Concepts per round" input** between "Rounds" and "Start" button:
```html
<div class="control-group">
  <label>Concepts per round</label>
  <input type="number" id="count-input" value="20" min="1" max="100">
</div>
```

**Update `startExpansion()`:**
```javascript
const count = parseInt(document.getElementById('count-input').value) || 20;
body: JSON.stringify({seed, backend, rounds, count})
```

**Update tooltip:**
- Remove domain line
- Keep: name, year, desc

**Update cluster label fallback:**
- Remove domain-based grouping. Cluster labels come from LLM naming or are numbered.
- In the cluster centroid calculation, group by the existing cluster assignment (from force worker), not by domain.

## Verification

```bash
cd ~/projects/knowledge-tree
python server.py &
curl -X POST http://localhost:8080/api/import -H "Content-Type: application/json" -d @seed_data.json
# Open http://localhost:8080
# Verify:
# 1. Nodes are colored by year (warm=ancient, cool=modern)
# 2. Tooltip shows name + year + desc, no domain
# 3. "Concepts per round" input appears in sidebar, defaults to 20
# 4. Expansion works with the count parameter
```
