<template>
  <div class="home-page">
    <!-- 顶部品牌区 -->
    <header class="home-hero">
      <div class="home-logo">
        <span>AI</span>
      </div>
      <h1 class="home-title">智能文档审核工作台</h1>
      <p class="home-sub">AI 文件留底检测 · 多文件并发 · 敏感信息自动脱敏</p>
    </header>

    <!-- 主推大卡：文件留底检测 -->
    <main class="home-main">
      <article
        class="primary-card"
        @click="go('/archive-detect')"
        tabindex="0"
        @keydown.enter="go('/archive-detect')"
      >
        <div class="primary-glow"></div>
        <div class="primary-content">
          <div class="primary-icon">
            <el-icon :size="40"><Reading /></el-icon>
          </div>
          <div class="primary-text">
            <div class="primary-badge">主功能</div>
            <h2 class="primary-title">文件留底检测</h2>
            <p class="primary-desc">
              上传文件或粘贴 URL，AI 根据你的判定标准检测文件是否符合留底要求；金额、电话、身份证、银行卡等敏感信息自动脱敏。
            </p>
            <ul class="primary-bullets">
              <li><el-icon size="14"><Check /></el-icon> 多文件并发处理（≤20）</li>
              <li><el-icon size="14"><Check /></el-icon> 自定义判定标准</li>
              <li><el-icon size="14"><Check /></el-icon> 支持 OSS 临时签名地址</li>
              <li><el-icon size="14"><Check /></el-icon> 敏感信息自动脱敏</li>
            </ul>
          </div>
          <div class="primary-cta">
            立即开始
            <el-icon size="16"><ArrowRight /></el-icon>
          </div>
        </div>
      </article>

      <!-- 折叠区：更多内部工具 -->
      <div class="more-section">
        <button
          class="more-toggle"
          :class="{ open: showMore }"
          @click="showMore = !showMore"
        >
          更多内部工具
          <el-icon class="more-arrow"><ArrowDown /></el-icon>
        </button>

        <transition name="fold">
          <div v-show="showMore" class="more-tools">
            <article class="tool-card" @click="go('/parse')" tabindex="0" @keydown.enter="go('/parse')">
              <div class="tool-icon icon-magic"><el-icon :size="22"><MagicStick /></el-icon></div>
              <div class="tool-body">
                <h4 class="tool-title">AI 材料解析</h4>
                <p class="tool-desc">OCR 识别证件并归档客户档案</p>
              </div>
              <el-icon class="tool-arrow"><ArrowRight /></el-icon>
            </article>

            <article class="tool-card" @click="go('/template')" tabindex="0" @keydown.enter="go('/template')">
              <div class="tool-icon icon-doc"><el-icon :size="22"><Document /></el-icon></div>
              <div class="tool-body">
                <h4 class="tool-title">AI 填写文件</h4>
                <p class="tool-desc">Word 模板自动定位占位符 + 填值</p>
              </div>
              <el-icon class="tool-arrow"><ArrowRight /></el-icon>
            </article>

            <article class="tool-card" @click="go('/split')" tabindex="0" @keydown.enter="go('/split')">
              <div class="tool-icon icon-split"><el-icon :size="22"><Files /></el-icon></div>
              <div class="tool-body">
                <h4 class="tool-title">处理超长 PDF</h4>
                <p class="tool-desc">按证件类型自动拆分为独立文件</p>
              </div>
              <el-icon class="tool-arrow"><ArrowRight /></el-icon>
            </article>

            <article class="tool-card" @click="go('/archive-admin')" tabindex="0" @keydown.enter="go('/archive-admin')">
              <div class="tool-icon icon-admin"><el-icon :size="22"><Reading /></el-icon></div>
              <div class="tool-body">
                <h4 class="tool-title">审核任务管理</h4>
                <p class="tool-desc">查看批次、进度、文件结果与 OCR 文本</p>
              </div>
              <el-icon class="tool-arrow"><ArrowRight /></el-icon>
            </article>
          </div>
        </transition>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  MagicStick, Document, Files, Reading,
  ArrowRight, ArrowDown, Check,
} from '@element-plus/icons-vue'

const router = useRouter()
const showMore = ref(false)

function go(path) {
  router.push(path)
}
</script>

<style scoped>
.home-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 36px;
  padding: 48px 24px 40px;
  overflow-y: auto;
  background:
    radial-gradient(circle at 15% 20%, rgba(251, 146, 60, 0.10) 0%, transparent 45%),
    radial-gradient(circle at 85% 80%, rgba(245, 158, 11, 0.08) 0%, transparent 45%),
    linear-gradient(180deg, #f8fafc 0%, #f0f2f8 100%);
}

/* ========== 顶部品牌区 ========== */
.home-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  text-align: center;
  flex-shrink: 0;
}

