<template>
  <div class="split-entry-page">
    <!-- 子页顶栏 -->
    <div class="entry-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回首页
      </el-button>
      <div class="entry-title">
        <span class="title-indicator"></span>
        处理超长PDF文件
      </div>
      <div class="entry-actions">
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".pdf"
          :on-change="handleFileSelect"
        >
          <el-button size="small" :loading="processing" class="upload-btn">
            <el-icon v-if="!processing" style="margin-right: 4px"><Upload /></el-icon>
            {{ processing ? '处理中...' : '上传 PDF' }}
          </el-button>
        </el-upload>
        <el-button
          size="small"
          class="history-btn"
          @click="openHistory"
        >
          <el-icon style="margin-right: 4px"><Clock /></el-icon>
          记录
        </el-button>
        <el-button
          v-if="result"
          size="small"
          class="download-all-btn"
          @click="downloadAll"
        >
          <el-icon style="margin-right: 4px"><Download /></el-icon>
          打包下载全部
        </el-button>
        <el-button v-if="result" @click="resetState" size="small" class="reset-btn">
          重新上传
        </el-button>
      </div>
    </div>

    <!-- 处理中:进度条 -->
    <div v-if="processing" class="progress-bar">
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
      </div>
      <span class="progress-label">{{ progressText }}</span>
      <span class="elapsed-label">已耗时 {{ formatElapsed(elapsedSec) }}</span>
    </div>

    <!-- 主体 -->
    <div class="split-body">
      <!-- 空状态 -->
      <div v-if="!processing && !result" class="empty-state">
        <div class="empty-icon">
          <el-icon :size="64"><Files /></el-icon>
        </div>
        <h3 class="empty-title">上传一份多证件 PDF</h3>
        <p class="empty-desc">
          系统会自动识别每页证件类型(身份证、户口本、护照、配偶身份证等),
          按页拆分为独立 PDF 供你单独下载或打包下载。
        </p>
        <p class="empty-hint">仅支持 .pdf 文件。处理时间取决于页数和 OCR 速度,通常 30s~3min。</p>
      </div>

      <!-- 结果列表 -->
      <div v-else-if="result" class="result-pane">
        <div class="result-summary">
          <div class="summary-item">
            <span class="summary-label">原始页数</span>
            <span class="summary-value">{{ result.total_pages }} 页</span>
          </div>
          <div class="summary-divider"></div>
          <div class="summary-item">
            <span class="summary-label">拆分文件数</span>
            <span class="summary-value">{{ result.ranges.length }} 份</span>
          </div>
          <div class="summary-divider"></div>
          <div class="summary-item">
            <span class="summary-label">任务编号</span>
            <span class="summary-value mono">{{ result.task_id }}</span>
          </div>
          <template v-if="lastDurationSec != null">
            <div class="summary-divider"></div>
            <div class="summary-item">
              <span class="summary-label">本次耗时</span>
              <span class="summary-value">{{ formatElapsed(lastDurationSec) }}</span>
            </div>
          </template>
        </div>

        <el-table
          :data="result.ranges"
          stripe
          class="split-table"
          empty-text="未生成任何拆分文件"
        >
          <el-table-column label="序号" type="index" width="60" align="center" />
          <el-table-column label="证件类型" width="140">
            <template #default="{ row }">
              <el-tag :type="docTypeTagType(row.doc_type)" effect="light" size="small">
                {{ row.doc_type }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="页码范围" width="140" align="center">
            <template #default="{ row }">
              <span v-if="row.page_start === row.page_end">第 {{ row.page_start }} 页</span>
              <span v-else>第 {{ row.page_start }} - {{ row.page_end }} 页</span>
            </template>
          </el-table-column>
          <el-table-column label="文件名" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="filename">{{ row.filename }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="180" align="center" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                type="primary"
                link
                @click="openPreview(row)"
              >
                <el-icon style="margin-right: 4px"><View /></el-icon>
                预览
              </el-button>
              <el-button
                size="small"
                type="primary"
                link
                @click="downloadOne(row.idx)"
              >
                <el-icon style="margin-right: 4px"><Download /></el-icon>
                下载
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 预览弹窗:显示该 range 覆盖的页面 PNG -->
        <el-dialog
          v-model="previewVisible"
          :title="previewTitle"
          width="860px"
          top="5vh"
          append-to-body
          destroy-on-close
        >
          <div class="preview-body">
            <div v-if="!previewImages.length" class="preview-empty">
              暂无可预览的页面图片
            </div>
            <div
              v-for="(img, i) in previewImages"
              :key="img.page"
              class="preview-page"
            >
              <div class="preview-page-label">第 {{ img.page }} 页</div>
              <img
                v-if="!img.failed"
                :src="img.url"
                :alt="`第 ${img.page} 页`"
                class="preview-page-img"
                @error="onPreviewImgError(i)"
              />
              <div v-else class="preview-page-failed">
                第 {{ img.page }} 页图片加载失败
              </div>
            </div>
          </div>
        </el-dialog>
      </div>
    </div>

    <!-- 历史记录抽屉:从右侧滑出,显示所有持久化的拆分任务 -->
    <el-drawer
      v-model="historyVisible"
      title="拆分记录"
      direction="rtl"
      size="60%"
      :destroy-on-close="false"
    >
      <div class="history-toolbar">
        <span class="history-summary">
          共 {{ historyList.length }} 条记录
          <span v-if="historyCleanedCount > 0" class="history-cleaned-hint">
            (其中 {{ historyCleanedCount }} 条文件已超期清理)
          </span>
        </span>
        <el-button size="small" @click="loadHistory" :loading="historyLoading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>

      <el-table
        :data="historyList"
        v-loading="historyLoading"
        stripe
        empty-text="暂无历史记录"
      >
        <el-table-column label="文件名" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="filename">{{ row.filename }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="historyStatusTag(row)" size="small" effect="light">
              {{ historyStatusLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="页数" width="64" align="center" prop="total_pages" />
        <el-table-column label="拆出" width="64" align="center" prop="files_count" />
        <el-table-column label="耗时" width="80" align="center">
          <template #default="{ row }">
            <span v-if="row.duration_sec != null">{{ row.duration_sec.toFixed(1) }}s</span>
            <span v-else class="dim">-</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            <span class="dim mono">{{ row.created_at }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              :disabled="row.status !== 'done'"
              @click="loadHistoryItem(row)"
            >
              查看
            </el-button>
            <el-popconfirm
              title="确认彻底删除该任务及其文件?"
              @confirm="removeHistoryItem(row)"
            >
              <template #reference>
                <el-button size="small" type="danger" link>
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { ArrowLeft, Upload, Download, Files, View, Clock, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  uploadSplitPdf,
  pollSplit,
  downloadSplitUrl,
  downloadSplitAllUrl,
  listSplitHistory,
  deleteSplitHistory,
} from '../api.js'

const emit = defineEmits(['back'])

const processing = ref(false)
const result = ref(null)
const progressText = ref('')
let pollTimer = null

// 计时器:让用户看到实时耗时,避免误以为页面卡死
const elapsedSec = ref(0)
const lastDurationSec = ref(null)
let startTime = null
let tickTimer = null

/**
 * 把秒数格式化为 "23 秒" 或 "1 分 12 秒"。
 * @param {number} sec
 * @returns {string}
 */
function formatElapsed(sec) {
  if (sec == null || sec < 0) return '0 秒'
  if (sec < 60) return `${sec} 秒`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return s === 0 ? `${m} 分` : `${m} 分 ${s} 秒`
}

function startTicker() {
  stopTicker(false)
  startTime = Date.now()
  elapsedSec.value = 0
  tickTimer = setInterval(() => {
    if (startTime != null) {
      elapsedSec.value = Math.floor((Date.now() - startTime) / 1000)
    }
  }, 1000)
}

/**
 * 停掉 1s 节拍器。
 * @param {boolean} commitAsLast 是否把当前 elapsed 写入 lastDurationSec(仅 done 时为 true)
 */
function stopTicker(commitAsLast) {
  if (tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
  if (commitAsLast) {
    lastDurationSec.value = elapsedSec.value
  }
  startTime = null
}

const progressPercent = computed(() => {
  const s = progressText.value || ''
  if (s.includes('OCR') || s.includes('识别')) return 33
  if (s.includes('分析') || s.includes('LLM') || s.includes('边界')) return 66
  if (s.includes('拆分')) return 90
  return 8
})

/**
 * 文件选择回调。立即上传并开始轮询。
 * @param {{raw: File}} uploadFile
 */
async function handleFileSelect(uploadFile) {
  const file = uploadFile?.raw
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.warning('仅支持 .pdf 文件')
    return
  }

  resetState()
  processing.value = true
  progressText.value = 'OCR 识别中...'

  try {
    const { task_id } = await uploadSplitPdf(file)
    startTicker()
    startPolling(task_id)
  } catch (err) {
    processing.value = false
    ElMessage.error('上传失败：' + (err.response?.data?.detail || err.message))
  }
}

/**
 * 启动 1.5s 轮询,直到 status=done 或 error。
 * @param {string} taskId
 */
function startPolling(taskId) {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const s = await pollSplit(taskId)
      if (s.status === 'done') {
        clearInterval(pollTimer)
        pollTimer = null
        stopTicker(true)
        processing.value = false
        result.value = s.result
        ElMessage.success(`已拆分为 ${s.result?.ranges?.length || 0} 份 PDF`)
        return
      }
      if (s.status === 'error') {
        clearInterval(pollTimer)
        pollTimer = null
        stopTicker(false)
        processing.value = false
        ElMessage.error('处理失败：' + (s.error || '未知错误'))
        return
      }
      // 进度文案
      if (s.status === 'ocr')       progressText.value = s.progress || 'OCR 识别中...'
      else if (s.status === 'llm')      progressText.value = 'LLM 分析页边界...'
      else if (s.status === 'splitting') progressText.value = '正在拆分 PDF...'
    } catch (err) {
      clearInterval(pollTimer)
      pollTimer = null
      stopTicker(false)
      processing.value = false
      ElMessage.error('轮询失败：' + (err.response?.data?.detail || err.message))
    }
  }, 1500)
}

function downloadOne(idx) {
  if (!result.value) return
  window.open(downloadSplitUrl(result.value.task_id, idx), '_blank')
}

// ==== 预览 ====
const previewVisible = ref(false)
const previewTitle = ref('')
const previewImages = ref([])  // [{page: 1, url: '/uploads/.../page_1.png'}]

/**
 * 打开预览弹窗:展示该 range 覆盖页码对应的 PNG。
 * 拆分流水线在 OCR 阶段会把每页渲染到 output/{task_id}/images/page_{N}.png。
 * @param {object} row 表格行数据,含 page_start/page_end/doc_type
 */
function openPreview(row) {
  if (!result.value || !row) return
  const taskId = result.value.task_id
  const start = row.page_start
  const end = row.page_end
  const imgs = []
  for (let p = start; p <= end; p++) {
    imgs.push({
      page: p,
      url: `/uploads/${encodeURIComponent(taskId)}/images/page_${p}.png`,
    })
  }
  previewImages.value = imgs
  const rangeLabel = start === end ? `第 ${start} 页` : `第 ${start} - ${end} 页`
  previewTitle.value = `预览 - ${row.doc_type}(${rangeLabel})`
  previewVisible.value = true
}

/**
 * 图片加载失败兜底:替换为占位提示,避免显示破图标。
 * @param {number} idx
 */
function onPreviewImgError(idx) {
  const img = previewImages.value[idx]
  if (img) {
    img.url = ''
    img.failed = true
  }
}

// ==== 历史记录 ====
const historyVisible = ref(false)
const historyLoading = ref(false)
const historyList = ref([])

const historyCleanedCount = computed(
  () => historyList.value.filter((r) => r.files_cleaned).length
)

/**
 * 打开历史抽屉,首次展开时拉数据。
 */
function openHistory() {
  historyVisible.value = true
  loadHistory()
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await listSplitHistory(200)
    historyList.value = data.history || []
  } catch (err) {
    ElMessage.error('加载历史失败:' + (err.response?.data?.detail || err.message))
  } finally {
    historyLoading.value = false
  }
}

/**
 * 把历史记录里的 ranges 加载回主表格,等同"复现一次拆分结果"。
 * 已被 7 天清理的不允许查看(下载和预览都会失败)。
 * @param {object} row
 */
function loadHistoryItem(row) {
  if (row.status !== 'done') return
  if (row.files_cleaned) {
    ElMessage.warning('该任务文件已超过 7 天保留期被清理,无法查看')
    return
  }
  result.value = {
    task_id: row.task_id,
    total_pages: row.total_pages,
    ranges: row.ranges || [],
  }
  lastDurationSec.value = row.duration_sec != null ? Math.round(row.duration_sec) : null
  historyVisible.value = false
  ElMessage.success(`已载入历史任务 ${row.task_id}`)
}

async function removeHistoryItem(row) {
  try {
    await deleteSplitHistory(row.task_id)
    ElMessage.success('已删除')
    // 如果当前主表显示的就是这条,清掉
    if (result.value && result.value.task_id === row.task_id) {
      resetState()
    }
    await loadHistory()
  } catch (err) {
    ElMessage.error('删除失败:' + (err.response?.data?.detail || err.message))
  }
}

/**
 * 历史行状态标签文案。
 * @param {object} row
 */
function historyStatusLabel(row) {
  if (row.files_cleaned && row.status === 'done') return '已清理'
  const map = { ocr: 'OCR中', llm: '分析中', splitting: '拆分中', done: '已完成', error: '失败' }
  return map[row.status] || row.status
}

/**
 * 历史行状态 tag type。
 * @param {object} row
 */
function historyStatusTag(row) {
  if (row.files_cleaned && row.status === 'done') return 'info'
  if (row.status === 'done') return 'success'
  if (row.status === 'error') return 'danger'
  return 'warning'
}

function downloadAll() {
  if (!result.value) return
  window.open(downloadSplitAllUrl(result.value.task_id), '_blank')
}

function resetState() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  stopTicker(false)
  elapsedSec.value = 0
  lastDurationSec.value = null
  processing.value = false
  result.value = null
  progressText.value = ''
}

/**
 * 根据证件类型给 el-tag 选择颜色。
 * @param {string} docType
 * @returns {string}
 */
function docTypeTagType(docType) {
  if (docType === '未知') return 'info'
  if (docType === '身份证' || docType === '护照') return 'primary'
  if (docType === '户口本' || docType === '结婚证') return 'success'
  return 'warning'
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  stopTicker(false)
})
</script>

