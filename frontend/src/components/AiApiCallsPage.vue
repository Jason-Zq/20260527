<template>
  <div class="aiapi-page">
    <div class="aiapi-header">
      <div class="aiapi-title">
        <span class="title-indicator"></span>
        AI/LLM 调用记录
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">
          数据库保留 30 天 · 共 {{ total }} 条
        </span>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="aiapi-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-date-picker
            v-model="filters.dateRange"
            class="date-filter"
            type="datetimerange"
            unlink-panels
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            size="small"
            value-format="YYYY-MM-DD HH:mm:ss"
          />
          <el-select v-model="filters.operation" clearable placeholder="操作" size="small">
            <el-option
              v-for="op in operationOptions"
              :key="op"
              :label="op"
              :value="op"
            />
          </el-select>
          <el-select v-model="filters.status" clearable placeholder="状态" size="small">
            <el-option label="成功" value="ok" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-input v-model="filters.model" clearable placeholder="模型 ID 模糊查" size="small" />
          <el-input v-model="filters.batch_id" clearable placeholder="批次 ID 模糊查" size="small" />
          <el-input v-model="filters.file_id" clearable placeholder="文件编码模糊查" size="small" />
          <el-input v-model="filters.client_code" clearable placeholder="客户编码模糊查" size="small" />
          <el-input v-model="filters.task_id" clearable placeholder="任务 ID 模糊查" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无记录" size="default" style="width: 100%">
          <el-table-column label="时间" min-width="150">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="操作" min-width="180" show-overflow-tooltip prop="operation" />
          <!-- <el-table-column label="模型" min-width="150" show-overflow-tooltip prop="model" /> -->
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">{{ row.status === 'ok' ? '成功' : '失败' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="100" align="center">
            <template #default="{ row }">{{ row.elapsed_ms != null ? (row.elapsed_ms / 1000).toFixed(3) + 's' : '-' }}</template>
          </el-table-column>
          <el-table-column label="批次" min-width="130" show-overflow-tooltip prop="batch_id" />
          <el-table-column label="文件编码" min-width="120" show-overflow-tooltip prop="file_id" />
          <el-table-column label="客户编码" min-width="100" show-overflow-tooltip prop="client_code" />
          <el-table-column label="任务 ID" min-width="120" show-overflow-tooltip prop="task_id" />
          <el-table-column label="操作" width="100" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-row">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 25, 50, 100, 200]"
            :total="total"
            layout="total, sizes, prev, pager, next"
            @current-change="loadList"
            @size-change="onPageSizeChange"
          />
        </div>
      </section>
    </div>

    <el-dialog v-model="detailVisible" title="调用详情" width="75%" top="5vh">
      <div v-if="selected" class="detail-body">
        <div class="detail-meta">
          <div><b>时间：</b><span class="mono">{{ selected.created_at }}</span></div>
          <div><b>操作：</b><span class="mono">{{ selected.operation || '-' }}</span></div>
          <!-- <div><b>模型：</b><span class="mono">{{ selected.model || '-' }}</span></div> -->
          <div><b>状态：</b><el-tag :type="selected.status === 'ok' ? 'success' : 'danger'" size="small">{{ selected.status === 'ok' ? '成功' : '失败' }}</el-tag></div>
          <div><b>耗时：</b>{{ selected.elapsed_ms != null ? (selected.elapsed_ms / 1000).toFixed(3) + 's' : '-' }}</div>
          <div><b>批次：</b><span class="mono">{{ selected.batch_id || '-' }}</span></div>
          <div><b>文件编码：</b><span class="mono">{{ selected.file_id || '-' }}</span></div>
          <div><b>客户编码：</b><span class="mono">{{ selected.client_code || '-' }}</span></div>
          <div><b>任务 ID：</b><span class="mono">{{ selected.task_id || '-' }}</span></div>
          <div v-if="selected.error_msg" style="grid-column: span 3;">
            <b>错误信息：</b><span style="color: #b42318">{{ selected.error_msg }}</span>
          </div>
        </div>
        <el-divider content-position="left">Prompt</el-divider>
        <pre class="ctx-text">{{ selected.prompt || '(空)' }}</pre>
        <el-divider content-position="left">Response</el-divider>
        <pre class="ctx-text">{{ selected.response_raw || '(空)' }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listAiApiCalls, getAiApiCallDetail } from '../api.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const detailVisible = ref(false)
const selected = ref(null)

const operationOptions = [
  'detect_and_extract',
  'classify_one_page',
  'detect_page_ranges',
  'summarize_text',
  'detect_large_table_doc',
  'detect_archival',
  'summarize_batch',
  'judge_batch_overall',
  'extract_client_profile_facts',
  'enrich_anchors_with_llm',
  'match_anchors_to_client',
]

function _defaultFilters() {
  const now = new Date()
  const start = new Date(now.getTime() - 7 * 24 * 3600 * 1000)
  const fmt = (d) => {
    const z = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`
  }
  return {
    dateRange: [fmt(start), fmt(now)],
    operation: '',
    status: '',
    model: '',
    batch_id: '',
    file_id: '',
    client_code: '',
    task_id: '',
  }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.operation) out.operation = filters.value.operation
  if (filters.value.status) out.status = filters.value.status
  if (filters.value.model) out.model = filters.value.model.trim()
  if (filters.value.batch_id) out.batch_id = filters.value.batch_id.trim()
  if (filters.value.file_id) out.file_id = filters.value.file_id.trim()
  if (filters.value.client_code) out.client_code = filters.value.client_code.trim()
  if (filters.value.task_id) out.task_id = filters.value.task_id.trim()
  if (filters.value.dateRange?.length === 2) {
    out.since = filters.value.dateRange[0]
    out.until = filters.value.dateRange[1]
  }
  return out
}

async function loadList() {
  loading.value = true
  try {
    const resp = await listAiApiCalls(buildParams())
    items.value = resp.items || []
    total.value = resp.total || 0
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function handleSearch() { currentPage.value = 1; loadList() }
function resetFilters() { filters.value = _defaultFilters(); currentPage.value = 1; loadList() }
function onPageSizeChange() { currentPage.value = 1; loadList() }
async function openDetail(row) {
  // 列表只回预览,点详情按需拉全文(prompt/response)
  selected.value = row
  detailVisible.value = true
  try {
    const full = await getAiApiCallDetail(row.id)
    if (selected.value && selected.value.id === row.id) {
      selected.value = { ...selected.value, prompt: full.prompt, response_raw: full.response_raw, error_msg: full.error_msg }
    }
  } catch {
    // 拉全文失败仍展示预览
  }
}

onMounted(() => { loadList() })
</script>

<style scoped>
.aiapi-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.aiapi-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.aiapi-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #3b82f6, #2563eb); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.aiapi-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filter-grid > .el-input,
.filter-grid > .el-select { width: 160px; flex: 0 0 auto; }
.filter-grid > .date-filter { flex: 0 0 300px; width: 300px; }
.date-filter :deep(.el-range-editor) { width: 300px; }
.filter-grid > .el-button { flex: 0 0 auto; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-body { padding: 4px; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 13px; color: #334155; }
.ctx-text { max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
</style>
