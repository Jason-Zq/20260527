<template>
  <div class="parse-entry-page">
    <!-- 顶栏 -->
    <div class="entry-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回首页
      </el-button>
      <div class="entry-title">
        <span class="title-indicator"></span>
        AI 识别文件
      </div>
      <div class="entry-actions">
        <el-select
          v-model="bindClientId"
          filterable
          clearable
          size="small"
          placeholder="绑定客户（可选，归档时使用）"
          style="width: 240px"
          :loading="loadingClients"
          @change="onBindClientChange"
        >
          <el-option
            v-for="c in clientOptions"
            :key="c.id"
            :label="`${c.name}${c.client_code ? ' [' + c.client_code + ']' : ''}${c.id_number ? ' · ' + c.id_number : ''}`"
            :value="c.id"
          />
        </el-select>
        <el-upload
          ref="uploadRef"
          :auto-upload="false"
          :show-file-list="false"
          :multiple="true"
          accept=".pdf,.png,.jpg,.jpeg,.bmp,.tiff,.webp"
          :on-change="handleFilesSelect"
        >
          <el-button size="small" class="upload-btn">
            <el-icon style="margin-right: 4px"><Upload /></el-icon>
            上传文件
          </el-button>
        </el-upload>
        <el-button v-if="queue.length > 0 && !anyRunning" @click="clearDoneQueue" size="small">清空队列</el-button>
        <el-button @click="openClients" size="small">
          <el-icon style="margin-right: 4px"><User /></el-icon>
          客户档案
        </el-button>
      </div>
    </div>

    <!-- 子页：客户列表 / 详情 -->
    <ClientListPage
      v-if="subView === 'clients'"
      @back="subView = 'main'"
      @select="onClientSelect"
      class="full-view"
    />
    <ClientDetailPage
      v-else-if="subView === 'client_detail' && currentClientId"
      :client-id="currentClientId"
      @back="subView = 'clients'"
      @select-doc="onDocSelect"
      class="full-view"
    />

    <!-- 主区 -->
    <div
      v-else
      class="main-area"
      @dragover.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <div v-if="dragOver" class="drag-mask">
        <el-icon :size="56"><Upload /></el-icon>
        <p>松手即可批量上传 {{ pendingDropCount > 0 ? `（${pendingDropCount} 个文件）` : '' }}</p>
      </div>

      <!-- 队列 -->
      <div v-if="queue.length > 0" class="queue-bar">
        <div class="queue-header">
          <span class="queue-title">
            上传队列
            <el-tag size="small" effect="plain">
              {{ doneCount }}/{{ queue.length }}
              <span v-if="errorCount" style="color:#ef4444; margin-left:4px">·失败 {{ errorCount }}</span>
            </el-tag>
          </span>
          <span class="queue-tip">
            点击单个文件进入归档审核
          </span>
        </div>
        <div class="queue-list">
          <div
            v-for="task in queue"
            :key="task.uid"
            class="queue-item"
            :class="[`q-${task.status}`, { active: activeTaskId === task.taskId }]"
            @click="task.status === 'done' && selectTask(task)"
          >
            <div class="qi-name" :title="task.file.name">{{ task.file.name }}</div>
            <div class="qi-meta">
              <span class="qi-status">{{ statusLabel(task) }}</span>
              <el-button v-if="task.status === 'error'" size="small" text type="primary" @click.stop="retry(task)">重试</el-button>
              <el-button v-if="['queued', 'uploading', 'processing'].includes(task.status)" size="small" text @click.stop="cancel(task)">取消</el-button>
              <el-tag v-if="task.archived" size="small" type="success" effect="dark">已归档</el-tag>
            </div>
            <div class="qi-progress">
              <div class="qi-fill" :style="{ width: task.progress + '%' }"></div>
            </div>
          </div>
        </div>

        <!-- 一键归档剩余 -->
        <div v-if="readyToArchiveCount > 0" class="batch-bar">
          <el-button size="small" type="primary" :loading="batchArchiving" @click="batchArchive">
            <el-icon style="margin-right: 4px"><Lightning /></el-icon>
            一键归档剩余 {{ readyToArchiveCount }} 个无冲突文件
          </el-button>
          <span class="batch-tip">仅对"已识别+已确定归属"的文件生效，遇冲突自动跳过</span>
        </div>
      </div>

      <!-- 主体三栏 -->
      <div class="review-container">
        <!-- 左：证件图 -->
        <div class="left-panel">
          <DocumentViewer
            :images="result ? result.images : []"
            :fields="{}"
            :doc-type="result?.items?.[0]?.doc_type || ''"
          />
        </div>

        <!-- 中：OCR 文字 -->
        <div class="middle-panel">
          <div class="panel-header">
            <span class="header-indicator"></span>
            OCR 识别文字
          </div>
          <div class="ocr-text-content">
            <template v-if="result">
              <div v-for="(text, idx) in result.ocr_texts" :key="idx" class="ocr-page">
                <div class="ocr-page-title">第 {{ idx + 1 }} 页</div>
                <pre class="ocr-page-text">{{ text || '(此页未OCR识别)' }}</pre>
              </div>
            </template>
            <div v-else class="empty-hint">
              <span>选择文件或拖拽到此处</span>
            </div>
          </div>
        </div>

        <!-- 右：归档审核器 -->
        <div class="right-panel">
          <ArchiveReviewPanel
            v-if="result"
            :key="result.task_id"
            :task="result"
            :bound-client-id="bindClientId"
            @archived="onArchived"
            @skip="onSkip"
            @client-selected="onMatchedClient"
            @request-create-client="showCreateClient = true"
            @request-pick-client="onRequestPickClient"
          />
          <div v-else class="empty-hint">
            <span>选择文件后显示归档审核面板</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 创建客户弹窗（与列表页同一个，直接复用 createClient API）-->
    <el-dialog v-model="showCreateClient" title="新建客户" width="520px">
      <el-form :model="newClientForm" label-width="92px" label-position="right">
        <el-form-item label="客户姓名" required>
          <el-input v-model="newClientForm.name" />
        </el-form-item>
        <el-form-item label="客户编号">
          <el-input v-model="newClientForm.client_code" />
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="newClientForm.gender" clearable style="width: 100%">
            <el-option label="男" value="男" />
            <el-option label="女" value="女" />
          </el-select>
        </el-form-item>
        <el-form-item label="出生日期">
          <el-date-picker v-model="newClientForm.birth_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="证件号">
          <el-input v-model="newClientForm.id_number" />
        </el-form-item>
        <el-form-item label="护照号">
          <el-input v-model="newClientForm.passport_no" />
        </el-form-item>
        <el-form-item label="业务类型">
          <el-input v-model="newClientForm.visa_type" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateClient = false">取消</el-button>
        <el-button type="primary" :loading="creatingClient" @click="handleCreateClient">创建并选中</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted, onMounted, reactive, nextTick } from 'vue'
