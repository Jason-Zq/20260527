<template>
  <div class="assign-page">
    <div class="assign-header">
      <div class="assign-title">
        <span class="title-indicator"></span>
        文件归属
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">共 {{ total }} 个文件</span>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="assign-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.client_name" clearable placeholder="客户姓名" size="small" />
          <el-select v-model="filters.doc_type" clearable placeholder="证件类型" size="small" style="width: 140px">
            <el-option v-for="o in DOC_TYPE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-select v-model="filters.assigned" clearable placeholder="归属状态" size="small" style="width: 120px">
            <el-option label="未归属" value="none" />
            <el-option label="已归属" value="any" />
          </el-select>
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="没有文件" size="default" style="width: 100%">
          <el-table-column label="客户" min-width="100" show-overflow-tooltip prop="client_name" />
          <el-table-column label="文件名称" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">{{ row.filename || row.file_code }}</template>
          </el-table-column>
          <el-table-column label="类型" width="100" align="center">
            <template #default="{ row }">{{ docTypeLabel(row.doc_type) }}</template>
          </el-table-column>
          <el-table-column label="归属人" width="130" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.person_name" :type="row.attributed_by === 'manual' ? 'primary' : 'info'" size="small">
                {{ row.person_name }}
              </el-tag>
              <el-tag v-else type="warning" size="small">未归属</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="导入任务" width="90" align="center">
            <template #default="{ row }">#{{ row.import_task_id }}</template>
          </el-table-column>
          <el-table-column label="导入时间" min-width="150">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openAssign(row)">归属</el-button>
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

    <!-- 归属弹窗:左原件预览 | 右归属人选择 -->
    <el-dialog v-model="assignVisible" title="指定归属人" width="72%" top="5vh" @closed="revokeRaw">
      <div v-if="assignRow" class="assign-layout">
        <div class="pane">
          <div class="pane-title">原件 · {{ assignRow.filename || assignRow.file_code }}</div>
          <div class="pane-body raw-view">
            <img v-if="rawState.url && rawState.isImage" :src="rawState.url" class="raw-img" alt="原件" />
            <iframe v-else-if="rawState.url" :src="rawState.url" class="raw-iframe" title="原件"></iframe>
            <span v-else class="dim">{{ rawState.hint }}</span>
          </div>
        </div>
        <div class="assign-side">
          <div class="pane-title">归属人</div>
          <div v-loading="personsLoading">
            <el-select v-model="assignPersonId" placeholder="选择归属人" clearable style="width: 100%">
              <el-option
                v-for="p in householdPersons" :key="p.id"
                :label="`${p.name}(${p.is_main ? '客户' : relationLabel(p.relation_to_main)})`" :value="p.id"
              />
            </el-select>
            <el-input v-model="assignNewName" placeholder="或输入新建人姓名" size="default" style="margin-top: 10px" />
            <div class="assign-actions">
              <el-button type="primary" :loading="assignSaving" @click="saveAssign">保存</el-button>
              <el-button
                v-if="assignRow.person_id" type="danger" link :loading="assignSaving"
                @click="clearAssign"
              >清除归属</el-button>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { docTypeLabel, DOC_TYPE_OPTIONS, relationLabel } from '../utils/labels'
import { assignFilePerson, fetchCustomerFileRawUrl, listFilesForAssign, listHouseholdPersons } from '../api'

const items = ref([])
const total = ref(0)
const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(25)
const filters = ref({ client_name: '', doc_type: null, assigned: null })

async function loadList() {
  loading.value = true
  try {
    const params = {
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value,
    }
    if (filters.value.client_name?.trim()) params.client_name = filters.value.client_name.trim()
    if (filters.value.doc_type) params.doc_type = filters.value.doc_type
    if (filters.value.assigned) params.assigned = filters.value.assigned
    const data = await listFilesForAssign(params)
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
  filters.value = { client_name: '', doc_type: null, assigned: null }
  handleSearch()
}

function onPageSizeChange() {
  currentPage.value = 1
  loadList()
}

// ---- 归属弹窗 ----
const assignVisible = ref(false)
const assignRow = ref(null)
const assignPersonId = ref(null)
const assignNewName = ref('')
const assignSaving = ref(false)
const householdPersons = ref([])
const personsLoading = ref(false)
const rawState = ref({ url: '', isImage: false, hint: '原件加载中…', _revoke: null })
const _personsCache = new Map() // household_id → persons

async function openAssign(row) {
  assignRow.value = row
  assignPersonId.value = row.person_id || null
  assignNewName.value = ''
  assignVisible.value = true
  revokeRaw()
  rawState.value = { url: '', isImage: false, hint: '原件加载中…', _revoke: null }
  try {
    const raw = await fetchCustomerFileRawUrl(row.id)
    rawState.value = { url: raw.blobUrl, isImage: (raw.mime || '').startsWith('image/'), hint: '', _revoke: raw.revoke }
  } catch (err) {
    rawState.value = { url: '', isImage: false, hint: '原件不可用(可能已清理且无法重下)', _revoke: null }
  }
  await loadPersons(row.household_id)
}

async function loadPersons(householdId) {
  if (!householdId) {
    householdPersons.value = []
    return
  }
  if (_personsCache.has(householdId)) {
    householdPersons.value = _personsCache.get(householdId)
    return
  }
  personsLoading.value = true
  try {
    const ps = await listHouseholdPersons(householdId)
    _personsCache.set(householdId, ps)
    householdPersons.value = ps
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
    householdPersons.value = []
  } finally {
    personsLoading.value = false
  }
}

async function saveAssign() {
  const payload = {}
  if (assignNewName.value.trim()) payload.new_person_name = assignNewName.value.trim()
  else if (assignPersonId.value) payload.person_id = assignPersonId.value
  else {
    ElMessage.warning('请选择归属人或输入新建人姓名')
    return
  }
  assignSaving.value = true
  try {
    const r = await assignFilePerson(assignRow.value.id, payload)
    ElMessage.success(r?.deduped ? '归属已保存（已存在同名人员，已自动关联，未重复建卡）' : '归属已保存')
    _personsCache.clear() // 可能新建了人,缓存失效
    assignVisible.value = false
    await loadList()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    assignSaving.value = false
  }
}

async function clearAssign() {
  assignSaving.value = true
  try {
    await assignFilePerson(assignRow.value.id, { person_id: null })
    ElMessage.success('已清除归属')
    assignVisible.value = false
    await loadList()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    assignSaving.value = false
  }
}

function revokeRaw() {
  if (rawState.value._revoke) rawState.value._revoke()
  rawState.value = { url: '', isImage: false, hint: '原件加载中…', _revoke: null }
}

onMounted(() => { loadList() })
</script>

<style scoped>
.assign-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.assign-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.assign-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.assign-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filter-grid > .el-input { width: 160px; flex: 0 0 auto; }
.filter-grid > .el-select { flex: 0 0 auto; }
.filter-grid > .el-button { flex: 0 0 auto; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.assign-layout { display: flex; gap: 14px; }
.pane { flex: 1; min-width: 0; }
.pane-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; color: #606266; }
.pane-body { background: #f5f7fa; border: 1px solid #e4e7ed; border-radius: 6px; padding: 10px; height: 62vh; overflow: auto; }
.raw-view { display: flex; align-items: flex-start; justify-content: center; }
.raw-img { max-width: 100%; max-height: 100%; }
.raw-iframe { width: 100%; height: 100%; border: none; }
.assign-side { width: 260px; flex-shrink: 0; }
.assign-actions { margin-top: 14px; display: flex; align-items: center; gap: 10px; }
</style>
