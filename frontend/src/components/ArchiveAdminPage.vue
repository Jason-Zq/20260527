<template>
  <div class="archive-admin-page">
    <div class="admin-header">
      <div class="admin-title">
        <span class="title-indicator"></span>
        检测批次管理
      </div>
      <div class="header-actions">
        <template v-if="rerunBatch.running">
          <el-progress
            :percentage="Math.round((rerunBatch.done / (rerunBatch.total || 1)) * 100)"
            :text-inside="true"
            :stroke-width="14"
            status="warning"
            style="width: 140px;"
          />
          <span class="dim rejudge-tip">
            批量重新检测派发中 {{ rerunBatch.done }}/{{ rerunBatch.total }}{{ rerunBatch.failed ? `（失败 ${rerunBatch.failed}）` : '' }}
          </span>
        </template>
        <el-button size="default" type="warning" plain :loading="rerunBatch.running" @click="onRerunBatch">
          <el-icon style="margin-right: 4px"><RefreshRight /></el-icon>
          批量重新检测
        </el-button>
        <el-button size="default" @click="loadBatches" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="admin-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.batch_id" class="w-wide" clearable placeholder="批次 ID" size="small" />
          <el-date-picker
            v-model="filters.date_range"
            class="date-filter"
            type="daterange"
            unlink-panels
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            size="small"
            value-format="YYYY-MM-DD"
          />
          <el-select
            v-model="filters.overall_verdict"
            class="w-verdict"
            multiple
            collapse-tags
            clearable
            placeholder="总体判断"
            size="small"
          >
            <el-option label="符合" value="match" />
            <el-option label="部分符合" value="partial" />
            <el-option label="不符合" value="mismatch" />
          </el-select>
          <el-checkbox v-model="filters.has_error_file" size="small" border>仅看有失败文件</el-checkbox>
          <el-select v-model="filters.status" class="w-narrow" clearable placeholder="状态" size="small">
            <el-option label="进行中" value="running" />
            <el-option label="完成" value="done" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-select v-model="filters.source_kind" class="w-narrow" clearable placeholder="来源" size="small">
            <el-option label="业务审核" value="batch" />
            <el-option label="重新审核" value="recheck" />
            <el-option label="快速上传" value="upload" />
            <el-option label="快速URL" value="url" />
          </el-select>
          <el-input v-model="filters.client_name" clearable placeholder="客户姓名" size="small" />
          <el-input v-model="filters.client_code" clearable placeholder="客户编码" size="small" />
          <el-input v-model="filters.progress_oid" class="w-wide" clearable placeholder="进展 OID" size="small" />
          <el-input v-model="filters.progress_name" clearable placeholder="进展名称" size="small" />
          <el-input v-model="filters.handler" clearable placeholder="办理人" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <div class="table-head">
          <span>批次列表</span>
          <div class="table-head-meta">
            <span class="dim">共 {{ total }} 条</span>
            <span v-if="selectedBatchIds.length" class="dim">已选 {{ selectedBatchIds.length }} 项</span>
            <el-button v-if="selectedBatchIds.length" size="small" link @click="clearSelection">清空已选</el-button>
          </div>
        </div>
        <el-table
          ref="batchTableRef"
          :data="batches"
          v-loading="loading"
          stripe
          empty-text="暂无批次"
          row-key="batch_id"
          :reserve-selection="true"
          @selection-change="handleSelectionChange"
        >
          <el-table-column type="selection" width="48" align="center" :selectable="isSelectable" />
          <el-table-column label="批次ID" min-width="160" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.batch_id }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="batchStatusTag(row.status)" size="small">{{ batchStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="90" align="center">
            <template #default="{ row }">{{ row.done_files }}/{{ row.total_files }}</template>
          </el-table-column>
          <!-- <el-table-column label="来源" width="90" align="center">
            <template #default="{ row }">{{ sourceKindLabel(row.source_kind) }}</template>
          </el-table-column> -->
          <el-table-column label="客户" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.client?.name || '-' }}</template>
          </el-table-column>
          <el-table-column label="办理人" min-width="100" show-overflow-tooltip align="center">
            <template #default="{ row }">{{ row.progress?.handler || '-' }}</template>
          </el-table-column>
          <el-table-column label="项目" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.project_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="项目详情" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.project_detail_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="进展" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.progress_name || row.progress?.progress_oid || '-' }}</template>
          </el-table-column>
          <el-table-column label="总体" width="110" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.overall_verdict" :type="verdictTag(row.overall_verdict)" size="small">
                {{ verdictLabel(row.overall_verdict) }} {{ row.overall_score ?? '' }}
              </el-tag>
              <span v-else class="dim">-</span>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="160">
            <template #default="{ row }"><span class="dim mono">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="识别完成时间" width="160">
            <template #default="{ row }"><span class="dim mono">{{ row.status === 'done' ? row.updated_at : '-' }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="200" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="selectBatch(row)">详情</el-button>
              <el-button
                size="small"
                type="warning"
                link
                @click.stop="openRerun(row)"
              >重审</el-button>
              <el-button
                size="small"
                type="danger"
                link
                @click.stop="onDeleteBatch(row)"
              >删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-row">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :total="total"
            :page-sizes="[10, 25, 50, 100, 200]"
            layout="total, sizes, prev, pager, next, jumper"
            small
            background
            @size-change="handlePageSizeChange"
            @current-change="handlePageChange"
          />
        </div>
      </section>

    </div>

    <BatchDetailDialog v-model="batchDialogVisible" :batch="selectedBatch" />

    <el-dialog v-model="rerunDialogVisible" title="重新审核" width="560px">
      <div v-if="rerunTarget" class="rerun-body">
        <div class="rerun-tip">
          原地重跑批次 <span class="mono">{{ rerunTarget.batch_id }}</span>：
          <br />• 有 OCR 文本的跳过 OCR，无 OCR 但有 URL 的重新 OCR
          <br />• 有 AI 结果的默认跳过（可勾选「全部重跑」强制用新标准重跑）
          <br />• 缺少的部分补跑
        </div>
        <el-form label-position="top">
          <el-form-item label="判定提示词（criteria）">
            <el-input
              v-model="rerunCriteria"
              type="textarea"
              :rows="6"
              placeholder="重新审核使用的判定提示词，不能为空"
            />
          </el-form-item>
          <el-form-item>
            <el-checkbox v-model="rerunForceAll">
              全部重跑（无视已有 AI 结果，全部用新标准重跑）
            </el-checkbox>
          </el-form-item>
          <el-form-item>
            <el-checkbox v-model="rerunRegenerateCriteria">
              使用新规则自动重新生成 criteria（根据客户/办理人/阶段）
            </el-checkbox>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="rerunDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="rerunSubmitting" @click="submitRerun">提交重跑</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="bulkRerunDialogVisible" title="批量重新检测" width="560px">
      <div class="rerun-body">
        <div class="rerun-tip">
          将对选中的 <span class="mono">{{ selectedBatchIds.length }}</span> 个批次逐个原地重跑——复用已有 OCR 文本、重新识别每个文件的符合度（不重新下载、不重新 OCR）。
          <br />此操作成本较高（每个文件一次 AI 调用），由后台队列串行处理，耗时较长。
        </div>
        <el-form label-position="top">
          <el-form-item>
            <el-checkbox v-model="bulkRerunRegenerateCriteria">
              使用新规则自动重新生成 criteria（逐批按客户/办理人/阶段）
            </el-checkbox>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="bulkRerunDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="bulkRerunSubmitting" @click="submitBulkRerun">开始检测</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'ArchiveAdminPage' })