import { ArrowLeft, Upload, User, Lightning } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import DocumentViewer from './DocumentViewer.vue'
import ClientListPage from './ClientListPage.vue'
import ClientDetailPage from './ClientDetailPage.vue'
import ArchiveReviewPanel from './ArchiveReviewPanel.vue'
import { uploadFile, pollResult, listClients, createClient, saveReview } from '../api.js'
import { useRouter } from 'vue-router'

const router = useRouter()
function emit(evt) {
  if (evt === 'back') router.push('/')
}

const result = ref(null)            // 当前选中预览的 task 完整结果
const activeTaskId = ref(null)      // 队列中当前选中的 taskId

const subView = ref('main')
const currentClientId = ref(null)

// 队列
const queue = reactive([])
const MAX_CONCURRENT = 2

// 客户
const bindClientId = ref(null)
const clientOptions = ref([])
const loadingClients = ref(false)

// 拖拽
const dragOver = ref(false)
const pendingDropCount = ref(0)
let dragCounter = 0

// 创建客户弹窗
const showCreateClient = ref(false)
const creatingClient = ref(false)
const newClientForm = ref({
  name: '', client_code: '', gender: '', birth_date: '',
  id_number: '', passport_no: '', visa_type: '',
})

// 批量归档
const batchArchiving = ref(false)

