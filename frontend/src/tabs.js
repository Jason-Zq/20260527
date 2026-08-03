/**
 * 多页签(tags-view)状态：已访问 tab 列表 + keep-alive 缓存名列表。
 * 项目无 Pinia，用 reactive 单例模块即可。
 * tab = { path(fullPath，唯一键), title(来自 route.meta.title), name(组件名，供 keep-alive include), closable }
 */
import { computed, nextTick, reactive } from 'vue'

export const tabsState = reactive({
  tabs: [],
  // path -> 刷新纪元：refreshTab 时 +1，拼进 router-view 的 key 强制重挂载
  epochs: {},
  // 正被刷新的组件名：include 里临时剔除一个 tick，清掉该组件的旧缓存实例
  refreshing: null,
})

// keep-alive 的 include：所有 tab 的组件名去重，剔除正在刷新的
export const cachedNames = computed(() => {
  const names = new Set()
  for (const t of tabsState.tabs) {
    if (t.name && t.name !== tabsState.refreshing) names.add(t.name)
  }
  return [...names]
})

export function addTab({ path, title, name }) {
  if (!path || path === '/login') return
  if (tabsState.tabs.some((t) => t.path === path)) return
  tabsState.tabs.push({ path, title: title || '主页', name, closable: path !== '/' })
}

export function closeTab(path) {
  const i = tabsState.tabs.findIndex((t) => t.path === path)
  if (i === -1 || !tabsState.tabs[i].closable) return
  tabsState.tabs.splice(i, 1)
}

export function closeOthers(keepPath) {
  tabsState.tabs = tabsState.tabs.filter((t) => !t.closable || t.path === keepPath)
}

export function closeAll() {
  tabsState.tabs = tabsState.tabs.filter((t) => !t.closable)
}

// 登出时清空全部 tab 与刷新纪元(模块级单例,否则活到下一会话)
export function resetTabs() {
  tabsState.tabs = []
  tabsState.epochs = {}
}

// 刷新：include 临时剔除该组件名(清缓存实例) + 递增 key 纪元(强制重挂载)
export async function refreshTab(path) {
  const tab = tabsState.tabs.find((t) => t.path === path)
  if (!tab?.name) return
  tabsState.refreshing = tab.name
  tabsState.epochs[path] = (tabsState.epochs[path] || 0) + 1
  await nextTick()
  tabsState.refreshing = null
}