<style scoped>
.split-entry-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  color: #1e293b;
}

.entry-header {
  padding: 0 24px;
  height: 56px;
  background: #ffffff;
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
  font-weight: 500 !important;
  border-radius: 8px !important;
}

.back-btn:hover {
  background: #e2e8f0 !important;
  color: #10b981 !important;
}

.entry-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-indicator {
  width: 3px;
  height: 16px;
  background: linear-gradient(180deg, #10b981, #06b6d4);
  border-radius: 2px;
}

.entry-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.upload-btn {
  background: linear-gradient(135deg, #10b981, #06b6d4) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
  padding: 8px 18px !important;
  transition: all 0.25s !important;
}

.upload-btn:hover {
  box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4) !important;
  transform: translateY(-1px);
}

.download-all-btn {
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
}

.download-all-btn:hover {
  box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4) !important;
  transform: translateY(-1px);
}

.reset-btn {
  background: #f1f5f9 !important;
  border: 1px solid #e2e8f0 !important;
  color: #475569 !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
}

.reset-btn:hover {
  background: #e2e8f0 !important;
  color: #10b981 !important;
}

/* 进度条 */
.progress-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 24px;
  background: #ffffff;
  flex-shrink: 0;
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
  background: linear-gradient(90deg, #10b981, #06b6d4);
  border-radius: 2px;
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.3);
  transition: width 0.5s ease;
}

