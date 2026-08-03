<template>
  <div class="file-info-page">
    <div class="file-info-header">
      <div class="file-info-title">
        <span class="title-indicator"></span>
        文件信息
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">共 {{ total }} 条</span>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="file-info-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.batch_id" clearable placeholder="批次号" size="small" />
          <el-input v-model="filters.file_id" clearable placeholder="文件编码" size="small" />
          <el-input v-model="filters.filename" clearable placeholder="文件名称" size="small" />
          <el-select v-model="filters.status" clearable placeholder="状态" size="small">
            <el-option label="排队中" value="pending" />
            <el-option label="下载中" value="fetching" />
            <el-option label="OCR中" value="ocr" />
            <el-option label="AI分析中" value="llm" />
            <el-option label="完成" value="done" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-select v-model="filters.verdict" clearable placeholder="判定" size="small">
            <el-option label="符合" value="match" />
            <el-option label="部分符合" value="partial" />
            <el-option label="不符合" value="mismatch" />
            <el-option label="无文字" value="no_text" />
          </el-select>
          <el-input v-model="filters.client_name" clearable placeholder="客户姓名" size="small" />
          <el-input v-model="filters.client_code" clearable placeholder="客户编码" size="small" />
          <el-input v-model="filters.handler" clearable placeholder="办理人" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无文件" size="default" style="width: 100%">
          <el-table-column label="ID" width="80" align="center" prop="id" />
          <el-table-column label="文件编码" min-width="100" show-overflow-tooltip prop="file_id" />
          <el-table-column label="文件名称" min-width="200" show-overflow-tooltip prop="filename" />
          <el-table-column label="文档分类" min-width="110" show-overflow-tooltip prop="doc_category" />
          <el-table-column label="批次号" min-width="150" show-overflow-tooltip prop="batch_id" />
          <el-table-column label="客户" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">
              {{ row.client?.name || row.client?.client_code || '-' }}
            </template>
          </el-table-column>
          <el-table-column label="办理人" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.handler || '-' }}</template>
          </el-table-column>
          <el-table-column label="项目" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.project_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="项目详情" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.project_detail_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="进展" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress?.progress_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
           <el-table-column label="匹配度" width="80" align="center">
            <template #default="{ row }">{{ row.match_score != null ? row.match_score : '-' }}</template>
          </el-table-column>
          <el-table-column label="判定" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="verdictTag(row.verdict)" size="small">{{ verdictLabel(row.verdict) }}</el-tag>
            </template>
          </el-table-column>
           <el-table-column label="耗时" width="90" align="right">
            <template #default="{ row }">{{ row.elapsed_sec != null ? row.elapsed_sec + 's' : '-' }}</template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="150">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center" fixed="right">
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

    <el-dialog v-model="detailVisible" title="文件详情" width="75%" top="5vh">
      <div v-if="selected" v-loading="detailLoading" class="detail-body">
        <div class="detail-meta">
          <div><b>文件编码：</b><span class="mono">{{ selected.file_id || '-' }}</span></div>
          <div><b>文件名称：</b><span>{{ selected.filename || '-' }}</span></div>
          <div><b>批次号：</b><span class="mono">{{ selected.batch_id || '-' }}</span></div>
          <div><b>客户：</b><span>{{ selected.client?.name || selected.client?.client_code || '-' }}</span></div>
          <div><b>办理人：</b><span>{{ selected.progress?.handler || '-' }}</span></div>
          <div><b>项目：</b><span>{{ selected.progress?.project_name || '-' }}</span></div>
          <div><b>项目详情：</b><span>{{ selected.progress?.project_detail_name || '-' }}</span></div>
          <div><b>进展：</b><span>{{ selected.progress?.progress_name || '-' }}</span></div>
          <div><b>状态：</b><el-tag :type="statusTag(selected.status)" size="small">{{ statusLabel(selected.status) }}</el-tag></div>
          <div><b>判定：</b><el-tag :type="verdictTag(selected.verdict)" size="small">{{ verdictLabel(selected.verdict) }}</el-tag></div>
          <div><b>匹配度：</b><span>{{ selected.match_score != null ? selected.match_score : '-' }}</span></div>
          <div><b>序号：</b><span>{{ selected.idx != null ? selected.idx : '-' }}</span></div>
          <div><b>版本：</b><span>{{ selected.version != null ? selected.version : '-' }}</span></div>
          <div><b>文件类型：</b><span>{{ selected.mime_type || '-' }}</span></div>
          <div><b>页数：</b><span>{{ selected.page_count != null ? selected.page_count : '-' }}</span></div>
          <div><b>字符数：</b><span>{{ selected.char_count != null ? selected.char_count : '-' }}</span></div>
          <div><b>是否复用：</b><span>{{ selected.is_reused ? '是' : '否' }}</span></div>
          <div v-if="selected.error_msg" style="grid-column: span 3;">
            <b>错误信息：</b><span style="color: #b42318;">{{ selected.error_msg }}</span>
          </div>
          <div v-if="selected.reason" style="grid-column: span 3;">
            <b>判定依据：</b><span>{{ selected.reason }}</span>
          </div>
        </div>
        <el-divider content-position="left">OCR 文本（已脱敏）</el-divider>
        <pre class="ocr-text">{{ selected.ocr_text || '(空)' }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'FileInfoPage' })
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listFileInfos, getArchiveAdminFileDetail } from '../api.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const detailVisible = ref(false)
const detailLoading = ref(false)
const selected = ref(null)

