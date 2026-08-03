<template>
  <div class="tags-view">
    <div ref="scrollRef" class="tags-scroll" @wheel.passive="onWheel">
      <div
        v-for="tab in tabsState.tabs"
        :key="tab.path"
        class="tag-item"
        :class="{ active: tab.path === route.fullPath }"
        @click="go(tab)"
        @mousedown.middle.prevent="onClose(tab)"
        @contextmenu.prevent="openMenu(tab, $event)"
      >
        <span class="tag-title">{{ tab.title }}</span>
        <el-icon v-if="tab.closable" class="tag-close" @click.stop="onClose(tab)"><Close /></el-icon>
      </div>
    </div>

    <!-- 右键菜单(teleport 到 body,避免被 tab 条横向滚动裁剪) -->
    <teleport to="body">
      <ul
        v-show="menu.visible"
        class="tag-menu"
        :style="{ left: menu.left + 'px', top: menu.top + 'px' }"
      >
        <li @click="onRefresh"><el-icon><Refresh /></el-icon>刷新当前</li>
        <li v-if="menu.tab?.closable" @click="onClose(menu.tab)"><el-icon><Close /></el-icon>关闭当前</li>
        <li @click="onCloseOthers"><el-icon><CircleClose /></el-icon>关闭其他</li>
        <li @click="onCloseAll"><el-icon><FolderDelete /></el-icon>关闭全部</li>
      </ul>
    </teleport>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Close, Refresh, CircleClose, FolderDelete } from '@element-plus/icons-vue'
import { tabsState, addTab, closeTab, closeOthers, closeAll, refreshTab } from '../tabs'

const route = useRoute()
const router = useRouter()

const scrollRef = ref()

// 路由变化 => 登记 tab + 把激活 tab 滚进可视区
watch(
  () => route.fullPath,
  async () => {
    addTab({ path: route.fullPath, title: route.meta.title, name: route.meta.cache })
    await nextTick()
    scrollRef.value
      ?.querySelector('.tag-item.active')
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  },
  { immediate: true }
)

function go(tab) {
  if (tab.path !== route.fullPath) router.push(tab.path)
}

// 关闭 tab;关掉的是当前激活 tab 时跳右侧邻居,无右侧跳左侧,全无跳首页
function onClose(tab) {
  if (!tab?.closable) return
  const idx = tabsState.tabs.findIndex((t) => t.path === tab.path)
  const wasActive = tab.path === route.fullPath
  closeTab(tab.path)
  if (wasActive) {
    const next = tabsState.tabs[idx] || tabsState.tabs[idx - 1]
    router.push(next ? next.path : '/')
  }
  closeMenu()
}

function onCloseOthers() {
  const keep = menu.tab?.path || route.fullPath
  closeOthers(keep)
  if (route.fullPath !== keep) router.push(keep)
  closeMenu()
}

function onCloseAll() {
  closeAll()
  if (route.path !== '/') router.push('/')
  closeMenu()
}

async function onRefresh() {
  if (menu.tab) await refreshTab(menu.tab.path)
  closeMenu()
}

// 滚轮上下滚动 => 横向滚动(纯横滑手势交给浏览器原生处理,不叠加 deltaX)
function onWheel(e) {
  if (scrollRef.value) scrollRef.value.scrollLeft += e.deltaY
}

// ---- 右键菜单 ----
const menu = reactive({ visible: false, left: 0, top: 0, tab: null })

function openMenu(tab, e) {
  menu.tab = tab
  // 防止菜单超出视口右/下边缘
  menu.left = Math.min(e.clientX, window.innerWidth - 140)
  menu.top = Math.min(e.clientY, window.innerHeight - 150)
  menu.visible = true
}

function closeMenu() {
  menu.visible = false
}

onMounted(() => document.addEventListener('click', closeMenu))
onBeforeUnmount(() => document.removeEventListener('click', closeMenu))
</script>

<style scoped>
.tags-view {
  flex-shrink: 0;
  height: 40px;
  background: #ffffff;
  border-bottom: 1px solid #e8ebf5;
  display: flex;
  align-items: center;
  padding: 0 12px;
}

.tags-scroll {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
}

.tags-scroll::-webkit-scrollbar {
  display: none;
}

.tag-item {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
  transition: all 0.15s ease;
}

.tag-item:hover {
  color: #4f46e5;
  border-color: #c7d2fe;
}

.tag-item.active {
  background: #eef2ff;
  border-color: #c7d2fe;
  color: #4f46e5;
  font-weight: 600;
}

.tag-close {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: 10px;
}

.tag-close:hover {
  background: #c7d2fe;
  color: #ffffff;
}

/* 右键菜单(teleport 到 body,scoped 的 data-v 属性仍会生效) */
.tag-menu {
  position: fixed;
  z-index: 4000;
  min-width: 124px;
  margin: 0;
  padding: 4px;
  list-style: none;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
}

.tag-menu li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  font-size: 12px;
  color: #475569;
  border-radius: 6px;
  cursor: pointer;
}

.tag-menu li:hover {
  background: #f1f5f9;
  color: #4f46e5;
}
</style>
