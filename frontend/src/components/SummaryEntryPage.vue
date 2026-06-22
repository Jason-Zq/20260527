<template>
  <div class="summary-entry-page">
    <!-- 顶栏 -->
    <div class="entry-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回首页
      </el-button>
      <div class="entry-title">
        <span class="title-indicator"></span>
        文件解析
      </div>
      <div class="entry-actions">
        <el-button @click="historyOpen = true" size="small">
          <el-icon style="margin-right: 4px"><Clock /></el-icon>
          历史记录 <el-tag v-if="historyCount" size="small" effect="plain" style="margin-left: 4px">{{ historyCount }}</el-tag>
        </el-button>
      </div>
    </div>

    <!-- URL 输入区 -->
    <div class="url-bar">
      <el-input
        v-model="progressName"
        placeholder="进展名称（必填）"
        size="large"
        clearable
        :disabled="processing"
        @keyup.enter="handleSubmit"
        class="progress-input"
      >
        <template #prefix><el-icon><Flag /></el-icon></template>
      </el-input>
      <el-input
        v-model="url"
        placeholder="粘贴文件 URL（支持 PDF / 图片 / Word）"
        size="large"
        clearable
        :disabled="processing"
        @keyup.enter="handleSubmit"
        class="url-input"
      >
        <template #prefix><el-icon><Link /></el-icon></template>
      </el-input>
      <el-button
        type="primary"
        size="large"
        :loading="processing"
        :disabled="!url.trim() || !progressName.trim()"
        class="submit-btn"
        @click="handleSubmit"
      >
        <el-icon v-if="!processing" style="margin-right: 4px"><MagicStick /></el-icon>
        {{ processing ? `识别中 ${elapsed}s` : '点击识别' }}
      </el-button>
    </div>

    <!-- 进度提示（处理时显示） -->
    <div v-if="processing" class="progress-bar">
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
      </div>
      <span class="progress-label">{{ progressLabel }}</span>
      <span class="progress-tip">总耗时通常 30~200 秒，请耐心等候</span>
    </div>

    <!-- 主体：原文 + 摘要双栏 -->
    <div class="main-content">
      <div v-if="!result && !processing" class="empty-state">
        <el-icon :size="56"><Reading /></el-icon>
        <p class="empty-title">输入文件 URL，开始解析</p>
        <p class="empty-tip">支持 .pdf / .png / .jpg / .jpeg / .bmp / .tiff / .webp / .docx</p>
        <p class="empty-tip">下载上限 50 MB</p>
      </div>

      <div v-else-if="result" class="result-layout">
        <!-- 左：原文 -->
        <div class="result-panel">
          <div class="panel-head">
            <span class="panel-title">
              <el-icon><Document /></el-icon>
              原文（{{ formatNumber(result.char_count) }} 字）
            </span>
            <el-tag size="small" effect="plain">{{ sourceLabel(result.source) }}</el-tag>
            <el-tag size="small" v-if="result.page_count > 1">{{ result.page_count }} 页</el-tag>
          </div>
          <div class="raw-text">{{ result.extracted_text || '（无文本）' }}</div>
        </div>

        <!-- 右：摘要 -->
        <div class="result-panel summary-panel">
          <div class="panel-head">
            <span class="panel-title">
              <el-icon><MagicStick /></el-icon>
              AI 摘要
            </span>
            <el-tag v-if="result.doc_category" size="small" type="warning" effect="plain">
              {{ result.doc_category }}
            </el-tag>
            <span v-if="result.elapsed_sec != null" class="elapsed-tag">
              耗时 {{ result.elapsed_sec }}s
            </span>
          </div>

          <div class="summary-body">
            <!-- 相关性判定卡片（醒目，progress_name 存在且 LLM 给了判断时显示）-->
            <div v-if="result.relevance" class="relevance-card" :class="`rel-${result.relevance}`">
              <div class="rel-head">
                <el-icon class="rel-icon">
                  <CircleCheck v-if="result.relevance === 'strong'" />
                  <Warning v-else-if="result.relevance === 'weak'" />
                  <CircleClose v-else />
                </el-icon>
                <span class="rel-label">{{ relLabel(result.relevance) }}</span>
                <span v-if="result.relevance_score != null" class="rel-score">
                  评分 {{ result.relevance_score }}/100
                </span>
              </div>
              <div class="rel-progress">
                <div class="rel-fill" :style="{ width: (result.relevance_score || 0) + '%' }"></div>
              </div>
              <div class="rel-progress-meta">
                <span class="rel-progress-name">进展：{{ result.progress_name || progressName }}</span>
              </div>
              <p v-if="result.relevance_reason" class="rel-reason">{{ result.relevance_reason }}</p>
            </div>

            <!-- 文件元信息 -->
            <div class="meta-row">
              <span class="meta-k">文件</span>
              <span class="meta-v" :title="result.filename">{{ result.filename || '—' }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-k">URL</span>
              <a :href="result.url" target="_blank" class="meta-v meta-link">{{ result.url }}</a>
            </div>

            <!-- 一句话定性 -->
            <div v-if="result.title" class="title-card">
              <el-icon><Star /></el-icon>
              <span>{{ result.title }}</span>
            </div>

            <!-- 摘要 -->
            <div v-if="result.summary" class="section">
              <div class="section-title">概览</div>
              <p class="section-text">{{ result.summary }}</p>
            </div>

            <!-- 关键要点 -->
            <div v-if="result.key_points && result.key_points.length" class="section">
              <div class="section-title">关键要点</div>
              <ul class="key-points">
                <li v-for="(p, i) in result.key_points" :key="i">{{ p }}</li>
              </ul>
            </div>

            <!-- 操作按钮 -->
            <div class="actions">
              <el-button size="small" @click="copyToClipboard(buildCopyText())">
                <el-icon style="margin-right: 4px"><CopyDocument /></el-icon>
                复制摘要
              </el-button>
              <el-button size="small" @click="downloadAsTxt">
                <el-icon style="margin-right: 4px"><Download /></el-icon>
                下载 .txt
              </el-button>
              <el-button size="small" @click="reset">
                <el-icon style="margin-right: 4px"><RefreshRight /></el-icon>
                解析新文件
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 历史记录抽屉 -->
    <el-drawer
      v-model="historyOpen"
      title="解析历史"
      direction="rtl"
      size="540px"
      @open="loadHistory"
    >
      <div v-if="historyLoading" class="drawer-loading">加载中...</div>
      <div v-else-if="history.length === 0" class="drawer-empty">暂无历史记录</div>
      <div v-else class="history-list">
        <div v-for="h in history" :key="h.id" class="history-card" :class="{ 'is-error': h.status === 'error' }">
          <div class="hc-head">
            <el-tag :type="h.status === 'done' ? 'success' : 'danger'" size="small">
              {{ h.status === 'done' ? '✓' : '✗' }}
            </el-tag>
            <span class="hc-name" :title="h.filename">{{ h.filename || '—' }}</span>
            <el-tag v-if="h.relevance" size="small"
              :type="h.relevance === 'strong' ? 'success' : (h.relevance === 'weak' ? 'warning' : 'danger')">
              {{ relLabel(h.relevance) }}
            </el-tag>
            <el-tag v-if="h.doc_category" size="small" type="warning" effect="plain">{{ h.doc_category }}</el-tag>
            <el-button size="small" link type="danger" class="hc-del" @click="onDelete(h)">删除</el-button>
          </div>
          <div v-if="h.progress_name" class="hc-progress">
            <el-icon size="12"><Flag /></el-icon> {{ h.progress_name }}
          </div>
          <div class="hc-body" v-if="h.status === 'done'">
            <p class="hc-title" v-if="h.title">{{ h.title }}</p>
            <p class="hc-summary">{{ truncate(h.summary, 120) }}</p>
            <div class="hc-meta">
              <span>{{ sourceLabel(h.source) }}</span>
              <span v-if="h.char_count">{{ formatNumber(h.char_count) }} 字</span>
              <span v-if="h.elapsed_sec != null">{{ h.elapsed_sec }}s</span>
              <span class="hc-time">{{ h.created_at }}</span>
            </div>
            <el-button size="small" type="primary" link @click="loadFromHistory(h.id)">查看完整摘要</el-button>
          </div>
          <div class="hc-body" v-else>
            <p class="hc-error">{{ h.error_msg || '处理失败' }}</p>
            <div class="hc-meta">
              <span class="hc-time">{{ h.created_at }}</span>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  ArrowLeft, Link, MagicStick, Reading, Document, Star,
  CopyDocument, Download, RefreshRight, Clock, Flag,
  CircleCheck, CircleClose, Warning,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { summarizeFile, listSummaries, getSummary, deleteSummary } from '../api.js'
import { useRouter } from 'vue-router'

const router = useRouter()
function emit(evt) {
  if (evt === 'back') router.push('/')
}

const url = ref('')
const progressName = ref('')
const result = ref(null)
const processing = ref(false)
const elapsed = ref(0)
let elapsedTimer = null

const historyOpen = ref(false)
const history = ref([])
const historyLoading = ref(false)
const historyCount = ref(0)

// 进度提示按 elapsed 时间分段
const progressLabel = computed(() => {
  const t = elapsed.value
  if (t < 5) return '下载文件中...'
  if (t < 15) return 'OCR / 文本抽取中...'
  return 'AI 分析中...'
})

const progressPercent = computed(() => {
  const t = elapsed.value
  if (t < 5) return 15
  if (t < 15) return 50
  if (t < 60) return 70
  if (t < 120) return 85
  return 95
})

function startElapsed() {
  elapsed.value = 0
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = setInterval(() => { elapsed.value++ }, 1000)
}

function stopElapsed() {
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = null
}

async function handleSubmit() {
  const trimmedUrl = url.value.trim()
  const trimmedProgress = progressName.value.trim()
  if (!trimmedUrl) {
    ElMessage.warning('请填入文件 URL')
    return
  }
  if (!trimmedProgress) {
    ElMessage.warning('请填入进展名称')
    return
  }
  if (!/^https?:\/\//i.test(trimmedUrl)) {
    ElMessage.warning('请输入合法的 http/https URL')
    return
  }

  processing.value = true
  result.value = null
  startElapsed()

  try {
    const data = await summarizeFile(trimmedUrl, trimmedProgress)
    result.value = data
    if (data.relevance === 'unrelated') {
      ElMessage.warning(`解析完成，但 AI 判定文件与"${trimmedProgress}"不相关`)
    } else if (data.relevance === 'weak') {
      ElMessage.warning(`解析完成，文件与进展弱相关（${data.relevance_score}/100）`)
    } else {
      ElMessage.success(`解析完成（耗时 ${data.elapsed_sec}s）`)
    }
    historyCount.value++
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '解析失败'
    ElMessage.error('解析失败：' + msg)
  } finally {
    processing.value = false
    stopElapsed()
  }
}

function reset() {
  result.value = null
  url.value = ''
  // progressName 保留，方便用户连续给同一进展上传多份文件
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await listSummaries(100, 0)
    history.value = data.items || []
    historyCount.value = history.value.length
  } catch (err) {
    ElMessage.error('加载历史失败：' + (err.response?.data?.detail || err.message))
  } finally {
    historyLoading.value = false
  }
}

