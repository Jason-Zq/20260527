<template>
  <div class="prompts-page">
    <div class="prompts-header">
      <div class="prompts-title">
        <span class="title-indicator"></span>
        提示词库
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">共 {{ total }} 条</span>
        <el-button size="default" type="primary" @click="openCreate">
          <el-icon style="margin-right: 4px"><Plus /></el-icon>
          新增
        </el-button>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="prompts-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-input v-model="filters.project_name" clearable placeholder="项目名称模糊查" size="small" />
          <el-input v-model="filters.project_detail_name" clearable placeholder="项目详情模糊查" size="small" />
          <el-input v-model="filters.progress_name" clearable placeholder="进展名称模糊查" size="small" />
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无提示词记录" size="default" style="width: 100%">
          <el-table-column label="项目名称" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.project_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="项目编码" min-width="110" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.project_code || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="项目详情" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.project_detail_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="详情编码" min-width="110" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.project_detail_code || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="进展名称" min-width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ row.progress_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="提示词2(留底标准)" min-width="220">
            <template #default="{ row }">
              <div v-if="row.prompt2" class="prompt-preview" :title="row.prompt2">{{ row.prompt2 }}</div>
              <span v-else class="dim">(空) 首次批次判定时自动生成</span>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="160">
            <template #default="{ row }"><span class="mono dim">{{ row.updated_at }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="220" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openEdit(row)">编辑</el-button>
              <el-button
                size="small"
                type="warning"
                link
                :loading="regeneratingId === row.id"
                @click="onRegenerate(row)"
              >重新生成提示词2</el-button>
              <el-button size="small" type="danger" link @click="onDelete(row)">删除</el-button>
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

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑提示词' : '新增提示词'"
      width="78%"
      top="4vh"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <div class="key-grid">
          <el-form-item label="项目名称">
            <el-input v-model="form.project_name" placeholder="如：新加坡自雇EP" />
          </el-form-item>
          <el-form-item label="项目编码">
            <el-input v-model="form.project_code" placeholder="业务方项目编码" />
          </el-form-item>
          <el-form-item label="项目详情">
            <el-input v-model="form.project_detail_name" placeholder="如：EP申请" />
          </el-form-item>
          <el-form-item label="项目详情编码">
            <el-input v-model="form.project_detail_code" placeholder="业务方项目详情编码" />
          </el-form-item>
          <el-form-item label="进展名称">
            <el-input v-model="form.progress_name" placeholder="如：递交申请" />
          </el-form-item>
        </div>
        <div class="dim key-hint">
          业务键 = 以上五字段组合；同一组合仅一条记录。批次判定时按该组合查找提示词。
        </div>
        <el-form-item>
          <template #label>
            <span>提示词1（批次总体判定模板）</span>
            <el-button size="small" link type="primary" style="margin-left: 8px" @click="fillDefaultTemplate">
              重置为默认模板
            </el-button>
          </template>
          <el-input
            v-model="form.prompt1"
            type="textarea"
            :rows="12"
            placeholder="留空保存时自动填入默认模板；支持占位符 {user_prompt} {files_detail} {name_header} {stage_hint} {n_files}"
          />
        </el-form-item>
        <el-form-item label="提示词2（该项目留底标准）">
          <el-input
            v-model="form.prompt2"
            type="textarea"
            :rows="8"
            placeholder="留空将在首次批次判定时由 AI 按五字段自动生成"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'ArchivePromptsPage' })
import { ref, onMounted } from 'vue'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listArchivePrompts, createArchivePrompt, updateArchivePrompt,
  deleteArchivePrompt, regenerateArchivePrompt2, getArchivePromptDefaultTemplate,
} from '../api.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)

const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const regeneratingId = ref(null)

function _defaultFilters() {
  return { project_name: '', project_detail_name: '', progress_name: '' }
}
const filters = ref(_defaultFilters())

function emptyForm() {
  return {
    project_name: '',
    project_code: '',
    project_detail_name: '',
    project_detail_code: '',
    progress_name: '',
    prompt1: '',
    prompt2: '',
  }
}
const form = ref(emptyForm())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  for (const k of ['project_name', 'project_detail_name', 'progress_name']) {
    const v = filters.value[k].trim()
    if (v) out[k] = v
  }
  return out
}

async function loadList() {
  loading.value = true
  try {
    const resp = await listArchivePrompts(buildParams())
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

function openCreate() {
  editingId.value = null
  form.value = emptyForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.value = {
    project_name: row.project_name || '',
    project_code: row.project_code || '',
    project_detail_name: row.project_detail_name || '',
    project_detail_code: row.project_detail_code || '',
    progress_name: row.progress_name || '',
    prompt1: row.prompt1 || '',
    prompt2: row.prompt2 || '',
  }
  dialogVisible.value = true
}

async function fillDefaultTemplate() {
  try {
    const resp = await getArchivePromptDefaultTemplate()
    form.value.prompt1 = resp.template || ''
  } catch (err) {
    ElMessage.error('获取默认模板失败：' + (err.response?.data?.detail || err.message))
  }
}

async function onSave() {
  saving.value = true
  try {
    const payload = { ...form.value }
    if (editingId.value) {
      await updateArchivePrompt(editingId.value, payload)
      ElMessage.success('已保存')
    } else {
      await createArchivePrompt(payload)
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    loadList()
  } catch (err) {
    ElMessage.error('保存失败：' + (err.response?.data?.detail || err.message))
  } finally {
    saving.value = false
  }
}

async function onDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除「${row.project_name || '-'} / ${row.progress_name || '-'}」的提示词记录？下次同五元组批次判定时会自动重建。`,
      '删除提示词',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteArchivePrompt(row.id)
    ElMessage.success('已删除')
    loadList()
  } catch (err) {
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
  }
}

async function onRegenerate(row) {
  try {
    await ElMessageBox.confirm(
      '将按五字段调用大模型重新生成提示词2并覆盖现有内容，确认继续？',
      '重新生成提示词2',
      { type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  regeneratingId.value = row.id
  try {
    const resp = await regenerateArchivePrompt2(row.id)
    row.prompt2 = resp.prompt2
    ElMessage.success('提示词2 已重新生成')
  } catch (err) {
    ElMessage.error('生成失败：' + (err.response?.data?.detail || err.message))
  } finally {
    regeneratingId.value = null
  }
}

onMounted(() => { loadList() })
</script>

<style scoped>
.prompts-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.prompts-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.prompts-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #3b82f6, #2563eb); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.prompts-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.filter-grid > .el-input { width: 180px; flex: 0 0 auto; }
.filter-grid > .el-button { flex: 0 0 auto; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.prompt-preview { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 12px; color: #475569; line-height: 1.5; }
.key-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 14px; }
.key-hint { font-size: 12px; margin: -6px 0 12px; }
</style>
