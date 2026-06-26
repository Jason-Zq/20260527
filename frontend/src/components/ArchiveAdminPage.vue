<template>
  <div class="archive-admin-page">
    <div class="admin-header">
      <div class="admin-title">
        <span class="title-indicator"></span>
        审核任务管理
      </div>
      <el-button size="default" @click="loadBatches" :loading="loading">
        <el-icon style="margin-right: 4px"><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <div class="admin-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-select v-model="filters.status" clearable placeholder="状态" size="small">
            <el-option label="进行中" value="running" />
            <el-option label="完成" value="done" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-select v-model="filters.source_kind" clearable placeholder="来源" size="small">
            <el-option label="业务审核" value="batch" />
            <el-option label="重新审核" value="recheck" />
            <el-option label="快速上传" value="upload" />
            <el-option label="快速URL" value="url" />
          </el-select>
          <el-input v-model="filters.client_name" clearable placeholder="客户姓名" size="small" />
          <el-input v-model="filters.client_code" clearable placeholder="客户编码" size="small" />
          <el-input v-model="filters.progress_oid" clearable placeholder="进展 OID" size="small" />
          <el-input v-model="filters.progress_name" clearable placeholder="进展名称" size="small" />
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
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <div class="table-head">
          <span>批次列表</span>
          <span class="dim">共 {{ total }} 条</span>
        </div>
        <el-table
          :data="batches"
          v-loading="loading"
          stripe
          empty-text="暂无批次"
        >
          <el-table-column label="批次ID" min-width="160" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.batch_id }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="90" align="center">
            <template #default="{ row }">{{ row.done_files }}/{{ row.total_files }}</template>
          </el-table-column>
          <el-table-column label="来源" width="90" align="center">
            <template #default="{ row }">{{ sourceLabel(row.source_kind) }}</template>
          </el-table-column>
          <el-table-column label="客户" min-width="120" show-overflow-tooltip>
            <template #default="{ row }">{{ row.client?.name || '-' }}</template>
          </el-table-column>
          <el-table-column label="进展" min-width="150" show-overflow-tooltip>
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
          <el-table-column label="操作" width="90" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="selectBatch(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-row">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :total="total"
            :page-sizes="[10, 20, 50, 100]"
            layout="total, sizes, prev, pager, next, jumper"
            small
            background
            @size-change="handlePageSizeChange"
            @current-change="handlePageChange"
          />
        </div>
      </section>

    </div>

    <el-dialog v-model="batchDialogVisible" title="批次详情" width="82%" top="5vh">
      <div v-if="selectedBatch" v-loading="detailLoading" class="batch-dialog-body">
        <div class="dialog-toolbar">
          <span class="dim mono">{{ detail?.batch_id || selectedBatch.batch_id }}</span>
          <el-button size="small" @click="loadBatchDetail(selectedBatch.batch_id)">刷新详情</el-button>
        </div>

        <div class="summary-row">
          <div class="summary-item"><b>批次</b><span class="mono">{{ detail?.batch_id || selectedBatch.batch_id }}</span></div>
          <div class="summary-item"><b>状态</b><span>{{ statusLabel(detail?.status || selectedBatch.status) }}</span></div>
          <div class="summary-item"><b>进度</b><span>{{ detail?.done_files ?? selectedBatch.done_files }}/{{ detail?.total_files ?? selectedBatch.total_files }}</span></div>
          <div class="summary-item"><b>客户</b><span>{{ detail?.client?.name || selectedBatch.client?.name || '-' }}</span></div>
        </div>

        <div v-if="detail?.overall_reason" class="overall-box">
          <div class="overall-title">{{ verdictLabel(detail.overall_verdict) }} · {{ detail.overall_score }}/100</div>
          <p>{{ detail.overall_reason }}</p>
        </div>

        <el-table :data="detail?.files || []" stripe empty-text="暂无文件" max-height="420">
          <el-table-column label="文件" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.filename || row.source_url || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }"><el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="分类" width="140" show-overflow-tooltip prop="doc_category" />
          <el-table-column label="判断" width="110" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.verdict" :type="verdictTag(row.verdict)" size="small">{{ verdictLabel(row.verdict) }}</el-tag>
              <span v-else class="dim">-</span>
            </template>
          </el-table-column>
          <el-table-column label="分数" width="70" align="center" prop="match_score" />
          <el-table-column label="复用" width="70" align="center">
            <template #default="{ row }"><el-tag v-if="row.is_reused" size="small" type="info">复用</el-tag><span v-else>-</span></template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="center">
            <template #default="{ row }">
              <el-button size="small" type="primary" link :disabled="!row.id" @click.stop="openFileDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>

    <el-dialog v-model="fileDialogVisible" title="文件详情" width="70%">
      <div v-loading="fileLoading">
        <template v-if="fileDetail">
          <div class="detail-meta">
            <div><b>文件名：</b>{{ fileDetail.filename || '-' }}</div>
            <div><b>分类：</b>{{ fileDetail.doc_category || '-' }}</div>
            <div><b>判断：</b>{{ verdictLabel(fileDetail.verdict) }} {{ fileDetail.match_score ?? '' }}</div>
          </div>
          <el-divider content-position="left">判断依据</el-divider>
          <p class="reason-text">{{ fileDetail.reason || '-' }}</p>
          <el-divider content-position="left">OCR 文本（已脱敏）</el-divider>
          <pre class="ocr-text">{{ fileDetail.ocr_text || '无 OCR 文本' }}</pre>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  listArchiveAdminBatches,
  getArchiveAdminFileDetail,
  pollBusinessBatch,
  pollArchiveDetect,
} from '../api.js'

