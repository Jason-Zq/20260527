<template>
  <div class="rules-page">
    <div class="rules-header">
      <div class="rules-title">
        <span class="title-indicator"></span>
        提取规则
      </div>
      <div class="header-actions">
        <span class="dim" style="font-size: 12px; margin-right: 12px">
          共 {{ total }} 条 · 每证件类型至多 1 条 active
        </span>
        <el-button size="default" @click="loadList" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="rules-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-select v-model="filters.doc_type" clearable placeholder="证件类型" size="small">
            <el-option v-for="t in docTypeOptions" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
          <el-select v-model="filters.status" clearable placeholder="状态" size="small">
            <el-option label="激活" value="active" />
            <el-option label="草稿" value="draft" />
            <el-option label="停用" value="disabled" />
          </el-select>
          <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
        </div>
      </section>

      <section class="card">
        <el-table :data="items" v-loading="loading" stripe empty-text="暂无规则" size="default">
          <el-table-column label="ID" width="70" prop="id" align="center" />
          <el-table-column label="证件类型" width="140">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ docTypeLabel(row.doc_type) }}</el-tag>
              <span class="mono dim" style="margin-left: 6px">{{ row.doc_type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="版本" width="70" align="center">
            <template #default="{ row }"><span class="mono">v{{ row.version }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="字段数" width="80" align="center">
            <template #default="{ row }">{{ (row.fields || []).length }}</template>
          </el-table-column>
          <el-table-column label="起草" width="90" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.drafted_by === 'ai'" size="small" type="warning">AI</el-tag>
              <span v-else class="dim">{{ row.drafted_by || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="审核人" width="120" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ dim: !row.reviewed_by }">{{ row.reviewed_by || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="审核时间" width="170">
            <template #default="{ row }"><span class="mono dim">{{ row.reviewed_at || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="更新时间" width="170">
            <template #default="{ row }"><span class="mono dim">{{ row.updated_at }}</span></template>
          </el-table-column>
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
            :page-sizes="[10, 25, 50, 100]"
            :total="total"
            layout="total, sizes, prev, pager, next"
            @current-change="loadList"
            @size-change="onPageSizeChange"
          />
        </div>
      </section>
    </div>

    <el-dialog v-model="detailVisible" title="规则详情" width="70%" top="6vh">
      <div v-if="selected" class="detail-body">
        <div class="detail-meta">
          <div><b>ID：</b><span class="mono">{{ selected.id }}</span></div>
          <div><b>证件类型：</b>{{ docTypeLabel(selected.doc_type) }} <span class="mono dim">({{ selected.doc_type }})</span></div>
          <div><b>版本：</b><span class="mono">v{{ selected.version }}</span></div>
          <div>
            <b>状态：</b>
            <el-tag :type="statusTag(selected.status)" size="small">{{ statusLabel(selected.status) }}</el-tag>
          </div>
          <div><b>起草：</b>{{ selected.drafted_by || '-' }}</div>
          <div><b>审核人：</b>{{ selected.reviewed_by || '-' }}</div>
          <div><b>审核时间：</b><span class="mono">{{ selected.reviewed_at || '-' }}</span></div>
          <div><b>创建时间：</b><span class="mono">{{ selected.created_at }}</span></div>
          <div><b>更新时间：</b><span class="mono">{{ selected.updated_at }}</span></div>
        </div>

        <el-divider content-position="left">字段定义 ({{ (selected.fields || []).length }} 个)</el-divider>
        <el-table :data="selected.fields || []" stripe size="small" empty-text="无字段">
          <el-table-column label="字段" min-width="130">
            <template #default="{ row }">
              <span class="mono">{{ row.key }}</span>
              <el-tag v-if="row.required" size="small" type="danger" style="margin-left: 6px">必填</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="显示名" width="120" prop="label" />
          <el-table-column label="归属" width="180">
            <template #default="{ row }">
              <span v-if="row.target">
                <el-tag size="small" type="info">{{ row.target.entity || 'person' }}</el-tag>
                <span class="mono" style="margin-left: 6px">{{ row.target.column || row.target.field || '-' }}</span>
              </span>
              <span v-else class="dim">-</span>
            </template>
          </el-table-column>
          <el-table-column label="示例" min-width="150" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono dim">{{ row.example || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="说明" min-width="200" show-overflow-tooltip prop="description" />
        </el-table>

        <el-divider content-position="left">类型级 prompt (prompt_extra)</el-divider>
        <pre class="ctx-json" v-if="selected.prompt_extra">{{ selected.prompt_extra }}</pre>
        <div v-else class="dim" style="padding: 8px 0">（无）</div>

        <el-divider content-position="left">原始 fields JSON</el-divider>
        <pre class="ctx-json">{{ JSON.stringify(selected.fields || [], null, 2) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listDocExtractRules, getDocExtractRule } from '../api.js'
import { docTypeLabel } from '../utils/labels.js'

const loading = ref(false)
const items = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(25)
const detailVisible = ref(false)
const selected = ref(null)

const docTypeOptions = [
  { value: 'id_card', label: '身份证' },
  { value: 'hukou', label: '户口本' },
  { value: 'passport', label: '护照' },
  { value: 'birth_cert', label: '出生证明' },
  { value: 'degree_cert', label: '学位证' },
  { value: 'marriage_cert', label: '结婚证' },
  { value: 'no_crime', label: '无犯罪' },
  { value: 'property_cert', label: '房产证' },
  { value: 'kyc_form', label: 'KYC表' },
  { value: 'approval', label: '批复' },
  { value: 'submission', label: '递交包' },
  { value: 'receipt', label: '签收回执' },
]

function _defaultFilters() {
  return { doc_type: '', status: '' }
}

const filters = ref(_defaultFilters())

function buildParams() {
  const out = { limit: pageSize.value, offset: (currentPage.value - 1) * pageSize.value }
  if (filters.value.doc_type) out.doc_type = filters.value.doc_type
  if (filters.value.status) out.status = filters.value.status
  return out
}

function statusTag(s) {
  return { active: 'success', draft: 'warning', disabled: 'info' }[s] || ''
}
function statusLabel(s) {
  return { active: '激活', draft: '草稿', disabled: '停用' }[s] || s || '-'
}

async function loadList() {
  loading.value = true
  try {
    const resp = await listDocExtractRules(buildParams())
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
  // 列表已含所有字段,详情弹窗直接用 row;仍走一次 getDocExtractRule 保证与 DB 最新一致。
  try {
    selected.value = await getDocExtractRule(row.id)
  } catch (err) {
    selected.value = row
  }
  detailVisible.value = true
}

onMounted(() => { loadList() })
</script>

<style scoped>
.rules-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.rules-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.rules-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #10b981, #059669); border-radius: 2px; }
.header-actions { display: flex; align-items: center; }
.rules-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.filter-grid { display: grid; grid-template-columns: 180px 140px auto auto 1fr; gap: 10px; align-items: center; }
.pagination-row { display: flex; justify-content: flex-end; margin-top: 12px; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.detail-body { padding: 4px; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 13px; color: #334155; }
.ctx-json { max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
</style>