const doneCount = computed(() => queue.filter(t => t.status === 'done').length)
const errorCount = computed(() => queue.filter(t => t.status === 'error').length)
const anyRunning = computed(() => queue.some(t => ['queued', 'uploading', 'processing'].includes(t.status)))
const runningCount = computed(() => queue.filter(t => ['uploading', 'processing'].includes(t.status)).length)
const readyToArchiveCount = computed(() => queue.filter(t => t.status === 'done' && !t.archived && t.canBatchArchive).length)

function statusLabel(task) {
  switch (task.status) {
    case 'queued': return '等待中'
    case 'uploading': return '上传中'
    case 'processing':
      if (task.subStatus === 'ocr') return 'OCR'
      if (task.subStatus === 'llm') return 'AI 分析'
      return '处理中'
    case 'done': return task.archived ? '✓ 已归档' : '✓ 待归档'
    case 'error': return '✗ ' + (task.error || '失败')
    case 'cancelled': return '已取消'
    default: return task.status
  }
}

let _uid = 0
function nextUid() { return ++_uid }

function enqueueFiles(files) {
  for (const file of files) {
    if (!file) continue
    queue.push({
      uid: nextUid(),
      file,
      status: 'queued',
      progress: 0,
      taskId: null,
      subStatus: '',
      error: '',
      pollTimer: null,
      cancelled: false,
      archived: false,
      canBatchArchive: false,    // 单个文件已确定归属、可批量归档
      resultData: null,           // 缓存的 task 完整数据
    })
  }
  scheduleNext()
}

function scheduleNext() {
  while (runningCount.value < MAX_CONCURRENT) {
    const next = queue.find(t => t.status === 'queued')
    if (!next) break
    runTask(next)
  }
}

async function runTask(task) {
  task.status = 'uploading'
  task.progress = 5
  try {
    // 注意：不传 client_id（A1 自动归档不再走旧路径，新流程统一在 ArchiveReviewPanel 走）
    const res = await uploadFile(task.file)
    if (task.cancelled) return
    task.taskId = res.task_id
    task.status = 'processing'
    task.subStatus = 'ocr'
    task.progress = 30
    startPolling(task)
  } catch (err) {
    task.status = 'error'
    task.error = err.response?.data?.detail || err.message || '上传失败'
    task.progress = 100
    scheduleNext()
  }
}

function startPolling(task) {
  if (task.pollTimer) clearInterval(task.pollTimer)
  task.pollTimer = setInterval(async () => {
    if (task.cancelled) {
      clearInterval(task.pollTimer); task.pollTimer = null
      return
    }
    try {
      const data = await pollResult(task.taskId)
      if (data.status === 'done') {
        clearInterval(task.pollTimer); task.pollTimer = null
        task.status = 'done'
        task.progress = 100
        task.resultData = data
        scheduleNext()
        // 第一个完成的自动加载到右侧预览
        if (!result.value) selectTask(task)
      } else if (data.status === 'error') {
        clearInterval(task.pollTimer); task.pollTimer = null
        task.status = 'error'
        task.error = data.error || '处理失败'
        task.progress = 100
        scheduleNext()
      } else {
        task.subStatus = data.status
        task.progress = data.status === 'llm' ? 75 : 50
      }
    } catch (_) {}
  }, 1500)
}

async function selectTask(task) {
  if (task.status !== 'done' || !task.taskId) return
  if (!task.resultData) {
    try {
      task.resultData = await pollResult(task.taskId)
    } catch (err) {
      ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
      return
    }
  }
  result.value = task.resultData
  activeTaskId.value = task.taskId
}

function retry(task) {
  task.cancelled = false
  task.error = ''
  task.taskId = null
  task.progress = 0
  task.subStatus = ''
  task.status = 'queued'
  task.archived = false
  task.resultData = null
  scheduleNext()
}

