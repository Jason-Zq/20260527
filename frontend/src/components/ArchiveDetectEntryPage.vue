<template>
  <div class="archive-detect-page">
    <!-- 顶栏：仅标题 -->
    <div class="entry-header">
      <div class="entry-title">
        <span class="title-indicator"></span>
        文件留底检测
      </div>
    </div>

    <!-- 主体 -->
    <div class="main-scroll">
      <!-- 1. 提示词输入区 -->
      <section class="card">
        <div class="card-head">
          <el-icon><EditPen /></el-icon>
          <span>判定提示词（可编辑，将拼接到 AI 识别中）</span>
        </div>
        <el-input
          v-model="userPrompt"
          type="textarea"
          :rows="3"
          :autosize="{ minRows: 3, maxRows: 8 }"
          placeholder="例：帮我检测文件是否是 XXX 客户 XXX 项目的 XXX 进展（留底）文件"
          :disabled="submitting"
        />
        <div class="hint">
          请把模板中的 XXX 替换为客户、项目、进展的具体名称。AI 会按你的描述判定每个文件是否符合留底要求。
        </div>
      </section>

      <!-- 2. 来源切换 -->
      <section class="card">
        <div class="card-head">
          <el-icon><Files /></el-icon>
          <span>文件来源</span>
        </div>
        <el-radio-group v-model="sourceKind" :disabled="submitting" size="default">
          <el-radio-button label="upload">上传文件</el-radio-button>
          <el-radio-button label="url">输入文件地址</el-radio-button>
        </el-radio-group>

        <!-- upload 模式 -->
        <div v-if="sourceKind === 'upload'" class="source-pane">
          <el-upload
            v-model:file-list="uploadFiles"
            multiple
            :auto-upload="false"
            :limit="MAX_FILES"
            accept=".pdf,.png,.jpg,.jpeg,.bmp,.tiff,.webp,.docx"
            :on-exceed="onExceedUpload"
            drag
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-text">点击或拖拽文件到此处</div>
            <div class="upload-tip">最多 {{ MAX_FILES }} 个文件 · 单文件 ≤50MB · 支持 PDF / 图片 / Word</div>
          </el-upload>
        </div>

        <!-- url 模式：动态行输入 -->
        <div v-else class="source-pane">
          <div class="url-rows">
            <div
              v-for="(row, i) in urlRows"
              :key="row.id"
              class="url-row"
              :class="{ 'has-error': row.invalid }"
            >
              <span class="url-row-label">文件地址 {{ i + 1 }}</span>
              <el-input
                v-model="row.value"
                placeholder="https://...（仅支持 http/https）"
                :disabled="submitting"
                clearable
                @input="row.invalid = false"
                @paste="(e) => onUrlPaste(e, i)"
              />
              <el-button
                class="url-row-del"
                :disabled="submitting || urlRows.length <= 1"
                circle
                size="small"
                @click="removeUrlRow(i)"
              >
                <el-icon><Close /></el-icon>
              </el-button>
            </div>
          </div>
          <div class="url-row-actions">
            <el-button
              size="small"
              :disabled="submitting || urlRows.length >= MAX_FILES"
              @click="addUrlRow()"
            >
              <el-icon style="margin-right: 4px"><Plus /></el-icon>
              添加文件地址
            </el-button>
            <span class="hint url-counter">
              当前 <b>{{ filledUrlCount }}</b>/{{ MAX_FILES }}
              <span v-if="urlRows.length >= MAX_FILES" class="warn">（已达上限）</span>
            </span>
          </div>
        </div>

        <div class="submit-row">
          <el-button
            type="primary"
            size="large"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="handleSubmit"
          >
            <el-icon v-if="!submitting" style="margin-right: 4px"><MagicStick /></el-icon>
            {{ submitting ? '提交中...' : '开始检测' }}
          </el-button>
        </div>
      </section>

      <!-- 3. 结果列表（一文件一卡） -->
      <section v-if="batch" class="card">
        <div class="card-head">
          <el-icon><Reading /></el-icon>
          <span>检测结果</span>
          <el-tag
            :type="batch.status === 'done' ? 'success' : 'warning'"
            size="small"
            style="margin-left: 8px"
          >
            {{ batch.status === 'done' ? '全部完成' : `进行中 ${batch.done_files}/${batch.total_files}` }}
          </el-tag>
        </div>

        <div class="result-grid">
          <div
            v-for="f in batch.files"
            :key="f.idx"
            class="file-card"
            :class="`status-${f.status}`"
          >
            <div class="fc-head">
              <span class="fc-name" :title="f.filename || f.source_url">
                {{ f.filename || f.source_url || '—' }}
              </span>
              <el-tag size="small" :type="statusTagType(f.status)">{{ statusLabel(f.status) }}</el-tag>
            </div>

            <!-- 进行中：显示 spinner + 阶段说明 -->
            <div v-if="['pending','fetching','ocr','llm'].includes(f.status)" class="fc-progress">
              <el-icon class="spin"><Loading /></el-icon>
              <span>{{ stageLabel(f.status) }}</span>
            </div>

            <!-- 完成：展示判定结论 -->
            <div v-else-if="f.status === 'done'" class="fc-body">
              <div class="verdict" :class="f.is_archival ? 'pass' : 'fail'">
                <el-icon>
                  <CircleCheck v-if="f.is_archival" />
                  <CircleClose v-else />
                </el-icon>
                <span class="verdict-text">{{ f.is_archival ? '符合留底要求' : '不符合留底要求' }}</span>
                <span class="verdict-score">置信度 {{ f.confidence }}/100</span>
              </div>

              <div class="confidence-track">
                <div class="confidence-fill" :style="{ width: (f.confidence || 0) + '%' }"></div>
              </div>

              <div class="meta-row">
                <el-tag v-if="f.doc_category" size="small" type="warning" effect="plain">{{ f.doc_category }}</el-tag>
                <el-tag v-if="f.page_count" size="small" effect="plain">{{ f.page_count }} 页</el-tag>
                <el-tag v-if="f.elapsed_sec != null" size="small" effect="plain">{{ f.elapsed_sec }}s</el-tag>
              </div>

              <div v-if="f.reason" class="reason">
                <div class="section-title">判断依据</div>
                <p class="reason-text">{{ f.reason }}</p>
              </div>

              <div v-if="f.key_points && f.key_points.length" class="key-points">
                <div class="section-title">关键要点</div>
                <ul>
                  <li v-for="(p, i) in f.key_points" :key="i">{{ p }}</li>
                </ul>
              </div>
            </div>

            <!-- 错误 -->
            <div v-else-if="f.status === 'error'" class="fc-error">
              <el-icon><Warning /></el-icon>
              <span>{{ f.error_msg || '处理失败' }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 空状态 -->
      <section v-else-if="!submitting" class="card empty">
        <el-icon :size="48"><Reading /></el-icon>
        <p class="empty-title">填写判定提示词，选择文件或输入文件地址，开始检测</p>
        <p class="empty-sub">检测结果中的金额、电话、身份证、银行卡等敏感信息会自动脱敏</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import {
  EditPen, UploadFilled, Files, Reading, MagicStick,
  CircleCheck, CircleClose, Warning, Loading, Plus, Close,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  submitArchiveDetectUpload,
  submitArchiveDetectUrls,
  pollArchiveDetect,
} from '../api.js'

const MAX_FILES = 20
const DEFAULT_PROMPT = '帮我检测文件是否是 XXX 客户 XXX 项目的 XXX 进展（留底）文件'

const userPrompt = ref(DEFAULT_PROMPT)
const sourceKind = ref('upload')         // 'upload' | 'url'
const uploadFiles = ref([])              // el-upload v-model 接管

// 动态行：每个对象 {id, value, invalid}；初始 1 行空
let _rowSeq = 0
function makeRow(value = '') {
  return { id: ++_rowSeq, value, invalid: false }
}
const urlRows = ref([makeRow()])

const submitting = ref(false)
const batch = ref(null)
let pollTimer = null

const filledUrlCount = computed(
  () => urlRows.value.filter((r) => r.value.trim()).length,
)

const canSubmit = computed(() => {
  if (submitting.value) return false
  if (!userPrompt.value.trim()) return false
  if (sourceKind.value === 'upload') {
    return uploadFiles.value.length > 0 && uploadFiles.value.length <= MAX_FILES
  }
  return filledUrlCount.value > 0 && urlRows.value.length <= MAX_FILES
})

function onExceedUpload() {
  ElMessage.warning(`最多 ${MAX_FILES} 个文件`)
}

function addUrlRow() {
  if (urlRows.value.length >= MAX_FILES) {
    ElMessage.warning(`最多 ${MAX_FILES} 个文件地址`)
    return
  }
  urlRows.value.push(makeRow())
}

function removeUrlRow(i) {
  if (urlRows.value.length <= 1) return
  urlRows.value.splice(i, 1)
}

/**
 * 粘贴增强：当用户在某行粘贴包含换行的文本时，按行拆分：
 *  - 第 1 行填到当前行
 *  - 后续行依次填到下一行（不存在则新建，受 MAX_FILES 限制）
 * 不含换行 → 走默认粘贴行为。
 */
function onUrlPaste(event, rowIdx) {
  const txt = (event.clipboardData || window.clipboardData)?.getData('text') ?? ''
  if (!/[\r\n]/.test(txt)) return
  event.preventDefault()
  const lines = txt.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
  if (lines.length === 0) return

  let cursor = rowIdx
  let truncated = false
  for (const line of lines) {
    if (cursor >= MAX_FILES) {
      truncated = true
      break
    }
    if (cursor >= urlRows.value.length) {
      urlRows.value.push(makeRow(line))
    } else {
      urlRows.value[cursor].value = line
      urlRows.value[cursor].invalid = false
    }
    cursor++
  }
  if (truncated) {
    ElMessage.warning(`仅保留前 ${MAX_FILES} 个文件地址，其余已忽略`)
  } else {
    ElMessage.success(`已拆分 ${lines.length} 个地址到独立输入框`)
  }
}

async function handleSubmit() {
  const prompt = userPrompt.value.trim()
  if (!prompt) {
    ElMessage.warning('请填写判定提示词')
    return
  }

  if (sourceKind.value === 'upload') {
    if (uploadFiles.value.length === 0) {
      ElMessage.warning('请至少选择一个文件')
      return
    }
    if (uploadFiles.value.length > MAX_FILES) {
      ElMessage.warning(`最多 ${MAX_FILES} 个文件`)
      return
    }
  } else {
    // 校验每行
    let firstBad = -1
    const urls = []
    urlRows.value.forEach((row, i) => {
      const v = row.value.trim()
      if (!v) return                // 空行允许，提交时丢弃
      if (!/^https?:\/\//i.test(v)) {
        row.invalid = true
        if (firstBad === -1) firstBad = i
      } else {
        row.invalid = false
        urls.push(v)
      }
    })
    if (firstBad >= 0) {
      ElMessage.warning(`第 ${firstBad + 1} 行不是合法地址（需以 http:// 或 https:// 开头）`)
      return
    }
    if (urls.length === 0) {
      ElMessage.warning('请至少输入一个文件地址')
      return
    }
    if (urls.length > MAX_FILES) {
      ElMessage.warning(`最多 ${MAX_FILES} 个文件地址`)
      return
    }

    submitting.value = true
    batch.value = null
    stopPoll()
    try {
      const resp = await submitArchiveDetectUrls(urls, prompt)
      ElMessage.success(`已提交 ${resp.total_files} 个文件，AI 正在检测...`)
      startPoll(resp.batch_id)
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || '提交失败'
      ElMessage.error('提交失败：' + msg)
    } finally {
      submitting.value = false
    }
    return
  }

  submitting.value = true
  batch.value = null
  stopPoll()
  try {
    const realFiles = uploadFiles.value.map((it) => it.raw).filter(Boolean)
    const resp = await submitArchiveDetectUpload(realFiles, prompt)
    ElMessage.success(`已提交 ${resp.total_files} 个文件，AI 正在检测...`)
    startPoll(resp.batch_id)
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '提交失败'
    ElMessage.error('提交失败：' + msg)
  } finally {
    submitting.value = false
  }
}

function startPoll(batchId) {
  pollOnce(batchId)
  pollTimer = setInterval(() => pollOnce(batchId), 1500)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollOnce(batchId) {
  try {
    const data = await pollArchiveDetect(batchId)
    batch.value = data
    if (data.status === 'done') {
      stopPoll()
      const okCnt = (data.files || []).filter((f) => f.status === 'done').length
      ElMessage.success(`检测完成：${okCnt}/${data.total_files}`)
    }
  } catch (err) {
    stopPoll()
    ElMessage.error('查询状态失败：' + (err.response?.data?.detail || err.message))
  }
}

// ==================== utils ====================

function statusLabel(s) {
  return {
    pending: '排队中',
    fetching: '下载中',
    ocr: 'OCR 中',
    llm: 'AI 分析中',
    done: '完成',
    error: '失败',
  }[s] || s
}

function stageLabel(s) {
  return {
    pending: '等待开始...',
    fetching: '下载文件中...',
    ocr: 'OCR / 文本抽取中...',
    llm: 'AI 判定中...',
  }[s] || '处理中...'
}

function statusTagType(s) {
  if (s === 'done') return 'success'
  if (s === 'error') return 'danger'
  if (s === 'llm') return 'warning'
  return 'info'
}

onUnmounted(stopPoll)
</script>

<style scoped>
.archive-detect-page {
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
.entry-title { font-size: 16px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 10px; }
.title-indicator {
  width: 3px; height: 16px;
  background: linear-gradient(180deg, #fb923c, #f59e0b);
  border-radius: 2px;
}

/* 主体 */
.main-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 18px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card {
  background: #fff;
  border: 1px solid #e8ebf5;
  border-radius: 12px;
  padding: 18px 20px;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 12px;
}

.hint {
  margin-top: 8px;
  font-size: 12px;
  color: #94a3b8;
}
.hint .warn { color: #ef4444; margin-left: 4px; }

.source-pane {
  margin-top: 14px;
}

.upload-icon {
  font-size: 40px;
  color: #cbd5e1;
}
.upload-text { font-size: 14px; color: #475569; margin-top: 4px; }
.upload-tip { font-size: 12px; color: #94a3b8; margin-top: 4px; }

/* URL 动态行 */
.url-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.url-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.url-row-label {
  flex-shrink: 0;
  width: 92px;
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}
.url-row :deep(.el-input) { flex: 1; }
.url-row :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #cbd5e1 inset;
  transition: box-shadow 0.2s;
}
.url-row :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 2px #fb923c inset !important;
}
.url-row.has-error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #ef4444 inset !important;
  background: #fef2f2;
}
.url-row-del {
  flex-shrink: 0;
  color: #94a3b8 !important;
}
.url-row-del:hover:not(.is-disabled) {
  color: #ef4444 !important;
  border-color: #fecaca !important;
}

.url-row-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.url-counter {
  margin-top: 0 !important;
}

.submit-row {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.submit-row :deep(.el-button) {
  background: linear-gradient(135deg, #fb923c, #f59e0b) !important;
  border: none !important;
  font-weight: 600 !important;
  padding: 0 28px !important;
  height: 42px;
}
.submit-row :deep(.el-button.is-disabled) {
  opacity: 0.55;
}

/* 结果卡片网格 */
.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

.file-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.file-card.status-done.status-done { /* 占位优先级 */ }
.file-card.status-error {
  background: #fef2f2;
  border-color: #fecaca;
}

.fc-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fc-name {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fc-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #f59e0b;
  padding: 8px 0;
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

.fc-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.verdict {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  font-weight: 600;
  font-size: 14px;
}
.verdict.pass { background: #ecfdf5; color: #065f46; }
.verdict.pass :deep(.el-icon) { color: #10b981; font-size: 20px; }
.verdict.fail { background: #f1f5f9; color: #475569; }
.verdict.fail :deep(.el-icon) { color: #94a3b8; font-size: 20px; }
.verdict-score {
  margin-left: auto;
  font-size: 12px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-weight: 600;
  opacity: 0.85;
}

.confidence-track {
  height: 5px;
  background: #e2e8f0;
  border-radius: 3px;
  overflow: hidden;
}
.confidence-fill {
  height: 100%;
  background: linear-gradient(90deg, #fb923c, #f59e0b);
  border-radius: 3px;
  transition: width 0.5s ease;
}

.meta-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.section-title {
  font-size: 11px;
  font-weight: 700;
  color: #f59e0b;
  letter-spacing: 1px;
  margin-bottom: 6px;
  text-transform: uppercase;
}

.reason-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #1e293b;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
}

.key-points ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.key-points li {
  position: relative;
  padding: 5px 10px 5px 20px;
  font-size: 12px;
  color: #475569;
  background: #fff;
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

.fc-error {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #b91c1c;
  padding: 8px 0;
}

/* 空状态 */
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
  color: #94a3b8;
  gap: 10px;
}
.empty :deep(.el-icon) { color: #cbd5e1; }
.empty-title { font-size: 14px; color: #475569; margin: 0; font-weight: 500; }
.empty-sub { font-size: 12px; color: #94a3b8; margin: 0; }
</style>
