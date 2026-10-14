// 6R21F D4 时间连续：固定模型步长的 bracket-lerp（纯函数，可注入 snapshot 便于测试）。
// frameAt(t) 对 floor(t/h) 与 ceil(t/h) 两个确定性模型帧做线性插值；cache 可 invalidation（cfg 改变清空）。
export function createFrameTimeline({ stepH = 0.1, maxId = 0, snapshot, cacheLimit = 48 }) {
  const cache = new Map()
  const cvBuf = new Float32Array(maxId + 1)
  const magBuf = new Float32Array(maxId + 1)
  function snap(st) {
    let f = cache.get(st)
    if (!f) {
      f = snapshot(st) // { cv: Float32Array, mag: Float32Array }
      cache.set(st, f)
      if (cache.size > cacheLimit) cache.delete(cache.keys().next().value)
    }
    return f
  }
  function frameAt(t) {
    const h = stepH
    const st0 = Math.floor(t / h) * h
    const st1 = st0 + h
    const f0 = snap(st0), f1 = snap(st1)
    const frac = st1 > st0 ? Math.min(1, Math.max(0, (t - st0) / h)) : 0
    for (let i = 0; i <= maxId; i++) { cvBuf[i] = f0.cv[i] + (f1.cv[i] - f0.cv[i]) * frac; magBuf[i] = f0.mag[i] + (f1.mag[i] - f0.mag[i]) * frac }
    return { cv: cvBuf, mag: magBuf, frac }
  }
  function invalidate() { cache.clear() }
  return { frameAt, invalidate, _cache: cache }
}