function cancel(task) {
  task.cancelled = true
  if (task.pollTimer) clearInterval(task.pollTimer)
  task.pollTimer = null
  task.status = 'cancelled'
  task.progress = 100
  scheduleNext()
}

function clearDoneQueue() {
  for (let i = queue.length - 1; i >= 0; i--) {
    if (['done', 'error', 'cancelled'].includes(queue[i].status) && (queue[i].archived || queue[i].status !== 'done')) {
      queue.splice(i, 1)
    }
  }
  if (queue.length === 0) {
    result.value = null
    activeTaskId.value = null
  }
}

// 拖拽
function onDragOver(e) {
  if (subView.value !== 'main') return
  if (!dragOver.value) {
    dragOver.value = true
    pendingDropCount.value = e.dataTransfer?.items?.length || 0
  }
  dragCounter = 1
}
function onDragLeave() {
  dragCounter--
  if (dragCounter <= 0) {
    dragOver.value = false; pendingDropCount.value = 0; dragCounter = 0
  }
}
function onDrop(e) {
  dragOver.value = false; pendingDropCount.value = 0; dragCounter = 0
  if (subView.value !== 'main') return
  const files = Array.from(e.dataTransfer?.files || [])
  if (!files.length) return
  const accept = /\.(pdf|png|jpg|jpeg|bmp|tiff|webp)$/i
  const valid = files.filter(f => accept.test(f.name))
  const skipped = files.length - valid.length
  if (skipped) ElMessage.warning(`已跳过 ${skipped} 个不支持的文件`)
  if (valid.length) enqueueFiles(valid)
}

function handleFilesSelect(file) {
  if (file?.raw) enqueueFiles([file.raw])
}

// 归档完成
function onArchived(payload) {
  const task = queue.find(t => t.taskId === payload.task_id)
  if (task) {
    task.archived = true
    task.canBatchArchive = false
  }
  // 自动跳到下一个待归档
  const next = queue.find(t => t.status === 'done' && !t.archived)
  if (next) {
    nextTick(() => selectTask(next))
  } else {
    result.value = null
    activeTaskId.value = null
  }
}

function onSkip() {
  const next = queue.find(t => t.status === 'done' && !t.archived && t.taskId !== activeTaskId.value)
  if (next) {
    selectTask(next)
  }
}

function onMatchedClient(clientId) {
  // ArchiveReviewPanel 通过 banner 选了某客户 → 同步到顶部下拉，便于其他文件复用
  if (clientId && bindClientId.value !== clientId) {
    bindClientId.value = clientId
  }
  // 标记当前 task 已可批量归档
  const task = queue.find(t => t.taskId === activeTaskId.value)
  if (task) task.canBatchArchive = true
}

function onBindClientChange() {
  // 顶部下拉切了客户：当前 task 重新匹配
  const task = queue.find(t => t.taskId === activeTaskId.value)
  if (task) task.canBatchArchive = !!bindClientId.value
}

function onRequestPickClient() {
  // 把焦点拉到顶部下拉（简化：不滚动，仅提示）
  ElMessage.info('请使用顶部"绑定客户"下拉选择')
}

async function handleCreateClient() {
  if (!newClientForm.value.name?.trim()) {
    ElMessage.warning('客户姓名不能为空'); return
  }
  creatingClient.value = true
  try {
    const payload = {}
    for (const [k, v] of Object.entries(newClientForm.value)) {
      if (v != null && v !== '') payload[k] = v
    }
    const c = await createClient(payload)
    ElMessage.success(`已创建：${c.name}`)
    showCreateClient.value = false
    newClientForm.value = { name: '', client_code: '', gender: '', birth_date: '', id_number: '', passport_no: '', visa_type: '' }
    await loadClientList()
    bindClientId.value = c.id
    // 通知当前面板
    onMatchedClient(c.id)
  } catch (err) {
    ElMessage.error('创建失败：' + (err.response?.data?.detail || err.message))
  } finally {
    creatingClient.value = false
  }
}