async function loadFromHistory(summaryId) {
  try {
    const data = await getSummary(summaryId)
    result.value = data
    url.value = data.url
    progressName.value = data.progress_name || ''
    historyOpen.value = false
    ElMessage.success('已加载历史记录')
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  }
}

async function onDelete(h) {
  try {
    await deleteSummary(h.id)
    history.value = history.value.filter(x => x.id !== h.id)
    historyCount.value = Math.max(0, historyCount.value - 1)
    ElMessage.success('已删除')
  } catch (err) {
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
  }
}

// utils
function sourceLabel(src) {
  return {
    pdf_text: 'PDF（文字型）',
    pdf_ocr: 'PDF（图片 OCR）',
    image_ocr: '图片 OCR',
    docx_text: 'Word 文档',
  }[src] || src || '—'
}

function relLabel(r) {
  return {
    strong: '✓ 强相关',
    weak: '⚠ 弱相关',
    unrelated: '✗ 不相关',
  }[r] || r
}

function formatNumber(n) {
  return n != null ? Number(n).toLocaleString('zh-CN') : '—'
}

function truncate(s, n) {
  s = String(s || '')
  return s.length > n ? s.slice(0, n) + '...' : s
}

function buildCopyText() {
  if (!result.value) return ''
  const r = result.value
  const lines = []
  if (r.progress_name) lines.push(`进展：${r.progress_name}`)
  if (r.title) lines.push(`【${r.title}】`)
  if (r.doc_category) lines.push(`分类：${r.doc_category}`)
  if (r.relevance) {
    lines.push(`相关性：${relLabel(r.relevance)}（${r.relevance_score || 0}/100）`)
    if (r.relevance_reason) lines.push(`理由：${r.relevance_reason}`)
  }
  if (r.summary) lines.push('', '概览：', r.summary)
  if (r.key_points && r.key_points.length) {
    lines.push('', '关键要点：')
    for (const p of r.key_points) lines.push(`• ${p}`)
  }
  if (r.url) lines.push('', `来源：${r.url}`)
  return lines.join('\n')
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

function downloadAsTxt() {
  if (!result.value) return
  const text = buildCopyText()
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objUrl
  a.download = (result.value.filename || 'summary') + '_摘要.txt'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(objUrl)
}

onMounted(async () => {
  // 加载历史数量（不展开抽屉）
  try {
    const data = await listSummaries(100, 0)
    historyCount.value = (data.items || []).length
  } catch (_) {}
})

onUnmounted(stopElapsed)
</script>

<style scoped>
.summary-entry-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  overflow: hidden;
}

