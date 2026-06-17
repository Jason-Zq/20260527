<template>
  <div class="app-container">
    <!-- 顶部标题栏 -->
    <header class="app-header">
      <div class="header-left">
        <div class="header-logo"></div>
        <h1 class="app-title">智能文档审核工作台</h1>
      </div>
    </header>

    <!-- 首页：四个入口卡片 -->
    <HomePage
      v-if="viewMode === 'home'"
      @parse="viewMode = 'parse'"
      @template="viewMode = 'template'"
      @split="viewMode = 'split'"
      @summary="viewMode = 'summary'"
      class="full-view"
    />

    <!-- AI 材料解析入口页 -->
    <ParseEntryPage
      v-else-if="viewMode === 'parse'"
      @back="viewMode = 'home'"
      class="full-view"
    />

    <!-- AI 填写文件入口页 -->
    <FillEntryPage
      v-else-if="viewMode === 'template'"
      @back="viewMode = 'home'"
      class="full-view"
    />

    <!-- 处理超长PDF文件入口页 -->
    <SplitEntryPage
      v-else-if="viewMode === 'split'"
      @back="viewMode = 'home'"
      class="full-view"
    />

    <!-- 文件解析（URL → 摘要）入口页 -->
    <SummaryEntryPage
      v-else-if="viewMode === 'summary'"
      @back="viewMode = 'home'"
      class="full-view"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import HomePage from './components/HomePage.vue'
import ParseEntryPage from './components/ParseEntryPage.vue'
import FillEntryPage from './components/FillEntryPage.vue'
import SplitEntryPage from './components/SplitEntryPage.vue'
import SummaryEntryPage from './components/SummaryEntryPage.vue'

// viewMode: home | parse | template | split | summary
const viewMode = ref('home')
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
