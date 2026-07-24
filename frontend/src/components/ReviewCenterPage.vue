<template>
  <div class="review-center-page">
    <div class="review-center-header">
      <div class="review-center-title">
        <span class="title-indicator"></span>
        复核中心
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">共 {{ total }} 条待复核</span>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="review-center-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.client_name" clearable placeholder="客户姓名" size="small" />
          <el-select v-model="filters.reason" clearable placeholder="复核原因" size="small" style="width: 160px">
            <el-option v-for="o in REVIEW_REASON_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="没有待复核文件" size="default" style="width: 100%">
          <el-table-column label="质量分" width="80" align="center" prop="quality_score" sortable />
          <el-table-column label="复核原因" width="110" align="center">
            <template #default="{ row }">
              <el-tag type="danger" size="small">{{ reviewReasonLabel(row.review_reason) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="客户" min-width="100" show-overflow-tooltip prop="client_name" />
          <el-table-column label="文件名称" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">{{ row.filename || row.file_code }}</template>
          </el-table-column>
          <el-table-column label="类型" width="100" align="center">
            <template #default="{ row }">{{ docTypeLabel(row.doc_type) }}</template>
          </el-table-column>
          <el-table-column label="导入任务" width="90" align="center">
            <template #default="{ row }">#{{ row.import_task_id }}</template>
          </el-table-column>
          <el-table-column label="导入时间" min-width="150">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openItem(row)">复核</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-row">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 25, 50, 100]"
            :total="total"
            layout="total, sizes, prev, pager, next"
            @current-change="loadList"
            @size-change="onPageSizeChange"
          />
        </div>
      </section>
    </div>

    <!-- 复核抽屉(与画像弹窗共用组件;不传 importTaskId = 跨任务全局队列) -->
    <ReviewDrawer ref="drawerRef" @done="loadList" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import ReviewDrawer from './ReviewDrawer.vue'
import { docTypeLabel, reviewReasonLabel, REVIEW_REASON_OPTIONS } from '../utils/labels'
import { listReviewFiles } from '../api'

const items = ref([])
const total = ref(0)
const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(25)
const filters = ref({ client_name: '', reason: null })
const drawerRef = ref(null)

async function loadList() {
  loading.value = true
  try {
    const params = {
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value,
    }
    if (filters.value.client_name?.trim()) params.client_name = filters.value.client_name.trim()
    if (filters.value.reason) params.reason = filters.value.reason
    const data = await listReviewFiles(params)
    items.value = data.items
    total.value = data.total
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  loadList()
}

function resetFilters() {
  filters.value = { client_name: '', reason: null }
  handleSearch()
}

function onPageSizeChange() {
  currentPage.value = 1
  loadList()
}

function openItem(row) {
  drawerRef.value?.open(row)
}

onMounted(() => { loadList() })
</script>

<style scoped>
.review-center-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.review-center-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.review-center-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.review-center-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filter-grid > .el-input,
.filter-grid > .el-select { width: 160px; flex: 0 0 auto; }
.filter-grid > .el-button { flex: 0 0 auto; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
</style>