/* 顶栏 */
.entry-header {
  padding: 0 24px;
  height: 56px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
  border-bottom: 1px solid #e8ebf5;
}
.back-btn {
  background: #f1f5f9 !important;
  border: 1px solid #e2e8f0 !important;
  color: #475569 !important;
  border-radius: 8px !important;
}
.back-btn:hover { background: #e2e8f0 !important; color: #f59e0b !important; }
.entry-title { font-size: 16px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 10px; }
.title-indicator {
  width: 3px; height: 16px;
  background: linear-gradient(180deg, #fb923c, #f59e0b);
  border-radius: 2px;
}
.entry-actions { margin-left: auto; }

/* URL 输入条 */
.url-bar {
  flex-shrink: 0;
  padding: 18px 24px 12px;
  background: #fff;
  border-bottom: 1px solid #e8ebf5;
  display: flex;
  gap: 12px;
}
.url-input { flex: 1; }
.progress-input {
  flex-shrink: 0;
  width: 280px;
}
.url-input :deep(.el-input__wrapper),
.progress-input :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #cbd5e1 inset !important;
  padding: 0 14px;
  height: 44px;
}
.url-input :deep(.el-input__wrapper.is-focus),
.progress-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 2px #fb923c inset !important;
}
.url-input :deep(.el-input__inner),
.progress-input :deep(.el-input__inner) { height: 44px; font-size: 14px; }
.submit-btn {
  height: 44px;
  background: linear-gradient(135deg, #fb923c, #f59e0b) !important;
  border: none !important;
  font-weight: 600 !important;
  padding: 0 24px !important;
}
.submit-btn:hover { box-shadow: 0 4px 14px rgba(251, 146, 60, 0.4) !important; }

/* 进度条 */
.progress-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 24px;
  background: #fff;
  border-bottom: 1px solid #e8ebf5;
}
.progress-track {
  flex: 1;
  height: 4px;
  background: #e2e8f0;
  border-radius: 2px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #fb923c, #f59e0b);
  border-radius: 2px;
  transition: width 0.5s ease;
  box-shadow: 0 0 8px rgba(251, 146, 60, 0.4);
}
.progress-label {
  font-size: 13px;
  color: #f59e0b;
  font-weight: 600;
  white-space: nowrap;
}
.progress-tip {
  font-size: 12px;
  color: #94a3b8;
  white-space: nowrap;
}

