<template>
  <div class="app-container">
    <header class="app-header">
      <div class="header-left">
        <div class="header-logo"></div>
        <h1 class="app-title">文件留底检测</h1>
      </div>
    </header>
    <ArchiveDetectEntryPage class="full-view" />
  </div>
</template>

<script setup>
import ArchiveDetectEntryPage from './components/ArchiveDetectEntryPage.vue'
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  height: 100%;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f8;
}
#app { height: 100%; }
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
  background: linear-gradient(90deg, #fb923c, #f59e0b);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-logo {
  width: 32px;
  height: 32px;
  background: linear-gradient(135deg, #fb923c, #f59e0b);
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
  background: linear-gradient(135deg, #fb923c, #f59e0b);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 0.5px;
}

.full-view { flex: 1; overflow: hidden; }

::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
</style>
