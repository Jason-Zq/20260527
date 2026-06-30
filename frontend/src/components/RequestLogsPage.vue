<template>
  <div class="rlogs-page">
    <div class="rlogs-header">
      <div class="rlogs-title">
        <span class="title-indicator"></span>
        请求记录
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">
          数据库保留 30 天 · 共 {{ total }} 条 · 仅记录 POST /business/batch
        </span>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="rlogs-main">
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
          <el-select v-model="filters.method" clearable placeholder="方法" size="small">
            <el-option label="GET" value="GET" />
            <el-option label="POST" value="POST" />
          </el-select>
          <el-select v-model="filters.source" clearable placeholder="来源" size="small" class="source-filter">
            <el-option label="业务调用" value="business">
              <el-tag size="small" type="warning">business</el-tag>
            </el-option>
            <el-option label="后台查看" value="admin">
              <el-tag size="small" type="info">admin</el-tag>
            </el-option>
            <el-option label="轮询" value="poll">
              <el-tag size="small" type="info">poll</el-tag>
            </el-option>
            <el-option label="其他" value="other">
              <el-tag size="small">other</el-tag>
            </el-option>
          </el-select>
          <el-input v-model="filters.path" clearable placeholder="路径片段模糊查" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无记录" size="default">
          <el-table-column label="时间" width="170">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="来源" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="sourceTag(row.source)" size="small">{{ sourceLabel(row.source) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="方法" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.method === 'POST' ? 'warning' : 'info'" size="small">{{ row.method }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="路径" min-width="280" show-overflow-tooltip prop="path" />
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.response_status)" size="small">{{ row.response_status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="80" align="center">
            <template #default="{ row }">{{ row.elapsed_ms ? row.elapsed_ms + 'ms' : '-' }}</template>
          </el-table-column>
          <el-table-column label="IP" width="140" show-overflow-tooltip prop="client_ip" />
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
            :page-sizes="[50, 100, 200]"
            :total="total"
            layout="total, sizes, prev, pager, next"
            @current-change="loadList"
            @size-change="onPageSizeChange"
          />
        </div>
      </section>
    </div>

    <el-dialog v-model="detailVisible" title="请求详情" width="60%" top="8vh">
      <div v-if="selected" class="detail-body">
        <div class="detail-meta">
          <div><b>时间：</b><span class="mono">{{ selected.created_at }}</span></div>
          <div><b>方法：</b>{{ selected.method }}</div>
          <div><b>路径：</b><span class="mono">{{ selected.path }}</span></div>
          <div>
            <b>状态：</b>
            <el-tag :type="statusTag(selected.response_status)" size="small">{{ selected.response_status }}</el-tag>
          </div>
          <div><b>耗时：</b>{{ selected.elapsed_ms ? selected.elapsed_ms + 'ms' : '-' }}</div>
          <div><b>IP：</b>{{ selected.client_ip || '-' }}</div>
        </div>
        <el-divider content-position="left">请求参数 (request_body)</el-divider>
        <pre class="ctx-json">{{ JSON.stringify(selected.request_body || {}, null, 2) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listRequestLogs } from '../api.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const detailVisible = ref(false)
const selected = ref(null)

function _defaultFilters() {
  const now = new Date()
  const start = new Date(now.getTime() - 24 * 3600 * 1000)
  const fmt = (d) => {
    const z = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`
  }
  return { dateRange: [fmt(start), fmt(now)], method: '', path: '', source: '' }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.source) out.source = filters.value.source
  if (filters.value.method) out.method = filters.value.method
  if (filters.value.path) out.path = filters.value.path.trim()
  if (filters.value.dateRange?.length === 2) {
    out.since = filters.value.dateRange[0]
    out.until = filters.value.dateRange[1]
  }
  return out
}

function sourceTag(s) {
  return { business: 'warning', admin: 'info', poll: 'info' }[s] || ''
}
function sourceLabel(s) {
  return { business: '业务', admin: '后台', poll: '轮询', other: '其他' }[s] || s || '-'
}

async function loadList() {
  loading.value = true
  try {
    const resp = await listRequestLogs(buildParams())
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

function statusTag(code) {
  if (!code) return 'info'
  if (code >= 200 && code < 300) return 'success'
  if (code >= 400 && code < 500) return 'warning'
  if (code >= 500) return 'danger'
  return 'info'
}

onMounted(() => { loadList() })
</script>

<style scoped>
.rlogs-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.rlogs-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.rlogs-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #10b981, #059669); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.rlogs-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: minmax(280px, 1.5fr) 110px 120px minmax(180px, 1fr) auto auto; gap: 10px; align-items: center; }
.date-filter { width: 100%; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-body { padding: 4px; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 13px; color: #334155; }
.message-text { line-height: 1.7; color: #334155; background: #f8fafc; padding: 10px 12px; border-radius: 8px; margin: 0; }
.message-text.error-text { color: #b42318; background: #fef3f2; }
.ctx-json { max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
</style>