const loading = ref(false)
const batches = ref([])
const total = ref(0)
const selectedBatch = ref(null)
const detail = ref(null)
const detailLoading = ref(false)
const batchDialogVisible = ref(false)
const fileDialogVisible = ref(false)
const fileLoading = ref(false)
const fileDetail = ref(null)
const currentPage = ref(1)
const pageSize = ref(10)

const filters = ref({
  status: '',
  source_kind: '',
  client_name: '',
  client_code: '',
  progress_oid: '',
  progress_name: '',
  date_range: [],
  limit: 10,
  offset: 0,
})

function buildParams() {
  const out = {}
  for (const [k, v] of Object.entries(filters.value)) {
    if (['limit', 'offset', 'date_range'].includes(k)) continue
    if (v !== '' && v != null) out[k] = v
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
  loadBatches()
}

function resetFilters() {
  filters.value = {
    status: '',
    source_kind: '',
    client_name: '',
    client_code: '',
    progress_oid: '',
    progress_name: '',
    date_range: [],
    limit: 10,
    offset: 0,
  }
  currentPage.value = 1
  pageSize.value = 10
  loadBatches()
}

function handlePageChange() {
  loadBatches()
}

function handlePageSizeChange() {
  currentPage.value = 1
  loadBatches()
}

async function selectBatch(row) {
  selectedBatch.value = row
  detail.value = null
  batchDialogVisible.value = true
  await loadBatchDetail(row.batch_id)
}

async function loadBatchDetail(batchId) {
  detailLoading.value = true
  try {
    // 业务批次优先 business 接口；非业务批次回落通用接口。
    let data
    try {
      data = await pollBusinessBatch(batchId)
    } catch (err) {
      if (err.response?.status !== 404) throw err
      data = await pollArchiveDetect(batchId)
    }
    detail.value = data
  } catch (err) {
    ElMessage.error('加载详情失败：' + (err.response?.data?.detail || err.message))
  } finally {
    detailLoading.value = false
  }
}

async function openFileDetail(row) {
  fileDialogVisible.value = true
  fileLoading.value = true
  fileDetail.value = null
  try {
    fileDetail.value = await getArchiveAdminFileDetail(row.id)
  } catch (err) {
    ElMessage.error('加载文件详情失败：' + (err.response?.data?.detail || err.message))
  } finally {
    fileLoading.value = false
  }
}

function statusLabel(s) {
  return { running: '进行中', done: '完成', error: '失败', pending: '排队中', fetching: '下载中', ocr: 'OCR中', llm: 'AI分析中' }[s] || s || '-'
}
function statusTag(s) {
  if (s === 'done') return 'success'
  if (s === 'error') return 'danger'
  return 'warning'
}
function verdictLabel(v) {
  return { match: '符合', partial: '部分符合', mismatch: '不符合' }[v] || '-'
}
function verdictTag(v) {
  if (v === 'match') return 'success'
  if (v === 'partial') return 'warning'
  return 'info'
}
function sourceLabel(v) {
  return { batch: '业务', recheck: '重审', upload: '上传', url: 'URL' }[v] || v || '-'
}

onMounted(() => {
  loadBatches()
})
</script>

<style scoped>
.archive-admin-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.admin-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.admin-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #fb923c, #f59e0b); border-radius: 2px; }
.admin-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)) auto auto; gap: 10px; align-items: center; }
.date-filter { grid-column: span 2; width: 100%; }
.table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; font-weight: 600; color: #1e293b; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-card { border-color: #fed7aa; }
.batch-dialog-body { min-height: 260px; }
.dialog-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.summary-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.summary-item { background: #f8fafc; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.summary-item b { color: #64748b; font-size: 12px; }
.overall-box { background: #fff7ed; border: 1px solid #fed7aa; border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.overall-title { font-weight: 700; color: #c2410c; margin-bottom: 6px; }
.overall-box p { margin: 0; color: #475569; line-height: 1.7; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; font-size: 13px; color: #334155; }
.reason-text { line-height: 1.7; color: #334155; background: #f8fafc; padding: 10px 12px; border-radius: 8px; }
.ocr-text { max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; }
@media (max-width: 1200px) { .filter-grid { grid-template-columns: repeat(3, 1fr); } .summary-row { grid-template-columns: repeat(2, 1fr); } }
</style>
