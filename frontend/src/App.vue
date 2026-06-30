<template>
  <div class="app-container">
    <!-- 顶部标题栏 -->
    <header class="app-header">
      <div class="header-left" @click="go('/')">
        <div class="header-logo"></div>
        <h1 class="app-title">智能文档审核工作台</h1>
      </div>
      <nav class="top-nav">
        <button class="nav-item" :class="{ active: isActive('/clients') }" @click="go('/clients')">客户档案</button>
        <button class="nav-item" :class="{ active: isActive('/archive-detect') }" @click="go('/archive-detect')">文件留底检测</button>
        <button class="nav-item" :class="{ active: isActive('/archive-admin') }" @click="go('/archive-admin')">审核任务管理</button>
        <button class="nav-item" :class="{ active: isActive('/events') }" @click="go('/events')">事件流</button>
        <button class="nav-item" :class="{ active: isActive('/request-logs') }" @click="go('/request-logs')">请求记录</button>
        <button class="nav-item" :class="{ active: isActive('/child-age-leads') }" @click="go('/child-age-leads')">子女年龄线索</button>
        <el-dropdown trigger="click">
          <button class="nav-item more">更多工具</button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="go('/parse')">AI 材料解析</el-dropdown-item>
              <el-dropdown-item @click="go('/template')">AI 填写文件</el-dropdown-item>
              <el-dropdown-item @click="go('/split')">处理超长 PDF</el-dropdown-item>
              <el-dropdown-item @click="go('/summary')">URL 文件摘要</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </nav>
    </header>

    <!-- 路由出口：每个页面通过 router-view 渲染 -->
    <router-view class="full-view" />
  </div>
</template>

<script setup>
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

function go(path) {
  router.push(path)
}

function isActive(path) {
  return route.path === path || route.path.startsWith(path + '/')
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
  flex-direction: column;
  background: #f0f2f8;
  color: #1e293b;
}

.app-header {
  padding: 0 28px;
  height: 56px;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  position: relative;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.app-header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, #6366f1, #8b5cf6, #a78bfa);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}

.top-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}

.nav-item {
  border: none;
  background: transparent;
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
  padding: 7px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.nav-item:hover,
.nav-item.active {
  background: #eef2ff;
  color: #4f46e5;
}

.nav-item.more {
  outline: none;
}

.header-logo {
  width: 32px;
  height: 32px;
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

.app-title {
  font-size: 17px;
  font-weight: 700;
  margin: 0;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 0.5px;
}

.full-view {
  flex: 1;
  overflow: hidden;
}

/* 滚动条 */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
</style>