import { ref, onMounted, onUnmounted } from 'vue'
import { Refresh, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listArchiveAdminBatches,
  rerunArchiveDetectBatch,
  startRerunFilesBatch,
  getRerunFilesProgress,
  deleteArchiveDetectBatch,
} from '../api.js'
import BatchDetailDialog from './BatchDetailDialog.vue'
import { verdictLabel, verdictTag, batchStatusLabel, batchStatusTag, sourceKindLabel } from '../utils/labels.js'

const loading = ref(false)
const batches = ref([])
const total = ref(0)
const selectedBatch = ref(null)
const batchDialogVisible = ref(false)
const currentPage = ref(1)
const pageSize = ref(10)
const batchTableRef = ref(null)
const selectedBatchIds = ref([])

function isSelectable(row) {
  // 只有已完成的批次才允许重新检测
  return row.status === 'done'
}

function handleSelectionChange(rows) {
  selectedBatchIds.value = rows.map(r => r.batch_id)
}

function clearSelection() {
  batchTableRef.value?.clearSelection()
  selectedBatchIds.value = []
}

const rerunDialogVisible = ref(false)
const rerunTarget = ref(null)
const rerunCriteria = ref('')
const rerunForceAll = ref(false)
const rerunRegenerateCriteria = ref(false)
const rerunSubmitting = ref(false)