/* 主体 */
.main-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  padding: 14px 18px;
}

.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
  gap: 8px;
}
.empty-state :deep(.el-icon) { color: #cbd5e1; margin-bottom: 8px; }
.empty-title { font-size: 16px; color: #475569; margin: 0; font-weight: 500; }
.empty-tip { font-size: 13px; margin: 0; }

/* 双栏 */
.result-layout {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  min-height: 0;
}

.result-panel {
  background: #fff;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.panel-head {
  flex-shrink: 0;
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 6px;
}
.elapsed-tag {
  margin-left: auto;
  font-size: 11px;
  color: #94a3b8;
}

/* 原文 */
.raw-text {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  font-size: 13px;
  line-height: 1.8;
  color: #475569;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

/* 摘要 */
.summary-panel { background: linear-gradient(180deg, #fffbeb 0%, #ffffff 30%); }
.summary-body { flex: 1; overflow-y: auto; padding: 16px 20px; }

/* 相关性判定卡片 */
.relevance-card {
  margin-bottom: 16px;
  padding: 14px 16px;
  border-radius: 10px;
  border-left: 4px solid;
}
.relevance-card.rel-strong {
  background: #ecfdf5;
  border-left-color: #10b981;
}
.relevance-card.rel-weak {
  background: #fffbeb;
  border-left-color: #f59e0b;
}
.relevance-card.rel-unrelated {
  background: #fef2f2;
  border-left-color: #ef4444;
}
.rel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.rel-icon {
  font-size: 22px;
}
.rel-strong .rel-icon { color: #10b981; }
.rel-weak .rel-icon { color: #f59e0b; }
.rel-unrelated .rel-icon { color: #ef4444; }
.rel-label {
  font-size: 15px;
  font-weight: 700;
}
.rel-strong .rel-label { color: #065f46; }
.rel-weak .rel-label { color: #78350f; }
.rel-unrelated .rel-label { color: #991b1b; }
.rel-score {
  margin-left: auto;
  font-size: 12px;
  color: #475569;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-weight: 600;
}
.rel-progress {
  height: 6px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 6px;
}
.rel-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease;
}
.rel-strong .rel-fill { background: linear-gradient(90deg, #10b981, #34d399); }
.rel-weak .rel-fill { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
.rel-unrelated .rel-fill { background: linear-gradient(90deg, #ef4444, #f87171); }
.rel-progress-meta {
  display: flex;
  font-size: 11px;
  color: #94a3b8;
  margin-bottom: 8px;
}
.rel-progress-name {
  font-weight: 500;
  color: #475569;
}
.rel-reason {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #1e293b;
}
.rel-strong .rel-reason { color: #065f46; }
.rel-weak .rel-reason { color: #78350f; }
.rel-unrelated .rel-reason { color: #991b1b; }

.meta-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 6px;
  align-items: baseline;
}
.meta-k {
  flex-shrink: 0;
  width: 36px;
  color: #94a3b8;
  font-weight: 500;
}
.meta-v {
  color: #475569;
  word-break: break-all;
  flex: 1;
}
.meta-link { color: #f59e0b; text-decoration: none; }
.meta-link:hover { text-decoration: underline; }

.title-card {
  margin: 14px 0 16px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #fef3c7, #fed7aa);
  border-radius: 10px;
  border-left: 3px solid #f59e0b;
  font-size: 14px;
  font-weight: 600;
  color: #78350f;
  display: flex;
  align-items: center;
  gap: 8px;
}

.section { margin-bottom: 18px; }
.section-title {
  font-size: 12px;
  font-weight: 700;
  color: #f59e0b;
  letter-spacing: 1px;
  margin-bottom: 8px;
  text-transform: uppercase;
}
.section-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
  color: #1e293b;
  white-space: pre-wrap;
}

.key-points {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.key-points li {
  position: relative;
  padding: 6px 12px 6px 22px;
  font-size: 13px;
  color: #1e293b;
  background: #f8fafc;
  border-radius: 6px;
  line-height: 1.6;
}
.key-points li::before {
  content: '▸';
  position: absolute;
  left: 8px;
  color: #f59e0b;
  font-weight: 700;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 14px;
  margin-top: 14px;
  border-top: 1px dashed #e2e8f0;
}

/* drawer */
.drawer-loading, .drawer-empty {
  text-align: center;
  color: #94a3b8;
  padding: 60px 20px;
  font-size: 13px;
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 4px;
}

.history-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 12px 14px;
}
.history-card.is-error {
  background: #fef2f2;
  border-color: #fecaca;
}
.hc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.hc-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hc-del { margin-left: auto; }
.hc-progress {
  font-size: 11px;
  color: #f59e0b;
  margin-bottom: 6px;
  padding: 3px 8px;
  background: #fef3c7;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}
.hc-title {
  font-size: 13px;
  font-weight: 600;
  color: #78350f;
  margin: 0 0 6px;
}
.hc-summary {
  font-size: 12px;
  line-height: 1.6;
  color: #475569;
  margin: 0 0 8px;
}
.hc-error {
  font-size: 12px;
  color: #b91c1c;
  margin: 0 0 8px;
}
.hc-meta {
  font-size: 11px;
  color: #94a3b8;
  display: flex;
  gap: 10px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.hc-time { margin-left: auto; }
</style>
