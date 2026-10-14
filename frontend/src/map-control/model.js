// 6R21E 纯空间控制模型（无 DOM/渲染耦合）。
// 拓扑：NavGraph（节点=可走 area，无向邻接）→ 多源 Dijkstra 图距离 → exp(-d/sigma) influence →
// NEUTRAL/CONTESTED/CT/T 分类 + scalar controlValue/magnitude。
// 确定性：同输入 + 同参数 → 同输出（hash 稳定）。Dead player 零贡献；绝不跨墙传播（图无边则不可达）。
// 与渲染分层：本文件只算模型，不画。坐标/参数由调用方给。

// ---- 图构建 ----
export function createGraph() {
  return { nodes: new Map(), adj: new Map() }; // adj: Map<id, Set<id>>
}
export function addNode(g, id, x, y) {
  g.nodes.set(id, { id, x, y });
  if (!g.adj.has(id)) g.adj.set(id, new Set());
}
export function addEdge(g, a, b) {
  if (!g.adj.has(a)) g.adj.set(a, new Set());
  if (!g.adj.has(b)) g.adj.set(b, new Set());
  g.adj.get(a).add(b);
  g.adj.get(b).add(a);
}
export function areaCentroid(area) {
  const c = area.corners || [];
  let sx = 0, sy = 0;
  for (const p of c) { sx += p[0]; sy += p[1]; }
  return { x: sx / c.length, y: sy / c.length };
}
// 从导出 JSON（cs2-navmesh-v1）建 NavGraph（每 area 一个节点；adjacency 无向边）
export function graphFromNavmesh(nav) {
  const g = createGraph();
  for (const a of nav.areas || []) {
    const c = areaCentroid(a);
    addNode(g, a.id, c.x, c.y);
  }
  for (const a of nav.areas || []) {
    for (const nb of a.adjacency || []) if (nb > a.id) addEdge(g, a.id, nb);
  }
  return g;
}
export function nodeDist(g, a, b) {
  const A = g.nodes.get(a), B = g.nodes.get(b);
  if (!A || !B) return Infinity;
  return Math.hypot(A.x - B.x, A.y - B.y);
}

// ---- 玩家 → area 映射（在真实图/同世界坐标系下按最近质心；绝不跨墙：图无边则不可达）----
export function mapPointToArea(g, x, y) {
  let best = null, bd = Infinity;
  for (const n of g.nodes.values()) {
    const d = Math.hypot(n.x - x, n.y - y);
    if (d < bd) { bd = d; best = n.id; }
  }
  return best;
}

// ---- 多源 Dijkstra（无向图、正值权重；返回 Map<id, 最短图距离>）----
export function multiSourceDijkstra(g, sources) {
  const dist = new Map();
  const heap = new MinHeap();
  for (const s of sources) {
    if (g.nodes.has(s) && !dist.has(s)) { dist.set(s, 0); heap.push([0, s]); }
  }
  while (heap.size() > 0) {
    const [d0, u] = heap.pop();
    if ((dist.get(u) ?? Infinity) < d0) continue;
    for (const v of g.adj.get(u) || []) {
      const nd = d0 + nodeDist(g, u, v);
      if (nd < (dist.get(v) ?? Infinity)) { dist.set(v, nd); heap.push([nd, v]); }
    }
  }
  return dist;
}

// ---- influence / 分类 ----
export function influence(d, sigma) { return sigma > 0 ? Math.exp(-d / sigma) : 0; }

// 分类：NEUTRAL/CONTESTED/CT/T（按 spec 逻辑；必须支持 NEUTRAL）
export function classifyArea(ct, t, neutralThreshold, contestRatio) {
  const total = ct + t;
  const magnitude = Math.max(ct, t);
  if (total < 1e-9 || magnitude < neutralThreshold) return { state: 'NEUTRAL', controlValue: 0, magnitude };
  const controlValue = (ct - t) / total; // -1 = T, +1 = CT
  const normDiff = Math.abs(ct - t) / total;
  let state;
  if (normDiff < contestRatio) state = 'CONTESTED';
  else state = ct > t ? 'CT' : 'T';
  return { state, controlValue, magnitude };
}

// ---- 控制帧 ----
const EPS = 1e-9;
export function computeControlFrame(g, players, config) {
  const { sigma, neutralThreshold, contestRatio, maxPropagationDistance } = config;
  const ctSrc = players.filter((p) => p.team === 'CT' && p.alive !== false && g.nodes.has(p.areaId)).map((p) => p.areaId);
  const tSrc = players.filter((p) => p.team === 'T' && p.alive !== false && g.nodes.has(p.areaId)).map((p) => p.areaId);
  const ctDist = multiSourceDijkstra(g, ctSrc);
  const tDist = multiSourceDijkstra(g, tSrc);
  const areas = [];
  for (const id of g.nodes.keys()) {
    const cd = ctDist.get(id), td = tDist.get(id);
    const ct = (cd != null && (maxPropagationDistance == null || cd <= maxPropagationDistance)) ? influence(cd, sigma) : 0;
    const t = (td != null && (maxPropagationDistance == null || td <= maxPropagationDistance)) ? influence(td, sigma) : 0;
    const r = classifyArea(ct, t, neutralThreshold, contestRatio);
    areas.push({ areaId: id, ctInfluence: ct, tInfluence: t, magnitude: r.magnitude, controlValue: r.controlValue, state: r.state });
  }
  areas.sort((a, b) => a.areaId - b.areaId);
  return { timestampMs: 0, areas, hash: frameHash(areas) };
}

// ---- 确定性 hash（FNV-1a，同输入稳定）----
export function frameHash(areas) {
  let h = 0x811c9dc5;
  for (const a of areas) {
    const s = `${a.areaId}:${a.state}:${a.controlValue.toFixed(4)}:${a.magnitude.toFixed(4)};`;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h * 0x01000193) >>> 0; }
  }
  return h >>> 0;
}

function MinHeap() {
  const a = [];
  return {
    size: () => a.length,
    push: (item) => {
      a.push(item);
      let i = a.length - 1;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (a[p][0] <= a[i][0]) break;
        [a[p], a[i]] = [a[i], a[p]];
        i = p;
      }
    },
    pop: () => {
      if (!a.length) return null;
      const top = a[0];
      const last = a.pop();
      if (a.length) {
        a[0] = last;
        let i = 0;
        for (;;) {
          const l = 2 * i + 1, r = 2 * i + 2;
          let m = i;
          if (l < a.length && a[l][0] < a[m][0]) m = l;
          if (r < a.length && a[r][0] < a[m][0]) m = r;
          if (m === i) break;
          [a[m], a[i]] = [a[i], a[m]];
          i = m;
        }
      }
      return top;
    },
  };
}