// 一键归档剩余
async function batchArchive() {
  const targets = queue.filter(t => t.status === 'done' && !t.archived && t.canBatchArchive)
  if (!targets.length) return
  batchArchiving.value = true
  let ok = 0, fail = 0
  try {
    for (const t of targets) {
      // 切换到该 task → 等下个 tick → 触发 ArchivePanel 内的 archive
      await selectTask(t)
      await nextTick()
      // 注：因为 ArchiveReviewPanel 实例每次切换重建，这里用一个简化策略：
      // 只把队列中"已确定归属且与顶部下拉客户一致"的 task 调用一次 archive。
      // 简化为：依次 selectTask + 触发 archive 不可靠，改为直接调 saveReview 接口
      try {
        const archive = {
          client_id: bindClientId.value,
          entity: t.suggestedEntity || guessEntityFromTask(t),
          target_id: null,
          sub_meta: t.suggestedSubMeta || guessSubMeta(t),
        }
        if (!archive.client_id || !archive.entity) {
          fail++; continue
        }
        // 用 saveReview 的新形态走后端 archive_document
        await saveReview(t.taskId, { items: t.resultData?.items || [], archive })
        t.archived = true
        ok++
      } catch (err) {
        fail++
      }
    }
    if (ok) ElMessage.success(`成功归档 ${ok} 个`)
    if (fail) ElMessage.warning(`${fail} 个跳过（信息不全或冲突）`)
  } finally {
    batchArchiving.value = false
  }
}

// 简易 entity 推断（只在 batch 时用，单个仍走 ArchiveReviewPanel 精细逻辑）
function guessEntityFromTask(task) {
  const dt = task.resultData?.items?.[0]?.doc_type || ''
  if (dt.startsWith('配偶')) return 'family'
  if (['出生医学证', '出生证'].includes(dt)) return 'family'
  if (['房产证', '不动产权证', '存款证明', '银行流水', '银行对账单', '行驶证', '车辆登记证', '股票账户'].includes(dt)) return 'assets'
  return 'clients'
}

function guessSubMeta(task) {
  const dt = task.resultData?.items?.[0]?.doc_type || ''
  if (dt.startsWith('配偶')) return { relation: '配偶' }
  if (['出生医学证', '出生证'].includes(dt)) return { relation: '子' }
  if (['房产证', '不动产权证'].includes(dt)) return { asset_type: '房产' }
  if (['存款证明'].includes(dt)) return { asset_type: '存款' }
  if (['银行流水', '银行对账单'].includes(dt)) return { asset_type: '银行流水' }
  if (['行驶证', '车辆登记证'].includes(dt)) return { asset_type: '车辆' }
  if (dt === '股票账户') return { asset_type: '股票' }
  return null
}

function openClients() { subView.value = 'clients' }
function onClientSelect(id) { currentClientId.value = id; subView.value = 'client_detail' }
async function onDocSelect(taskId) {
  try {
    const data = await pollResult(taskId)
    if (data.status === 'done') {
      result.value = data
      activeTaskId.value = taskId
      subView.value = 'main'
      ElMessage.success('已加载文档')
    } else {
      ElMessage.warning('该记录尚未完成处理')
    }
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  }
}

async function loadClientList() {
  loadingClients.value = true
  try {
    const data = await listClients()
    clientOptions.value = data.clients || []
  } catch (err) {
    console.warn('客户列表加载失败', err)
  } finally {
    loadingClients.value = false
  }
}

onMounted(() => {
  loadClientList()
})

onUnmounted(() => {
  for (const t of queue) {
    if (t.pollTimer) clearInterval(t.pollTimer)
  }
})
</script>

<style scoped>
.parse-entry-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; color: #1e293b; }