const bulkRerunDialogVisible = ref(false)
const bulkRerunRegenerateCriteria = ref(false)
const bulkRerunSubmitting = ref(false)

function _defaultFilters() {
  const now = new Date()
  const start = new Date(now.getTime() - 7 * 24 * 3600 * 1000)
  const fmt = (d) => {
    const z = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
  }
  return {
    status: '',
    source_kind: '',
    batch_id: '',
    overall_verdict: [],
    has_error_file: false,
    client_name: '',
    client_code: '',
    progress_oid: '',
    progress_name: '',
    handler: '',
    date_range: [fmt(start), fmt(now)],
    limit: 10,
    offset: 0,
  }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = {}
  for (const [k, v] of Object.entries(filters.value)) {
    if (['limit', 'offset', 'date_range', 'overall_verdict', 'has_error_file'].includes(k)) continue
    if (v !== '' && v != null) out[k] = v
  }
  // 多选总体判断:非空时逗号拼接
  if (filters.value.overall_verdict?.length) {
    out.overall_verdict = filters.value.overall_verdict.join(',')
  }
  // 含失败文件:仅勾选时传 true
  if (filters.value.has_error_file) {
    out.has_error_file = true
  }
  if (filters.value.date_range?.length === 2) {
    out.date_from = filters.value.date_range[0]
    out.date_to = filters.value.date_range[1]
  }
  out.limit = pageSize.value
  out.offset = (currentPage.value - 1) * pageSize.value
  return out
}

