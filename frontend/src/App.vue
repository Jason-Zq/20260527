<template>
  <div class="app-container">
    <template v-if="!isLoginPage">
      <!-- 左侧分组侧边栏(Ant Design Pro 经典中后台布局);菜单项配置在 menu.js -->
      <aside class="sidebar" :class="{ collapsed }">
        <div class="logo-area" @click="go('/')">
          <div class="header-logo"></div>
          <span v-show="!collapsed" class="logo-title">智能文档审核工作台</span>
        </div>

        <el-menu
          ref="menuRef"
          class="side-menu"
          :default-active="route.path"
          :collapse="collapsed"
          :collapse-transition="false"
          router
        >
          <el-menu-item index="/">
            <el-icon><HomeFilled /></el-icon>
            <template #title>首页</template>
          </el-menu-item>

          <el-sub-menu v-for="group in menuGroups" :key="group.key" :index="group.key">
            <template #title>
              <el-icon><component :is="group.icon" /></el-icon>
              <span>{{ group.title }}</span>
            </template>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon>
              <template #title>{{ item.title }}</template>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>

        <div class="sidebar-footer">
          <el-tooltip :content="collapsed ? '展开菜单' : '收起菜单'" placement="right">
            <button class="footer-btn" @click="collapsed = !collapsed">
              <el-icon><Expand v-if="collapsed" /><Fold v-else /></el-icon>
            </button>
          </el-tooltip>
          <el-tooltip content="退出登录" placement="right">
            <button class="footer-btn logout" @click="onLogout">
              <el-icon><SwitchButton /></el-icon>
            </button>
          </el-tooltip>
        </div>
      </aside>

      <!-- 右侧:页签条 + 路由出口(keep-alive 保活已访问页面) -->
      <main class="main-area">
        <TagsView />
        <div class="content-area">
          <router-view v-slot="{ Component }">
            <keep-alive :include="cachedNames">
              <component
                :is="Component"
                :key="route.fullPath + ':' + (tabsState.epochs[route.fullPath] || 0)"
                class="full-view"
              />
            </keep-alive>
          </router-view>
        </div>
      </main>
    </template>

    <!-- 登录页不显示侧边栏 -->
    <router-view v-else class="full-view" />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { SwitchButton, Fold, Expand, HomeFilled } from '@element-plus/icons-vue'
import { clearToken } from './api'
import { menuGroups } from './menu'
import { tabsState, cachedNames, resetTabs } from './tabs'
import TagsView from './components/TagsView.vue'

const router = useRouter()
const route = useRoute()

const collapsed = ref(false)
const menuRef = ref()

const isLoginPage = computed(() => route.path === '/login')

// 当前路由所在分组;路由变化时幂等展开(open 不会收起用户已开的组)
const activeGroupKey = computed(() =>
  menuGroups.find((g) => g.items.some((i) => route.path === i.path || route.path.startsWith(i.path + '/')))?.key
)
watch(
  activeGroupKey,
  (key) => {
    if (key) nextTick(() => menuRef.value?.open(key))
  },
  { immediate: true }
)

function go(path) {
  router.push(path)
}

function onLogout() {
  clearToken()
  resetTabs()
  router.push('/login')
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body {
  height: 100%;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f8;
}

#app {
  height: 100%;
}
</style>

<style scoped>
.app-container {
  height: 100vh;
  display: flex;
  background: #f0f2f8;
  color: #1e293b;
}

/* ========== 侧边栏 ========== */
.sidebar {
  width: 216px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-right: 1px solid #e8ebf5;
  transition: width 0.2s ease;
  overflow: hidden;
}

.sidebar.collapsed {
  width: 64px;
}

.logo-area {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px;
  cursor: pointer;
  flex-shrink: 0;
  border-bottom: 1px solid #f1f5f9;
  white-space: nowrap;
}

.sidebar.collapsed .logo-area {
  padding: 0;
  justify-content: center;
}

.logo-title {
  font-size: 15px;
  font-weight: 700;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 0.5px;
}

.side-menu {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  border-right: none;
  padding: 8px;
  --el-menu-text-color: #475569;
  --el-menu-hover-bg-color: #f5f7ff;
  --el-menu-active-color: #4f46e5;
  --el-menu-item-height: 42px;
}

.side-menu :deep(.el-menu-item),
.side-menu :deep(.el-sub-menu__title) {
  border-radius: 8px;
  margin: 2px 0;
  font-weight: 500;
}

.side-menu :deep(.el-menu-item.is-active) {
  background: #eef2ff;
  font-weight: 600;
}

/* 折叠态去掉横向内边距,图标与底部按钮对齐;分组图标 hover 出 flyout 子菜单 */
.side-menu.el-menu--collapse {
  padding: 8px 0;
}

/* 子项激活时一级标题高亮(不加背景色,背景留给具体激活子项) */
.side-menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
  color: var(--el-menu-active-color);
  font-weight: 600;
}

.sidebar-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 10px 12px;
  border-top: 1px solid #f1f5f9;
}

.sidebar.collapsed .sidebar-footer {
  flex-direction: column;
  padding: 10px 0;
}

.footer-btn {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #64748b;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.footer-btn:hover {
  background: #f1f5f9;
  color: #4f46e5;
}

.footer-btn.logout:hover {
  background: #fef2f2;
  color: #ef4444;
}

/* ========== 内容区 ========== */
.main-area {
  flex: 1;
  min-width: 0;
  height: 100%;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.content-area {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.full-view {
  height: 100%;
  overflow: hidden;
}

/* 登录页:router-view 是 app-container 唯一直接子节点 */
.app-container > .full-view {
  flex: 1;
}

.header-logo {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  border-radius: 8px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.header-logo::after {
  content: 'AI';
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

/* 滚动条 */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
</style>
