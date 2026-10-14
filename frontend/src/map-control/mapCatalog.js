// 6R21F-E6D：地图接入目录（demo → 权威地图 → NavMesh/radar/sample URL）。
// 产品按权威 mapName 选择 backend /api/maps/{map}/navmesh 与对应 radar/样本；
// 不复制一套 Mirage 组件；未接入地图 / 未知 demo 一律返回 null（fail-closed，前端诚实降级）。
//
// 权威对齐（三源一致，见 docs/6r21f-e6c-multimap-preflight.md）：
//   demo19 → de_mirage；demo22 → de_inferno。
// 每张图只保留一份 NavMesh artifact（demo22 对应地图即 de_inferno，去重后单份）。

const REPLAY_CATALOG = {
  de_mirage: {
    navUrl: '/api/maps/de_mirage/navmesh',
    radarUrl: '/api/maps/de_mirage/radar',
    sampleUrl: '/demo/6r21-round-sample.json',
  },
  de_dust2: {
    navUrl: '/api/maps/de_dust2/navmesh',
    radarUrl: '/api/maps/de_dust2/radar',
    sampleUrl: '/demo/dust2-round-sample.json',
  },
  de_inferno: {
    navUrl: '/api/maps/de_inferno/navmesh',
    radarUrl: '/api/maps/de_inferno/radar',
    sampleUrl: '/demo/6r22-round-sample.json',
  },
}

// demo → 权威地图（demo 比赛/产品回放按此选择）。仅收录已接入地图的 demo。
export const demoMapAlignment = {
  19: 'de_mirage',
  21: 'de_dust2',
  22: 'de_inferno',
}

/** 该地图是否为已接入的可回放地图。 */
export function isReplayMap(mapName) {
  return Object.prototype.hasOwnProperty.call(REPLAY_CATALOG, mapName)
}

/** 返回该地图的 navmesh/radar/sample URL；未接入返回 null（fail-closed）。 */
export function replayConfigFor(mapName) {
  if (!isReplayMap(mapName)) return null
  return REPLAY_CATALOG[mapName]
}

/** 该 demo 的权威地图名；未收录返回 null（不误用其它地图）。 */
export function authoritativeMapForDemo(demoId) {
  return demoMapAlignment[String(demoId)] || null
}