async function loadBatches() {
  loading.value = true
  try {
    const resp = await listArchiveAdminBatches(buildParams())
    batches.value = resp.items || []
    total.value = resp.total || 0
  } catch (err) {
    ElMessage.error('加载批次失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  batchTableRef.value?.clearSelection()
  loadBatches()
}

function resetFilters() {
  filters.value = _defaultFilters()
  currentPage.value = 1
  pageSize.value = 10
  batchTableRef.value?.clearSelection()
  loadBatches()
}

function handlePageChange() {
  loadBatches()
}

function handlePageSizeChange() {
  currentPage.value = 1
  loadBatches()
}

function selectBatch(row) {
  selectedBatch.value = row
  batchDialogVisible.value = true
}

async function onDeleteBatch(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除批次「${row.batch_id}」？删除后无法恢复。`,
      '确认删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await deleteArchiveDetectBatch(row.batch_id)
    ElMessage.success('已删除')
  } catch (err) {
    if (err === 'cancel') return
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
    return
  }
  // 清理相关 UI 状态并刷新列表（刷新失败不应影响已删除成功）
  if (selectedBatch.value && selectedBatch.value.batch_id === row.batch_id) {
    batchDialogVisible.value = false
    selectedBatch.value = null
  }
  if (selectedBatchIds.value.includes(row.batch_id)) {
    selectedBatchIds.value = selectedBatchIds.value.filter((id) => id !== row.batch_id)
  }
  await loadBatches()
}

async function openRerun(row) {
  rerunTarget.value = row
  rerunCriteria.value = row.user_prompt || row.criteria || ''
  rerunForceAll.value = false
  rerunRegenerateCriteria.value = false
  rerunDialogVisible.value = true
}

async function submitRerun() {
  if (!rerunRegenerateCriteria.value && !rerunCriteria.value.trim()) {
    ElMessage.warning('判定提示词不能为空')
    return
  }
  rerunSubmitting.value = true
  try {
    const resp = await rerunArchiveDetectBatch(
      rerunTarget.value.batch_id,
      rerunCriteria.value.trim(),
      null,  // stage
      rerunForceAll.value || false,
      rerunRegenerateCriteria.value || false,
    )
    if (resp.mode === 'no-op') {
      ElMessage.info('所有文件已有完整结果，无需重跑')
    } else {
      const msg = resp.skipped_count > 0
        ? `已启动重跑:${resp.ai_only_count} 个复用 OCR,${resp.ocr_count} 个需 OCR,${resp.skipped_count} 个跳过`
        : `已启动重跑:${resp.ai_only_count} 个复用 OCR,${resp.ocr_count} 个需 OCR`
      ElMessage.success(msg)
    }
    rerunDialogVisible.value = false
    loadBatches()
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '重跑失败'
    ElMessage.error('重跑失败:' + msg)
  } finally {
    rerunSubmitting.value = false
  }
}

const rerunBatch = ref({ running: false, total: 0, done: 0, failed: 0 })
let rerunBatchTimer = null

async function pollRerunBatch() {
  try {
    const p = await getRerunFilesProgress()
    rerunBatch.value = p
    if (!p.running) {
      clearInterval(rerunBatchTimer)
      rerunBatchTimer = null
      ElMessage.success(`批量重新检测已全部派发：共 ${p.total} 个批次。单文件由后台队列串行处理，请稍后刷新查看结果`)
      loadBatches()
    }
  } catch (e) {
    // 轮询失败不打断
  }
}

async function onRerunBatch() {
  if (selectedBatchIds.value.length === 0) {
    ElMessage.warning('请勾选要重新检测的批次')
    return
  }
  bulkRerunDialogVisible.value = true
}

async function submitBulkRerun() {
  bulkRerunSubmitting.value = true
  try {
    const resp = await startRerunFilesBatch({
      batch_ids: selectedBatchIds.value,
      regenerateCriteria: bulkRerunRegenerateCriteria.value || false,
    })
    if (!resp.total) {
      ElMessage.info('没有符合重新检测条件的批次')
    } else {
      ElMessage.success(`已启动批量重新检测，共 ${resp.total} 个批次将被重跑`)
      rerunBatch.value = { running: true, total: resp.total, done: 0, failed: 0 }
      if (rerunBatchTimer) clearInterval(rerunBatchTimer)
      rerunBatchTimer = setInterval(pollRerunBatch, 2000)
    }
    bulkRerunDialogVisible.value = false
    batchTableRef.value?.clearSelection()
    selectedBatchIds.value = []
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '启动失败'
    ElMessage.error('批量重新检测启动失败：' + msg)
  } finally {
    bulkRerunSubmitting.value = false
  }
}

onMounted(() => {
  loadBatches()
  // 页面加载时若已有批量重新检测在跑,恢复轮询
  getRerunFilesProgress().then((p) => {
    if (p.running) {
      rerunBatch.value = p
      rerunBatchTimer = setInterval(pollRerunBatch, 2000)
    }
  }).catch(() => {})
})

onUnmounted(() => {
  if (rerunBatchTimer) clearInterval(rerunBatchTimer)
})
</script>

<style scoped>
.archive-admin-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.admin-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.admin-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.header-actions { display: flex; align-items: center; gap: 10px; }
.rejudge-tip { font-size: 12px; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #fb923c, #f59e0b); border-radius: 2px; }
.admin-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filter-grid > .el-input,
.filter-grid > .el-select { width: 160px; flex: 0 0 auto; }
.filter-grid > .w-narrow { width: 130px; }
.filter-grid > .w-wide { width: 200px; }
.filter-grid > .w-verdict { width: 170px; }
.date-filter { flex: 0 0 300px; width: 300px; }
.date-filter :deep(.el-range-editor) { width: 300px; }
.filter-grid > .el-button { flex: 0 0 auto; }
.table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; font-weight: 600; color: #1e293b; }
.table-head-meta { display: flex; align-items: center; gap: 12px; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-card { border-color: #fed7aa; }
</style>
