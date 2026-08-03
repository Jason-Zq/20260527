<template>
  <div class="daily-report-page">
    <div class="admin-header">
      <div class="admin-title">
        <span class="title-indicator"></span>
        每日留底检测报告
      </div>
      <div class="header-actions">
        <el-date-picker
          v-model="date"
          type="date"
          :clearable="false"
          :disabled-date="(d) => d.getTime() > Date.now()"
          value-format="YYYY-MM-DD"
          size="default"
          style="width: 150px"
          @change="onDateChange"
        />
        <el-button size="default" :loading="loading" @click="loadReport">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="admin-main" v-loading="loading">
      <template v-if="report && report.totals.batches > 0">
        <!-- KPI 统计卡:判定/状态卡可点击钻取 -->
        <div class="kpi-grid">
          <div class="kpi-card kpi-total">
            <div class="kpi-num">{{ report.totals.batches }}</div>
            <div class="kpi-label">检测批次</div>
            <div class="kpi-sub">文件 {{ report.totals.files }} · 客户 {{ report.totals.clients }}</div>
          </div>
          <div
            v-for="k in KPI_CARDS"
            :key="k.key"
            class="kpi-card clickable"
            :class="{ active: isDrill(k) }"
            :style="{ '--accent': k.color }"
            @click="openDrill(k)"
          >
            <div class="kpi-num" :style="{ color: k.color }">{{ report.totals[k.key] }}</div>
            <div class="kpi-label">{{ k.label }}</div>
            <div class="kpi-sub">{{ pctOf(report.totals[k.key]) }}</div>
          </div>
        </div>

        <!-- 判定分布条 -->
        <section class="card dist-card">
          <div class="table-head">
            <span>判定分布</span>
            <span class="avg-score" :style="{ color: scoreColor(report.totals.avg_score) }">
              平均分 {{ report.totals.avg_score ?? '-' }}
            </span>
          </div>
          <div class="dist-bar">
            <div
              v-for="seg in distSegments"
              :key="seg.key"
              class="dist-seg"
              :style="{ width: seg.pct + '%', background: seg.color }"
              :title="`${seg.label} ${seg.count}（${seg.pct.toFixed(1)}%）`"
            ></div>
          </div>
          <div class="dist-legend">
            <span v-for="seg in distSegments" :key="seg.key" class="legend-item">
              <i class="legend-dot" :style="{ background: seg.color }"></i>
              {{ seg.label }} {{ seg.count }}（{{ seg.pct.toFixed(0) }}%）
            </span>
          </div>
        </section>

        <!-- 客户预览 -->
        <section class="card">
          <div class="table-head">
            <span>客户预览</span>
            <span class="dim">点击数字查看对应批次</span>
          </div>
          <el-table :data="report.clients" stripe empty-text="暂无客户批次">
            <el-table-column label="客户" min-width="150">
              <template #default="{ row }">
                <div class="client-name">{{ row.name }}</div>
                <div class="dim mono">{{ row.client_code || '-' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="批次" width="70" align="center" prop="batches" sortable />
            <el-table-column label="文件" width="70" align="center" prop="files" />
            <el-table-column v-for="k in KPI_CARDS" :key="k.key" :label="k.label" width="90" align="center">
              <template #default="{ row }">
                <el-button
                  v-if="row[k.key] > 0 && row.client_id != null"
                  type="primary" link size="small"
                  class="num-link" :style="{ color: k.color }"
                  @click="openDrill(k, row)"
                >{{ row[k.key] }}</el-button>
                <span v-else-if="row[k.key] > 0" :style="{ color: k.color }">{{ row[k.key] }}</span>
                <span v-else class="dim">-</span>
              </template>
            </el-table-column>
            <el-table-column label="平均分" width="90" align="center">
              <template #default="{ row }">
                <strong :style="{ color: scoreColor(row.avg_score) }">{{ row.avg_score ?? '-' }}</strong>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" align="center">
              <template #default="{ row }">
                <el-button
                  v-if="row.client_id != null"
                  type="primary" link size="small"
                  @click="openDrill(ALL_CARD, row)"
                >全部批次</el-button>
                <span v-else class="dim">-</span>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <!-- 钻取明细 -->
        <section v-if="drill" class="card drill-card">
          <div class="table-head">
            <span>{{ drillTitle }}</span>
            <el-button size="small" link @click="drill = null">
              <el-icon><Close /></el-icon>
            </el-button>
          </div>
          <el-table :data="drillBatches" v-loading="drillLoading" stripe empty-text="暂无批次">
            <el-table-column label="批次ID" min-width="160" show-overflow-tooltip>
              <template #default="{ row }"><span class="mono">{{ row.batch_id }}</span></template>
            </el-table-column>
            <el-table-column label="客户" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.client?.name || '-' }}</template>
            </el-table-column>
            <el-table-column label="进展" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.progress?.progress_name || row.progress?.progress_oid || '-' }}</template>
            </el-table-column>
            <el-table-column label="项目" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.progress?.project_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="办理人" min-width="90" show-overflow-tooltip align="center">
              <template #default="{ row }">{{ row.progress?.handler || '-' }}</template>
            </el-table-column>
            <el-table-column label="进度" width="80" align="center">
              <template #default="{ row }">{{ row.done_files }}/{{ row.total_files }}</template>
            </el-table-column>
            <el-table-column label="总体" width="110" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.overall_verdict" :type="verdictTag(row.overall_verdict)" size="small">
                  {{ verdictLabel(row.overall_verdict) }} {{ row.overall_score ?? '' }}
                </el-tag>
                <span v-else class="dim">-</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="batchStatusTag(row.status)" size="small">{{ batchStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="160">
              <template #default="{ row }"><span class="dim mono">{{ row.created_at }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="center" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="primary" link @click="selectBatch(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </template>

      <section v-else-if="!loading" class="card empty-card">
        <el-empty :description="`${date} 没有检测批次`" />
      </section>
    </div>

    <BatchDetailDialog v-model="batchDialogVisible" :batch="selectedBatch" />
  </div>
</template>

<script setup>
defineOptions({ name: 'ArchiveDailyReportPage' })
import { ref, computed, onMounted } from 'vue'
import { Refresh, Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getArchiveDailyReport, listArchiveAdminBatches } from '../api.js'
import BatchDetailDialog from './BatchDetailDialog.vue'
import { verdictLabel, verdictTag, batchStatusLabel, batchStatusTag } from '../utils/labels.js'

// KPI/分布共用的分桶定义(顺序即展示顺序)
const KPI_CARDS = [
  { key: 'match', label: '符合', color: '#16a34a', kind: 'verdict', value: 'match' },
  { key: 'partial', label: '部分符合', color: '#d97706', kind: 'verdict', value: 'partial' },
  { key: 'mismatch', label: '不符合', color: '#dc2626', kind: 'verdict', value: 'mismatch' },
  { key: 'in_progress', label: '进行中', color: '#2563eb', kind: 'status', value: 'running' },
  { key: 'error', label: '失败', color: '#64748b', kind: 'status', value: 'error' },
]
const ALL_CARD = { key: 'all', label: '全部', kind: 'all', value: '' }

function todayStr() {
  const d = new Date()
  const z = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
}

const date = ref(todayStr())
const loading = ref(false)
const report = ref(null)

// 钻取状态:{kind: verdict|status|all, value, label, client} (client=null 表示全部客户)
const drill = ref(null)
const drillBatches = ref([])
const drillLoading = ref(false)

const selectedBatch = ref(null)
const batchDialogVisible = ref(false)

async function loadReport() {
  loading.value = true
  try {
    report.value = await getArchiveDailyReport(date.value)
  } catch (err) {
    ElMessage.error('加载报告失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function onDateChange() {
  drill.value = null
  drillBatches.value = []
  loadReport()
}

function pctOf(n) {
  const total = report.value?.totals.batches || 0
  return total ? `${((n / total) * 100).toFixed(0)}%` : '-'
}

const distSegments = computed(() => {
  const t = report.value?.totals
  if (!t || !t.batches) return []
  const segs = [...KPI_CARDS, { key: 'other', label: '未判定', color: '#cbd5e1' }]
  return segs
    .map((s) => ({ ...s, count: t[s.key] || 0, pct: ((t[s.key] || 0) / t.batches) * 100 }))
    .filter((s) => s.count > 0)
})

function scoreColor(s) {
  if (s == null) return '#94a3b8'
  if (s >= 80) return '#16a34a'
  if (s >= 50) return '#d97706'
  return '#dc2626'
}

function isDrill(k) {
  return drill.value && drill.value.kind === k.kind && drill.value.value === k.value && !drill.value.client
}

async function openDrill(k, client = null) {
  // 再点一次已选中的全量 KPI 卡 = 收起
  if (isDrill(k) && !client) {
    drill.value = null
    drillBatches.value = []
    return
  }
  drill.value = { kind: k.kind, value: k.value, label: k.label, client }
  drillLoading.value = true
  drillBatches.value = []
  try {
    const params = { date_from: date.value, date_to: date.value, limit: 100 }
    if (client) params.client_code = client.client_code
    if (k.kind === 'verdict') params.overall_verdict = k.value
    if (k.kind === 'status') params.status = k.value
    const resp = await listArchiveAdminBatches(params)
    drillBatches.value = resp.items || []
  } catch (err) {
    ElMessage.error('加载批次失败：' + (err.response?.data?.detail || err.message))
  } finally {
    drillLoading.value = false
  }
}

const drillTitle = computed(() => {
  if (!drill.value) return ''
  const who = drill.value.client ? `${drill.value.client.name} · ` : ''
  const what = drill.value.kind === 'all' ? '全部' : drill.value.label
  return `${who}${what}批次（${drillBatches.value.length}）`
})

function selectBatch(row) {
  selectedBatch.value = row
  batchDialogVisible.value = true
}

onMounted(loadReport)
</script>

<style scoped>
.daily-report-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.admin-header { height: 56px; flex-shrink: 0; padding: 0 24px; background: #fff; border-bottom: 1px solid #e8ebf5; display: flex; align-items: center; justify-content: space-between; }
.admin-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; color: #1e293b; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #fb923c, #f59e0b); border-radius: 2px; }
.header-actions { display: flex; align-items: center; gap: 10px; }
.admin-main { flex: 1; overflow: auto; padding: 18px 24px 32px; display: flex; flex-direction: column; gap: 14px; }
.card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px; }
.table-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; font-weight: 600; color: #1e293b; }
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }

/* KPI 统计卡 */
.kpi-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
.kpi-card { background: #fff; border: 1px solid #e8ebf5; border-radius: 12px; padding: 16px 18px 14px; display: flex; flex-direction: column; gap: 2px; position: relative; }
.kpi-card.clickable { cursor: pointer; transition: box-shadow 0.15s, transform 0.15s, border-color 0.15s; }
.kpi-card.clickable:hover { box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08); transform: translateY(-1px); }
.kpi-card.clickable.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.kpi-card.clickable::before { content: ''; position: absolute; left: 0; top: 14px; bottom: 14px; width: 3px; border-radius: 2px; background: var(--accent); }
.kpi-num { font-size: 28px; font-weight: 700; color: #1e293b; line-height: 1.2; font-variant-numeric: tabular-nums; }
.kpi-label { font-size: 13px; color: #475569; }
.kpi-sub { font-size: 12px; color: #94a3b8; }
@media (max-width: 1400px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 900px) { .kpi-grid { grid-template-columns: repeat(2, 1fr); } }

/* 判定分布条 */
.dist-card .table-head { margin-bottom: 14px; }
.avg-score { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }
.dist-bar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: #f1f5f9; }
.dist-seg { height: 100%; transition: width 0.3s; }
.dist-seg:first-child { border-radius: 7px 0 0 7px; }
.dist-seg:last-child { border-radius: 0 7px 7px 0; }
.dist-legend { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 10px; font-size: 12px; color: #475569; }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }

/* 客户预览 */
.client-name { font-weight: 600; color: #1e293b; }
.num-link { font-weight: 700; font-size: 14px; font-variant-numeric: tabular-nums; }

/* 钻取明细 */
.drill-card { border-color: #fed7aa; }

.empty-card { padding: 40px 0; }
</style>