.progress-label {
  font-size: 13px;
  color: #10b981;
  font-weight: 500;
  white-space: nowrap;
}

.elapsed-label {
  font-size: 12px;
  color: #94a3b8;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  white-space: nowrap;
}

/* 主体 */
.split-body {
  flex: 1;
  overflow: auto;
  padding: 16px 24px;
}

/* 空状态 */
.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px;
}

.empty-icon {
  width: 120px;
  height: 120px;
  border-radius: 24px;
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(6, 182, 212, 0.1));
  color: #10b981;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 24px;
}

.empty-title {
  margin: 0 0 12px;
  font-size: 22px;
  font-weight: 700;
  color: #1e293b;
}

.empty-desc {
  margin: 0 0 8px;
  max-width: 520px;
  font-size: 14px;
  line-height: 1.6;
  color: #475569;
}

.empty-hint {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
}

/* 结果区 */
.result-pane {
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.06), rgba(6, 182, 212, 0.06));
  border-radius: 10px;
  border: 1px solid rgba(16, 185, 129, 0.15);
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.summary-label {
  font-size: 11px;
  color: #64748b;
  font-weight: 500;
}

.summary-value {
  font-size: 15px;
  color: #1e293b;
  font-weight: 700;
}

.summary-value.mono {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 13px;
}