.entry-header {
  padding: 0 24px;
  height: 56px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  border-bottom: 1px solid #e8ebf5;
}
.back-btn {
  background: #f1f5f9 !important;
  border: 1px solid #e2e8f0 !important;
  color: #475569 !important;
  border-radius: 8px !important;
}
.back-btn:hover { background: #e2e8f0 !important; color: #6366f1 !important; }
.entry-title { font-size: 16px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 10px; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.entry-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.upload-btn {
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
  padding: 8px 18px !important;
}
.upload-btn:hover { box-shadow: 0 4px 14px rgba(99,102,241,0.4) !important; transform: translateY(-1px); }

.full-view { flex: 1; overflow: hidden; }
.main-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
.drag-mask {
  position: absolute; inset: 0; background: rgba(99,102,241,0.08);
  border: 2px dashed #6366f1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 12px;
  z-index: 50; color: #6366f1; font-weight: 600; pointer-events: none;
}

/* 队列 */
.queue-bar {
  flex-shrink: 0; background: #fff; border-bottom: 1px solid #e8ebf5;
  padding: 10px 16px; max-height: 35vh; display: flex; flex-direction: column;
}
.queue-header { display: flex; justify-content: space-between; margin-bottom: 8px; }
.queue-title { font-size: 13px; font-weight: 600; color: #1e293b; display: flex; align-items: center; gap: 8px; }
.queue-tip { font-size: 12px; color: #94a3b8; }
.queue-list { flex: 1; overflow-y: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px; padding: 2px; }
.queue-item {
  position: relative; background: #f8fafc; border: 1px solid #e2e8f0;
  border-radius: 8px; padding: 8px 12px 6px; display: flex; flex-direction: column; gap: 4px;
  transition: all 0.15s; overflow: hidden; cursor: pointer;
}
.queue-item.q-done { border-color: rgba(16,185,129,0.3); }
.queue-item.q-done:hover { background: #f0fdf4; }
.queue-item.q-error { border-color: rgba(239,68,68,0.3); background: #fef2f2; }
.queue-item.q-cancelled { opacity: 0.6; }
.queue-item.active { border-color: #6366f1; box-shadow: 0 0 0 1px #6366f1; background: #eef2ff; }
.qi-name { font-size: 13px; color: #1e293b; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qi-meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 11px; color: #64748b; }
.qi-status { font-weight: 500; }
.q-done .qi-status { color: #10b981; }
.q-error .qi-status { color: #ef4444; }
.qi-progress { height: 3px; background: #e2e8f0; border-radius: 2px; overflow: hidden; }
.qi-fill { height: 100%; background: linear-gradient(90deg, #6366f1, #8b5cf6); transition: width 0.4s ease; }
.q-done .qi-fill { background: #10b981; }
.q-error .qi-fill { background: #ef4444; }

.batch-bar {
  margin-top: 10px; padding-top: 10px;
  border-top: 1px dashed #e2e8f0;
  display: flex; align-items: center; gap: 12px;
}
.batch-tip { font-size: 12px; color: #94a3b8; }

/* 三栏 */
.review-container { flex: 1; display: flex; gap: 12px; padding: 12px; overflow: hidden; }
.left-panel { flex: 1; min-width: 0; }
.middle-panel {
  width: 320px; flex-shrink: 0; display: flex; flex-direction: column; overflow: hidden;
  background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.panel-header { padding: 12px 16px; font-size: 13px; font-weight: 600; color: #1e293b; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.header-indicator { width: 3px; height: 14px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.ocr-text-content { flex: 1; overflow-y: auto; padding: 12px; }
.ocr-page { margin-bottom: 14px; }
.ocr-page-title { font-size: 11px; color: #6366f1; font-weight: 600; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px dashed #e2e8f0; }
.ocr-page-text { font-size: 12px; line-height: 1.7; color: #475569; white-space: pre-wrap; word-break: break-all; font-family: 'JetBrains Mono','Consolas',monospace; margin: 0; background: #f8fafc; padding: 8px 10px; border-radius: 6px; }

.right-panel {
  width: 460px; flex-shrink: 0; overflow: hidden;
  background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  display: flex; flex-direction: column;
}

.empty-hint {
  display: flex; align-items: center; justify-content: center;
  height: 100%; color: #94a3b8; font-size: 13px;
}
</style>
