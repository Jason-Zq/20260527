<template>
  <div class="home-page">
    <!-- 顶部品牌区 -->
    <header class="home-hero">
      <div class="home-logo">
        <span>AI</span>
      </div>
      <h1 class="home-title">智能文档工作台</h1>
      <p class="home-sub">移民客户档案 · AI 解析 · 模板填写 · PDF 拆分</p>
    </header>

    <!-- 横向三栏卡片 -->
    <main class="home-entries">
      <article class="entry-card" @click="emit('parse')" tabindex="0" @keydown.enter="emit('parse')">
        <div class="card-glow icon-magic"></div>
        <div class="entry-icon icon-magic">
          <el-icon :size="30"><MagicStick /></el-icon>
        </div>
        <h3 class="entry-title">AI 材料解析</h3>
        <p class="entry-desc">上传证件、护照、学历证书等材料，AI 自动 OCR 识别并归档到客户档案</p>
        <ul class="entry-bullets">
          <li><el-icon size="12"><Check /></el-icon> 拖拽多文件批量上传</li>
          <li><el-icon size="12"><Check /></el-icon> 智能匹配现有客户</li>
          <li><el-icon size="12"><Check /></el-icon> 字段精准路由到主表/子表</li>
        </ul>
        <div class="entry-cta">
          开始使用
          <el-icon size="14"><ArrowRight /></el-icon>
        </div>
      </article>

      <article class="entry-card" @click="emit('template')" tabindex="0" @keydown.enter="emit('template')">
        <div class="card-glow icon-doc"></div>
        <div class="entry-icon icon-doc">
          <el-icon :size="30"><Document /></el-icon>
        </div>
        <h3 class="entry-title">AI 填写文件</h3>
        <p class="entry-desc">上传 Word 模板，AI 自动定位占位符，从客户档案抽取数据生成 PDF</p>
        <ul class="entry-bullets">
          <li><el-icon size="12"><Check /></el-icon> 智能识别占位符</li>
          <li><el-icon size="12"><Check /></el-icon> 客户档案一键填充</li>
          <li><el-icon size="12"><Check /></el-icon> 核心字段自动锁定</li>
        </ul>
        <div class="entry-cta">
          开始使用
          <el-icon size="14"><ArrowRight /></el-icon>
        </div>
      </article>

      <article class="entry-card" @click="emit('split')" tabindex="0" @keydown.enter="emit('split')">
        <div class="card-glow icon-split"></div>
        <div class="entry-icon icon-split">
          <el-icon :size="30"><Files /></el-icon>
        </div>
        <h3 class="entry-title">处理超长 PDF</h3>
        <p class="entry-desc">把多证件合并的 PDF 自动按证件类型拆分为独立文件，一键打包下载</p>
        <ul class="entry-bullets">
          <li><el-icon size="12"><Check /></el-icon> 全页 OCR 自动分类</li>
          <li><el-icon size="12"><Check /></el-icon> 进程重启历史可恢复</li>
          <li><el-icon size="12"><Check /></el-icon> 7 天后自动清理文件</li>
        </ul>
        <div class="entry-cta">
          开始使用
          <el-icon size="14"><ArrowRight /></el-icon>
        </div>
      </article>

      <article class="entry-card" @click="emit('summary')" tabindex="0" @keydown.enter="emit('summary')">
        <div class="card-glow icon-summary"></div>
        <div class="entry-icon icon-summary">
          <el-icon :size="30"><Reading /></el-icon>
        </div>
        <h3 class="entry-title">文件解析</h3>
        <p class="entry-desc">输入文件 URL，AI 下载并 OCR 识别，输出文件内容摘要与关键要点</p>
        <ul class="entry-bullets">
          <li><el-icon size="12"><Check /></el-icon> 支持 PDF / 图片 / Word</li>
          <li><el-icon size="12"><Check /></el-icon> 一句话定性 + 摘要</li>
          <li><el-icon size="12"><Check /></el-icon> 历史记录可复查</li>
        </ul>
        <div class="entry-cta">
          开始使用
          <el-icon size="14"><ArrowRight /></el-icon>
        </div>
      </article>
    </main>

    <!-- 底部脚标 -->
    <footer class="home-footer">
      <span class="footer-tip">💡 推荐先建立客户档案，再使用 AI 解析自动归档</span>
    </footer>
  </div>
</template>

<script setup>
import { MagicStick, Document, Files, Reading, ArrowRight, Check } from '@element-plus/icons-vue'

const emit = defineEmits(['parse', 'template', 'split', 'summary'])
</script>