.home-logo {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  background: linear-gradient(135deg, #fb923c 0%, #f59e0b 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 800;
  font-size: 20px;
  letter-spacing: 1px;
  box-shadow:
    0 12px 30px rgba(251, 146, 60, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.25);
  animation: float 6s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}

.home-title {
  margin: 4px 0 0;
  font-size: 30px;
  font-weight: 800;
  background: linear-gradient(135deg, #1e293b 0%, #fb923c 60%, #f59e0b 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 0.5px;
}

.home-sub {
  margin: 0;
  color: #64748b;
  font-size: 14px;
  letter-spacing: 0.5px;
}

/* ========== 主区域 ========== */
.home-main {
  width: 100%;
  max-width: 880px;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

/* ========== 主推卡片 ========== */
.primary-card {
  position: relative;
  background: #fff;
  border: 1px solid #fde68a;
  border-radius: 20px;
  padding: 36px 40px 32px;
  cursor: pointer;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1),
              box-shadow 0.3s ease,
              border-color 0.3s ease;
  box-shadow:
    0 4px 12px rgba(251, 146, 60, 0.08),
    0 1px 3px rgba(15, 23, 42, 0.04);
  overflow: hidden;
  outline: none;
}

.primary-card:hover,
.primary-card:focus-visible {
  transform: translateY(-4px);
  box-shadow:
    0 20px 50px -12px rgba(251, 146, 60, 0.35),
    0 8px 16px -8px rgba(15, 23, 42, 0.08);
  border-color: #fb923c;
}

.primary-card:focus-visible {
  outline: 2px solid #fb923c;
  outline-offset: 3px;
}

.primary-glow {
  position: absolute;
  top: -80px;
  right: -80px;
  width: 280px;
  height: 280px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(251, 146, 60, 0.25), transparent 70%);
  filter: blur(20px);
  pointer-events: none;
  transition: opacity 0.4s ease;
}

.primary-content {
  position: relative;
  display: flex;
  gap: 28px;
  align-items: flex-start;
}

.primary-icon {
  flex-shrink: 0;
  width: 76px;
  height: 76px;
  border-radius: 18px;
  background: linear-gradient(135deg, #fb923c, #f59e0b);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 10px 25px -6px rgba(251, 146, 60, 0.5);
  transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

.primary-card:hover .primary-icon,
.primary-card:focus-visible .primary-icon {
  transform: scale(1.05) rotate(-4deg);
}

.primary-text {
  flex: 1;
  min-width: 0;
}

.primary-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #fb923c;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  padding: 3px 10px;
  border-radius: 999px;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.primary-title {
  margin: 0 0 8px;
  font-size: 24px;
  font-weight: 800;
  color: #0f172a;
  letter-spacing: 0.3px;
}

.primary-desc {
  margin: 0 0 14px;
  font-size: 14px;
  line-height: 1.7;
  color: #475569;
}

.primary-bullets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px 16px;
}

.primary-bullets li {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #334155;
}

.primary-bullets li :deep(.el-icon) {
  flex-shrink: 0;
  color: #10b981;
}

.primary-cta {
  position: absolute;
  bottom: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  color: #fb923c;
  padding: 10px 18px;
  background: #fff7ed;
  border-radius: 12px;
  transition: gap 0.2s ease, transform 0.2s ease;
}

.primary-card:hover .primary-cta,
.primary-card:focus-visible .primary-cta {
  gap: 12px;
  transform: translateX(-2px);
}

/* ========== 折叠区：更多工具 ========== */
.more-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.more-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: 1px dashed #cbd5e1;
  color: #64748b;
  padding: 7px 18px;
  border-radius: 999px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.more-toggle:hover {
  border-color: #94a3b8;
  color: #475569;
  background: #f8fafc;
}

.more-arrow {
  transition: transform 0.3s ease;
}

.more-toggle.open .more-arrow {
  transform: rotate(180deg);
}

.more-tools {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.tool-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid #e8ebf5;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  opacity: 0.85;
  outline: none;
}

.tool-card:hover,
.tool-card:focus-visible {
  opacity: 1;
  transform: translateY(-2px);
  box-shadow: 0 6px 14px -6px rgba(15, 23, 42, 0.15);
  border-color: #cbd5e1;
}

.tool-card:focus-visible {
  outline: 2px solid #94a3b8;
  outline-offset: 2px;
}

.tool-icon {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}

.tool-icon.icon-magic {
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
}
.tool-icon.icon-doc {
  background: linear-gradient(135deg, #3b82f6, #6366f1);
}
.tool-icon.icon-split {
  background: linear-gradient(135deg, #10b981, #06b6d4);
}
.tool-icon.icon-admin {
  background: linear-gradient(135deg, #f97316, #f59e0b);
}

.tool-body {
  flex: 1;
  min-width: 0;
}

.tool-title {
  margin: 0 0 2px;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}

.tool-desc {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-arrow {
  flex-shrink: 0;
  color: #cbd5e1;
  transition: transform 0.2s ease, color 0.2s ease;
}

.tool-card:hover .tool-arrow,
.tool-card:focus-visible .tool-arrow {
  color: #64748b;
  transform: translateX(2px);
}

/* 折叠过渡 */
.fold-enter-active,
.fold-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.fold-enter-from,
.fold-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

/* ========== 响应式 ========== */
@media (max-width: 900px) {
  .primary-content {
    flex-direction: column;
    gap: 18px;
  }
  .primary-cta {
    position: static;
    align-self: flex-end;
    margin-top: 8px;
  }
  .more-tools {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 540px) {
  .home-page {
    padding: 28px 16px;
    gap: 24px;
  }
  .home-title { font-size: 24px; }
  .primary-card { padding: 24px 22px; }
  .primary-bullets { grid-template-columns: 1fr; }
}
</style>
