<template>
  <div class="events-page">
    <div class="events-header">
      <div class="events-title">
        <span class="title-indicator"></span>
        事件流
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">
          数据库保留 30 天 · 共 {{ total }} 条
        </span>
        <el-button size="default" @click="loadEvents" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="events-main">
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
          <el-select v-model="filters.severities" multiple collapse-tags collapse-tags-tooltip placeholder="级别(默认 warn+)" size="small" class="severity-filter">
            <el-option label="info" value="info">
              <el-tag size="small" type="info">info</el-tag>
            </el-option>
            <el-option label="warn" value="warn">
              <el-tag size="small" type="warning">warn</el-tag>
            </el-option>
            <el-option label="error" value="error">
              <el-tag size="small" type="danger">error</el-tag>
            </el-option>
            <el-option label="critical" value="critical">
              <el-tag size="small" type="danger" effect="dark">critical</el-tag>
            </el-option>
          </el-select>
          <el-select v-model="filters.categories" multiple collapse-tags collapse-tags-tooltip placeholder="类别" size="small" class="category-filter" filterable>
            <el-option v-for="c in availableCategories" :key="c" :label="c" :value="c" />
          </el-select>
          <el-input v-model="filters.batchId" clearable placeholder="batch_id 查相关事件" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <div class="table-head">
          <span>事件列表</span>
          <span class="dim" style="font-size: 12px">
            默认显示最近 24 小时的 warn / error / critical(隐藏 info 噪声)
          </span>
        </div>
        <el-table :data="events" v-loading="loading" stripe empty-text="暂无事件" size="default">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">
              <span class="mono dim">{{ row.occurred_at }}</span>
            </template>
          </el-table-column>
          <el-table-column label="级别" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="severityTag(row.severity)" :effect="row.severity === 'critical' ? 'dark' : 'light'" size="small">
                {{ row.severity }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类别" width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="mono">{{ row.category }}</span>
            </template>
          </el-table-column>
          <el-table-column label="消息" min-width="320">
            <template #default="{ row }">
              <div>{{ row.message }}</div>
            </template>
          </el-table-column>
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
            @current-change="loadEvents"
            @size-change="onPageSizeChange"
          />
        </div>
      </section>
    </div>

    <el-dialog v-model="detailVisible" title="事件详情" width="60%" top="8vh">
      <div v-if="selected" class="detail-body">
        <div class="detail-meta">
          <div><b>时间：</b><span class="mono">{{ selected.occurred_at }}</span></div>
          <div><b>级别：</b>
            <el-tag :type="severityTag(selected.severity)" :effect="selected.severity === 'critical' ? 'dark' : 'light'" size="small">
              {{ selected.severity }}
            </el-tag>
          </div>
          <div><b>类别：</b><span class="mono">{{ selected.category }}</span></div>
          <div><b>事件 ID：</b><span class="mono">{{ selected.id }}</span></div>
        </div>
        <el-divider content-position="left">消息</el-divider>
        <p class="message-text">{{ selected.message }}</p>
        <el-divider content-position="left">上下文 (context)</el-divider>
        <pre class="ctx-json">{{ JSON.stringify(selected.context || {}, null, 2) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listSystemEvents, listEventCategories } from '../api.js'

const loading = ref(false)
const events = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const detailVisible = ref(false)
const selected = ref(null)
const availableCategories = ref([])

function _defaultFilters() {
  // 默认:最近 24h,severity = warn/error/critical(隐藏 info 噪声)
  const now = new Date()
  const start = new Date(now.getTime() - 24 * 3600 * 1000)
  const fmt = (d) => {
    const z = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`
  }
  return {
    dateRange: [fmt(start), fmt(now)],
    severities: ['warn', 'error', 'critical'],
    categories: [],
    batchId: '',
  }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.severities?.length) out.severity = filters.value.severities.join(',')
  if (filters.value.categories?.length) out.category = filters.value.categories.join(',')
  if (filters.value.batchId) out.batch_id = filters.value.batchId.trim()
  if (filters.value.dateRange?.length === 2) {
    out.since = filters.value.dateRange[0]
    out.until = filters.value.dateRange[1]
  }
  return out
}

async function loadEvents() {
  loading.value = true
  try {
    const resp = await listSystemEvents(buildParams())
    events.value = resp.items || []
    total.value = resp.total || 0
  } catch (err) {
    ElMessage.error('加载事件失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

async function loadCategories() {
  try {
    const resp = await listEventCategories()
    availableCategories.value = resp.categories || []
  } catch (err) {
    // 失败不打断,categories 为空时用户也能输入自由值
    availableCategories.value = []
  }
}

function handleSearch() {
  currentPage.value = 1
  loadEvents()
}

function resetFilters() {
  filters.value = _defaultFilters()
  currentPage.value = 1
  loadEvents()
}

function onPageSizeChange() {
  currentPage.value = 1
  loadEvents()
}

function openDetail(row) {
  selected.value = row
  detailVisible.value = true
}

function severityTag(s) {
  return { info: 'info', warn: 'warning', error: 'danger', critical: 'danger' }[s] || 'info'
}

onMounted(() => {
  loadCategories()
  loadEvents()
})
</script>

<style scoped>
.events-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.events-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.events-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #4f46e5); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.events-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: minmax(280px, 1.5fr) minmax(180px, 1fr) minmax(180px, 1fr) minmax(160px, 1fr) auto auto; gap: 10px; align-items: center; }
.date-filter { width: 100%; }
.table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; font-weight: 600; color: #1e293b; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-body { padding: 4px; }
.detail-meta { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; font-size: 13px; color: #334155; }
.message-text { line-height: 1.7; color: #334155; background: #f8fafc; padding: 10px 12px; border-radius: 8px; margin: 0; }
.ctx-json { max-height: 360px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
@media (max-width: 1200px) { .filter-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
