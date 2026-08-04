<template>
  <div class="ealog-page">
    <div class="ealog-header">
      <div class="ealog-title">
        <span class="title-indicator"></span>
        调用外部接口记录
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

    <div class="ealog-main">
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
          <el-select v-model="filters.service" clearable placeholder="服务" size="small">
            <el-option label="URL刷新" value="refresh_url" />
            <el-option label="大模型" value="llm" />
          </el-select>
          <el-select v-model="filters.status" clearable placeholder="状态" size="small">
            <el-option label="成功" value="ok" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-input v-model="filters.batch_id" clearable placeholder="批次 ID 模糊查" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无记录" size="default">
          <el-table-column label="时间" width="170">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="服务" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="row.service === 'llm' ? 'primary' : 'warning'" size="small">{{ serviceLabel(row.service) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">{{ row.status === 'ok' ? '成功' : '失败' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="地址" min-width="240" show-overflow-tooltip prop="url" />
          <el-table-column label="耗时" width="90" align="center">
            <template #default="{ row }">{{ row.elapsed_ms != null ? row.elapsed_ms + 'ms' : '-' }}</template>
          </el-table-column>
          <el-table-column label="批次" width="130" show-overflow-tooltip prop="batch_id" />
          <el-table-column label="文件编码" width="110" prop="file_id" />
          <el-table-column label="操作" width="100" align="center">
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

    <el-dialog v-model="detailVisible" title="调用详情" width="70%" top="5vh">
      <div v-if="selected" class="detail-body">
        <div class="detail-meta">
          <div><b>时间：</b><span class="mono">{{ selected.created_at }}</span></div>
          <div><b>服务：</b><el-tag :type="selected.service === 'llm' ? 'primary' : 'warning'" size="small">{{ serviceLabel(selected.service) }}</el-tag></div>
          <div><b>状态：</b><el-tag :type="selected.status === 'ok' ? 'success' : 'danger'" size="small">{{ selected.status === 'ok' ? '成功' : '失败' }}</el-tag></div>
          <div><b>地址：</b><span class="mono">{{ selected.url || '-' }}</span></div>
          <div><b>耗时：</b>{{ selected.elapsed_ms != null ? selected.elapsed_ms + 'ms' : '-' }}</div>
          <div><b>批次：</b><span class="mono">{{ selected.batch_id || '-' }}</span></div>
          <div><b>文件编码：</b><span class="mono">{{ selected.file_id || '-' }}</span></div>
          <div v-if="selected.error_msg" style="grid-column: span 3;">
            <b>错误信息：</b><span style="color: #b42318">{{ selected.error_msg }}</span>
          </div>
        </div>
        <el-divider content-position="left">请求参数 (request_params)</el-divider>
        <pre class="ctx-json">{{ formatJson(selected.request_params) }}</pre>
        <el-divider content-position="left">返回结果 (response_summary)</el-divider>
        <pre class="ctx-json">{{ formatJson(selected.response_summary) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'ExternalApiLogsPage' })
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listExternalApiLogs } from '../api.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const detailVisible = ref(false)
const selected = ref(null)

function _defaultFilters() {
  const now = new Date()
  const start = new Date(now.getTime() - 24 * 3600 * 1000)
  const fmt = (d) => {
    const z = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`
  }
  return { dateRange: [fmt(start), fmt(now)], service: '', status: '', batch_id: '' }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.service) out.service = filters.value.service
  if (filters.value.status) out.status = filters.value.status
  if (filters.value.batch_id) out.batch_id = filters.value.batch_id.trim()
  if (filters.value.dateRange?.length === 2) {
    out.since = filters.value.dateRange[0]
    out.until = filters.value.dateRange[1]
  }
  return out
}

function serviceLabel(s) { return { refresh_url: 'URL刷新', llm: '大模型' }[s] || s || '-' }

async function loadList() {
  loading.value = true
  try {
    const resp = await listExternalApiLogs(buildParams())
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
function openDetail(row) { selected.value = row; detailVisible.value = true }
function formatJson(obj) { return JSON.stringify(obj || {}, null, 2) }

onMounted(() => { loadList() })
</script>

<style scoped>
.ealog-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.ealog-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.ealog-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #10b981, #059669); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.ealog-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: minmax(260px, 1.5fr) 120px 100px minmax(160px, 1fr) auto auto; gap: 10px; align-items: center; }
.date-filter { width: 100%; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-body { padding: 4px; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 13px; color: #334155; }
.ctx-json { max-height: 480px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
</style>