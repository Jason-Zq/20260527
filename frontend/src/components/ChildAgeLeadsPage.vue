<template>
  <div class="child-age-page">
    <div class="page-header">
      <div class="page-title"><span class="title-indicator"></span>子女年龄线索</div>
      <el-button size="default" @click="load" :loading="loading">刷新</el-button>
    </div>

    <div class="page-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.keyword" clearable placeholder="客户姓名 / 编码 / 子女姓名" size="small" @keyup.enter="search" />
          <el-input-number v-model="filters.min_age" :min="0" :max="100" controls-position="right" size="small" placeholder="最小年龄" />
          <el-input-number v-model="filters.max_age" :min="0" :max="100" controls-position="right" size="small" placeholder="最大年龄" />
          <el-button type="primary" size="small" @click="search">查询</el-button>
          <el-button size="small" @click="reset">重置</el-button>
        </div>
      </section>

      <section class="card">
        <div class="table-head"><span>年龄列表</span><span class="dim">共 {{ total }} 条</span></div>
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无子女年龄数据">
          <el-table-column label="客户姓名" min-width="120" show-overflow-tooltip prop="client_name" />
          <el-table-column label="客户编码" min-width="120" show-overflow-tooltip prop="client_code" />
          <el-table-column label="子女姓名" min-width="120" show-overflow-tooltip prop="child_name" />
          <el-table-column label="关系" width="90" align="center" prop="relation" />
          <el-table-column label="出生日期" width="120" align="center" prop="birth_date" />
          <el-table-column label="年龄" width="110" align="center">
            <template #default="{ row }"><strong>{{ row.age_text }}</strong></template>
          </el-table-column>
          <el-table-column label="操作" width="130" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openClient(row)">查看客户档案</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :total="total"
            :page-sizes="[20, 50, 100]"
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
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { listChildAgeLeads } from '../api.js'

const router = useRouter()
const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const filters = ref({ keyword: '', min_age: null, max_age: null })

function params() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.keyword) out.keyword = filters.value.keyword
  if (filters.value.min_age != null) out.min_age = filters.value.min_age
  if (filters.value.max_age != null) out.max_age = filters.value.max_age
  return out
}

async function load() {
  loading.value = true
  try {
    const resp = await listChildAgeLeads(params())
    items.value = resp.items || []
    total.value = resp.total || 0
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function search() { currentPage.value = 1; load() }
function reset() { filters.value = { keyword: '', min_age: null, max_age: null }; currentPage.value = 1; load() }
function onPageSize() { currentPage.value = 1; load() }
function openClient(row) { router.push(`/clients/${row.client_id}`) }

onMounted(load)
</script>

<style scoped>
.child-age-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.page-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.page-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #fb923c, #f59e0b); border-radius: 2px; }
.page-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: 260px 140px 140px auto auto; gap: 10px; align-items: center; }
.table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; font-weight: 600; color: #1e293b; }
.dim { color: #94a3b8; }
.pagination-row { display: flex; justify-content: flex-end; padding-top: 12px; }
</style>