.summary-divider {
  width: 1px;
  height: 28px;
  background: rgba(16, 185, 129, 0.2);
}

.split-table {
  width: 100%;
}

.filename {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 12px;
  color: #1e293b;
}

/* 预览弹窗 */
.preview-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 75vh;
  overflow: auto;
}

.preview-empty {
  text-align: center;
  color: #94a3b8;
  padding: 40px 0;
  font-size: 13px;
}

.preview-page {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid #e8ebf5;
  border-radius: 8px;
  background: #fafbfd;
}

.preview-page-label {
  font-size: 12px;
  color: #64748b;
  font-weight: 600;
}

.preview-page-img {
  width: 100%;
  height: auto;
  display: block;
  border-radius: 4px;
  background: #fff;
}

.preview-page-failed {
  padding: 24px;
  text-align: center;
  color: #ef4444;
  font-size: 13px;
  background: #fef2f2;
  border-radius: 4px;
}

/* 历史按钮 */
.history-btn {
  background: #f1f5f9 !important;
  border: 1px solid #e2e8f0 !important;
  color: #475569 !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
}

.history-btn:hover {
  background: #e2e8f0 !important;
  color: #6366f1 !important;
}

/* 历史抽屉 */
.history-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding: 0 4px;
}

.history-summary {
  font-size: 13px;
  color: #475569;
}

.history-cleaned-hint {
  margin-left: 8px;
  color: #94a3b8;
  font-size: 12px;
}

.dim {
  color: #94a3b8;
}

.mono {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 12px;
}
</style>
