/**
 * 6R4：路线回放跨 Tab 持久状态（durable UI state）。
 * 切换「我的/热力图/道具」等 Tab 时 PersonalTab 会 v-if 卸载重挂，
 * 但焦点玩家已在服务端持久化；这里再持久化本地的 side/round/speed，
 * 保证切回「我的」时路线播放器的筛选与回合选择不丢。
 */
import { reactive } from 'vue'

export const routeReplayState = reactive({
  side: 'all',        // single 模式：all | CT | T（focus 玩家真实阵营过滤）
  round: null,        // single 模式：选中的回合号（null = 默认首个可用回合）
  speed: 1,           // 0.5 | 1 | 2
  showFullRoute: false, // single 模式：是否显示完整路线（默认关；仅当前会话持久）
  mode: 'single',     // single（单回合回放） | half（半场总览）
  halfSide: 'CT',     // half 模式：CT | T（必须选边，无 all）
})

// 当焦点玩家变化时重置（新玩家 → 新回合列表）
export function resetRouteReplayState() {
  routeReplayState.side = 'all'
  routeReplayState.round = null
  routeReplayState.speed = 1
  routeReplayState.showFullRoute = false
  routeReplayState.mode = 'single'
  routeReplayState.halfSide = 'CT'
}