<style scoped>
.home-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 48px;
  padding: 40px 24px 32px;
  overflow-y: auto;
  background:
    radial-gradient(circle at 15% 20%, rgba(99, 102, 241, 0.10) 0%, transparent 45%),
    radial-gradient(circle at 85% 75%, rgba(16, 185, 129, 0.08) 0%, transparent 45%),
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
  background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 800;
  font-size: 20px;
  letter-spacing: 1px;
  box-shadow:
    0 12px 30px rgba(99, 102, 241, 0.35),
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
  background: linear-gradient(135deg, #1e293b 0%, #6366f1 60%, #8b5cf6 100%);
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

/* ========== 横向卡片区 ========== */
.home-entries {
  width: 100%;
  max-width: 1280px;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 20px;
  flex-shrink: 0;
}

.entry-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 32px 28px 28px;
  background: #fff;
  border: 1px solid #e8ebf5;
  border-radius: 18px;
  cursor: pointer;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1),
              box-shadow 0.3s ease,
              border-color 0.3s ease;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  outline: none;
  overflow: hidden;
}

.entry-card:hover,
.entry-card:focus-visible {
  transform: translateY(-6px);
  box-shadow:
    0 20px 40px -12px rgba(99, 102, 241, 0.25),
    0 8px 16px -8px rgba(15, 23, 42, 0.08);
  border-color: rgba(99, 102, 241, 0.3);
}

.entry-card:focus-visible {
  outline: 2px solid #6366f1;
  outline-offset: 2px;
}

/* 卡片角落柔光（hover 时显现） */
.card-glow {
  position: absolute;
  top: -40px;
  right: -40px;
  width: 160px;
  height: 160px;
  border-radius: 50%;
  filter: blur(40px);
  opacity: 0;
  transition: opacity 0.4s ease;
  pointer-events: none;
}
.card-glow.icon-magic   { background: rgba(99, 102, 241, 0.4); }
.card-glow.icon-doc     { background: rgba(59, 130, 246, 0.4); }
.card-glow.icon-split   { background: rgba(16, 185, 129, 0.4); }
.card-glow.icon-summary { background: rgba(251, 146, 60, 0.4); }

.entry-card:hover .card-glow,
.entry-card:focus-visible .card-glow {
  opacity: 1;
}

/* 图标 */
.entry-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
  position: relative;
  transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}

.entry-icon.icon-magic {
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  box-shadow: 0 8px 20px -4px rgba(99, 102, 241, 0.4);
}
.entry-icon.icon-doc {
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  box-shadow: 0 8px 20px -4px rgba(59, 130, 246, 0.4);
}
.entry-icon.icon-split {
  background: linear-gradient(135deg, #10b981, #06b6d4);
  box-shadow: 0 8px 20px -4px rgba(16, 185, 129, 0.4);
}
.entry-icon.icon-summary {
  background: linear-gradient(135deg, #fb923c, #f59e0b);
  box-shadow: 0 8px 20px -4px rgba(251, 146, 60, 0.4);
}

.entry-card:hover .entry-icon,
.entry-card:focus-visible .entry-icon {
  transform: scale(1.08) rotate(-4deg);
}

/* 文字 */
.entry-title {
  margin: 4px 0 0;
  font-size: 19px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: 0.3px;
}

.entry-desc {
  margin: 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
  min-height: 40px;
}

/* 特性列表 */
.entry-bullets {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}

.entry-bullets li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #475569;
  line-height: 1.4;
}

.entry-bullets li :deep(.el-icon) {
  flex-shrink: 0;
  color: #10b981;
}

/* CTA */
.entry-cta {
  margin-top: 12px;
  padding-top: 14px;
  border-top: 1px dashed #e2e8f0;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #6366f1;
  transition: gap 0.2s ease;
}

.entry-card:hover .entry-cta,
.entry-card:focus-visible .entry-cta {
  gap: 10px;
}

/* ========== 脚标 ========== */
.home-footer {
  flex-shrink: 0;
}

.footer-tip {
  font-size: 12px;
  color: #94a3b8;
  letter-spacing: 0.3px;
}

/* ========== 响应式 ========== */
@media (max-width: 1180px) {
  .home-entries {
    grid-template-columns: repeat(2, 1fr);
    max-width: 760px;
  }
}

@media (max-width: 640px) {
  .home-entries {
    grid-template-columns: 1fr;
    max-width: 480px;
  }
  .entry-card { padding: 24px 22px; }
  .entry-desc { min-height: 0; }
}

@media (max-width: 480px) {
  .home-page {
    padding: 24px 16px;
    gap: 32px;
  }
  .home-title { font-size: 24px; }
  .home-logo { width: 56px; height: 56px; font-size: 18px; }
}
</style>
