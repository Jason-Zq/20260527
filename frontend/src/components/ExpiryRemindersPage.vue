<template>
  <div class="expiry-page">
    <div class="page-header">
      <div class="page-title"><span class="title-indicator"></span>证件到期提醒</div>
      <el-button size="default" @click="load" :loading="loading">刷新</el-button>
    </div>

    <div class="page-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.keyword" clearable placeholder="客户姓名 / 成员姓名" size="small" @keyup.enter="search" />
          <el-input-number v-model="filters.days" :min="1" :max="3650" controls-position="right" size="small" placeholder="预警天数" />
          <el-checkbox v-model="filters.include_ok" size="small">含正常证件</el-checkbox>
          <el-button type="primary" size="small" @click="search">查询</el-button>
          <el-button size="small" @click="reset">重置</el-button>
        </div>
      </section>

      <section class="card">
        <div class="table-head"><span>到期列表</span><span class="dim">共 {{ total }} 条</span></div>
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无到期提醒">
          <el-table-column label="客户" min-width="110" show-overflow-tooltip prop="household_name" />
          <el-table-column label="客户编码" min-width="150" show-overflow-tooltip prop="customer_code">
            <template #default="{ row }">{{ row.customer_code || '-' }}</template>
          </el-table-column>
          <el-table-column label="成员" min-width="120" show-overflow-tooltip prop="person_name" />
          <el-table-column label="关系" width="80" align="center">
            <template #default="{ row }">{{ relationLabel(row.relation_to_main) }}</template>
          </el-table-column>
          <el-table-column label="证件类型" width="110" align="center" prop="credential_type" />
          <el-table-column label="到期日期" width="115" align="center" prop="expiry_date" />
          <el-table-column label="剩余天数" width="100" align="center" sortable prop="days_left">
            <template #default="{ row }">
              <strong v-if="row.days_left < 0" class="expired-text">已过期 {{ -row.days_left }} 天</strong>
              <strong v-else>{{ row.days_left }} 天</strong>
            </template>
          </el-table-column>
          <el-table-column label="级别" width="90" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.level === 'expired'" type="danger" size="small">已过期</el-tag>
              <el-tag v-else-if="row.level === 'expiring'" type="warning" size="small">即将到期</el-tag>
              <el-tag v-else type="success" size="small">正常</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="数据状态" width="90" align="center">
            <template #default="{ row }">
              <el-tooltip :content="row.field_status === 'ai' ? 'AI 提取,未经人工确认' : '人工已确认/修正'" placement="top">
                <span :class="['status-dot', row.field_status === 'ai' ? 'ai' : 'human']">{{ row.field_status === 'ai' ? 'AI' : '人工' }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :total="total"
            :page-sizes="[10, 25, 50, 100]"
            layout="total, sizes, prev, pager, next, jumper"
            small
            background
            @size-change="onPageSize"
            @current-change="load"
          />
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'ExpiryRemindersPage' })
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listExpiryReminders } from '../api.js'
import { relationLabel } from '../utils/labels.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(25)
const filters = ref({ keyword: '', days: 180, include_ok: false })

function params() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.keyword) out.keyword = filters.value.keyword
  if (filters.value.days) out.days = filters.value.days
  if (filters.value.include_ok) out.include_ok = true
  return out
}

async function load() {
  loading.value = true
  try {
    const resp = await listExpiryReminders(params())
    items.value = resp.items || []
    total.value = resp.total || 0
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function search() { currentPage.value = 1; load() }
function reset() { filters.value = { keyword: '', days: 180, include_ok: false }; currentPage.value = 1; load() }
function onPageSize() { currentPage.value = 1; load() }

onMounted(load)
</script>

<style scoped>
.expiry-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.page-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.page-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #fb923c, #f59e0b); border-radius: 2px; }
.page-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: 240px 140px 110px auto auto; gap: 10px; align-items: center; }
.table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; font-weight: 600; color: #1e293b; }
.dim { color: #94a3b8; }
.expired-text { color: #dc2626; }
.status-dot { font-size: 12px; font-weight: 600; }
.status-dot.ai { color: #d97706; }
.status-dot.human { color: #16a34a; }
.pagination-row { display: flex; justify-content: flex-end; padding-top: 12px; }
</style>