function _defaultFilters() {
  return {
    batch_id: '',
    file_id: '',
    filename: '',
    status: '',
    verdict: '',
    client_name: '',
    client_code: '',
    handler: '',
  }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.batch_id) out.batch_id = filters.value.batch_id.trim()
  if (filters.value.file_id) out.file_id = filters.value.file_id.trim()
  if (filters.value.filename) out.filename = filters.value.filename.trim()
  if (filters.value.status) out.status = filters.value.status
  if (filters.value.verdict) out.verdict = filters.value.verdict
  if (filters.value.client_name) out.client_name = filters.value.client_name.trim()
  if (filters.value.client_code) out.client_code = filters.value.client_code.trim()
  if (filters.value.handler) out.handler = filters.value.handler.trim()
  return out
}

async function loadList() {
  loading.value = true
  try {
    const resp = await listFileInfos(buildParams())
    items.value = resp.items || []
    total.value = resp.total || 0
  } catch (err) {
    ElMessage.error('加载文件信息失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function handleSearch() { currentPage.value = 1; loadList() }
function resetFilters() { filters.value = _defaultFilters(); currentPage.value = 1; loadList() }
function onPageSizeChange() { currentPage.value = 1; loadList() }

async function openDetail(row) {
  selected.value = { ...row }
  detailVisible.value = true
  detailLoading.value = true
  try {
    const full = await getArchiveAdminFileDetail(row.id)
    if (selected.value && selected.value.id === row.id) {
      selected.value = full
    }
  } catch (err) {
    ElMessage.error('加载文件详情失败：' + (err.response?.data?.detail || err.message))
  } finally {
    detailLoading.value = false
  }
}

function statusTag(s) {
  return {
    pending: 'info',
    fetching: 'info',
    ocr: 'warning',
    llm: 'warning',
    done: 'success',
    error: 'danger',
  }[s] || 'info'
}

function statusLabel(s) {
  return {
    pending: '排队中',
    fetching: '下载中',
    ocr: 'OCR中',
    llm: 'AI分析中',
    done: '完成',
    error: '失败',
  }[s] || s || '-'
}

function verdictTag(v) {
  return {
    match: 'success',
    partial: 'warning',
    mismatch: 'danger',
    no_text: 'info',
  }[v] || 'info'
}

function verdictLabel(v) {
  return {
    match: '符合',
    partial: '部分',
    mismatch: '不符合',
    no_text: '无文字',
  }[v] || v || '-'
}

onMounted(() => { loadList() })
</script>

<style scoped>
.file-info-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.file-info-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.file-info-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.file-info-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filter-grid > .el-input,
.filter-grid > .el-select { width: 160px; flex: 0 0 auto; }
.filter-grid > .el-button { flex: 0 0 auto; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-body { padding: 4px; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 13px; color: #334155; }
.ocr-text { max-height: 420px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
</style>
