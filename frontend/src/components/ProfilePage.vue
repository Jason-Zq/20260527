<template>
  <div class="profile-page">
    <div class="profile-header">
      <div class="profile-title">
        <span class="title-indicator"></span>
        客户画像
      </div>
      <div class="header-actions">
        <el-upload
          :show-file-list="false"
          accept=".xlsx"
          :http-request="handleUpload"
          :disabled="uploading"
          style="display: inline-block; margin-right: 12px"
        >
          <el-button type="primary" :loading="uploading">
            <el-icon style="margin-right: 4px"><Upload /></el-icon>
            导入客户文件清单(.xlsx)
          </el-button>
        </el-upload>
        <el-button @click="loadTasks" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="profile-main">
      <section class="card">
        <div class="card-title">导入任务</div>
        <el-table :data="tasks" v-loading="loading" stripe empty-text="暂无任务,点击右上角导入 Excel 开始" style="width: 100%">
          <el-table-column label="ID" width="70" align="center" prop="id" />
          <el-table-column label="客户" min-width="100" show-overflow-tooltip prop="client_name" />
          <el-table-column label="清单文件" min-width="180" show-overflow-tooltip prop="filename" />
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="taskTag(row.status)" size="small">{{ taskLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="110" align="center">
            <template #default="{ row }">{{ row.processed_files }}/{{ row.total_files }}</template>
          </el-table-column>
          <el-table-column label="当前文件" min-width="150" show-overflow-tooltip>
            <template #default="{ row }"><span class="dim">{{ row.current_file || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="四类证件" min-width="170">
            <template #default="{ row }">
              <span class="type-counts">
                身份证 {{ row.id_card_count }} · 户口 {{ row.hukou_count }} · 学位 {{ row.degree_cert_count }} · 出生 {{ row.birth_cert_count }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="提取" width="70" align="center" prop="extracted_count" />
          <el-table-column label="待复核" width="80" align="center">
            <template #default="{ row }">
              <span v-if="row.needs_review_count > 0" class="err-text" style="font-weight: 600">{{ row.needs_review_count }}</span>
              <span v-else class="dim">0</span>
            </template>
          </el-table-column>
          <el-table-column label="失败" width="70" align="center">
            <template #default="{ row }">
              <span :class="{ 'err-text': row.failed_count > 0 }">{{ row.failed_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="150">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openProfile(row)">查看画像</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </div>

    <!-- 客户画像详情 -->
    <el-dialog v-model="profileVisible" :title="`客户画像 · ${profile?.task?.client_name || ''}`" width="88%" top="4vh">
      <div v-if="profileLoading" style="padding: 40px; text-align: center"><el-icon class="is-loading"><Loading /></el-icon> 加载中...</div>
      <div v-else-if="profile" class="profile-body">
        <div class="task-summary">
          <el-tag :type="taskTag(profile.task.status)" size="small">{{ taskLabel(profile.task.status) }}</el-tag>
          <span class="dim">进度 {{ profile.task.processed_files }}/{{ profile.task.total_files }}</span>
          <span class="dim">新 OCR {{ profile.task.fresh_ocr_count }}</span>
          <span class="dim">复用 {{ profile.task.reused_count + profile.task.relinked_count }}</span>
          <span class="dim" :class="{ 'err-text': profile.task.failed_count > 0 }">失败 {{ profile.task.failed_count }}</span>
          <span class="type-counts">
            身份证 {{ profile.type_counts.id_card }} · 户口 {{ profile.type_counts.hukou }} · 学位 {{ profile.type_counts.degree_cert }} · 出生 {{ profile.type_counts.birth_cert }}
          </span>
        </div>

        <el-tabs v-model="activeTab">
          <el-tab-pane label="客户档案" name="profile">
            <div v-if="profile.task.needs_review_count > 0" class="review-banner" @click="openReviewCenter">
              <el-icon><WarningFilled /></el-icon>
              <span>{{ profile.task.needs_review_count }} 个文件待复核(含识别失败/质量差/无法归属),点击前往复核中心</span>
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div v-if="passportAlerts.length" class="expiry-banner" :class="{ expired: passportAlerts.some(a => a.level === 'expired') }">
              <el-icon><WarningFilled /></el-icon>
              <span>护照到期提醒:{{ passportAlerts.map(a => a.text).join(';') }}</span>
            </div>
            <div v-if="conflictAlerts.length" class="conflict-banner">
              <el-icon><WarningFilled /></el-icon>
              <span>字段冲突提醒(多来源不一致,只提示不改值):{{ conflictAlerts.join(';') }},详见成员卡字段「冲突」标</span>
            </div>
            <div class="person-grid">
              <section v-for="p in profile.persons" :key="p.id" class="card inner-card person-card">
                <div class="person-head">
                  <div class="person-avatar" :class="{ main: p.is_main }">{{ (p.name || '?')[0] }}</div>
                  <div class="person-title">
                    <span class="person-name">{{ p.name }}</span>
                    <el-tag v-if="p.is_main" type="primary" size="small" effect="dark">户主</el-tag>
                    <el-tag v-else-if="p.relation_to_main === '待确认'" type="warning" size="small">待确认</el-tag>
                    <el-tag v-else size="small" effect="plain">{{ p.relation_to_main }}</el-tag>
                  </div>
                </div>
                <div class="person-fields">
                  <div v-for="f in p.fields" :key="f.field" class="field-row">
                    <span class="field-label">{{ f.label || f.field }}</span>
                    <span class="field-value">
                      {{ f.value || '-' }}
                      <el-tooltip v-if="f.status === 'corrected'" content="人工已修正" placement="top">
                        <span class="status-mark corrected">已修正</span>
                      </el-tooltip>
                      <span v-else-if="f.status === 'confirmed'" class="status-mark confirmed">已确认</span>
                      <span v-if="f.layer === 'declared'" class="layer-tag">自报</span>
                      <span v-if="f.field === 'passport_expiry_date' && p.passport_expiry && p.passport_expiry.level !== 'ok'"
                            class="expiry-tag" :class="p.passport_expiry.level">{{ expiryTagText(p.passport_expiry) }}</span>
                      <el-tooltip v-if="p.field_conflicts && p.field_conflicts[f.field]" placement="top">
                        <template #content>
                          <div v-for="(v, i) in p.field_conflicts[f.field].values" :key="i" style="max-width: 360px">
                            {{ v.value }} ← {{ v.sources.map(s => s.source).join('、') }}
                          </div>
                        </template>
                        <span class="conflict-tag">冲突</span>
                      </el-tooltip>
                    </span>
                  </div>
                  <div v-if="!p.fields.length" class="dim" style="padding: 8px 0">暂无提取字段</div>
                </div>
              </section>
              <el-empty v-if="!profile.persons?.length" description="任务完成后自动生成家庭成员档案" :image-size="60" style="grid-column: 1 / -1" />
            </div>

            <section v-if="profile.assets?.length" class="card inner-card">
              <div class="card-title">家庭资产({{ profile.assets.length }})</div>
              <section v-for="a in profile.assets" :key="a.id" class="card inner-card person-card asset-card">
                <div class="person-head">
                  <div class="person-avatar asset">产</div>
                  <div class="person-title">
                    <span class="person-name">{{ a.name }}</span>
                    <el-tag size="small" effect="plain">{{ a.asset_type }}</el-tag>
                    <el-tag v-if="a.status === 'corrected'" type="primary" size="small" effect="dark">已修正</el-tag>
                  </div>
                </div>
                <div class="person-fields">
                  <div v-if="a.owner_person_id" class="field-row">
                    <span class="field-label">权属人</span>
                    <span class="field-value">{{ personName(a.owner_person_id) }}</span>
                  </div>
                  <div v-for="(v, k) in a.attrs" :key="k" class="field-row">
                    <span class="field-label">{{ assetAttrLabel(k) }}</span>
                    <span class="field-value">{{ v }}</span>
                  </div>
                </div>
              </section>
            </section>

            <section class="card inner-card">
              <div class="card-title">提取明细({{ profile.extraction_total }})</div>
              <el-table :data="profile.extractions" stripe size="small" empty-text="暂无提取记录">
                <el-table-column label="文件编码" min-width="100" show-overflow-tooltip prop="file_id" />
                <el-table-column label="类型" width="90" align="center">
                  <template #default="{ row }">{{ docTypeLabel(row.doc_type) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="110" align="center">
                  <template #default="{ row }">
                    <el-tag :type="extractTag(row.status)" size="small">{{ extractLabel(row) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="规则版本" width="90" align="center">
                  <template #default="{ row }">{{ row.rule_version != null ? 'v' + row.rule_version : '-' }}</template>
                </el-table-column>
                <el-table-column label="写入统计" min-width="180">
                  <template #default="{ row }">{{ writeStatsText(row.write_stats) }}</template>
                </el-table-column>
                <el-table-column label="时间" min-width="150">
                  <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
                </el-table-column>
                <el-table-column label="操作" width="90" align="center">
                  <template #default="{ row }">
                    <el-button size="small" type="primary" link @click="openExtractDetail(row)">详情</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </section>
          </el-tab-pane>

          <el-tab-pane label="完备度矩阵" name="matrix">
            <el-table :data="matrix.persons" border size="small" v-loading="matrixLoading" empty-text="任务完成后生成">
              <el-table-column label="成员" width="130">
                <template #default="{ row }">
                  <b>{{ row.name }}</b>
                  <span class="dim" style="margin-left: 4px">{{ row.relation_to_main }}</span>
                </template>
              </el-table-column>
              <el-table-column v-for="col in matrix.columns" :key="col.key" :label="col.label" align="center" min-width="95">
                <template #default="{ row }">
                  <span class="matrix-cell" :class="cellOf(row, col).status" @click="onCellClick(row, col)">
                    {{ cellText(cellOf(row, col).status) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
            <div class="matrix-legend dim">
              <span class="matrix-cell ok">✓</span> 材料齐 &nbsp;
              <span class="matrix-cell warn">!</span> 有文件但待复核(点击直达) &nbsp;
              <span class="matrix-cell missing">✗</span> 缺失 &nbsp;
              <span class="matrix-cell na">—</span> 不适用
            </div>
          </el-tab-pane>

          <el-tab-pane label="案件时间线" name="cases">
            <div v-if="profile.cases?.length" class="case-list">
              <section v-for="c in profile.cases" :key="c.id" class="card inner-card">
                <div class="card-title case-head">
                  <span>{{ c.case_type }}</span>
                  <el-tag :type="caseStatusTag(c.status)" size="small" effect="dark">{{ c.status }}</el-tag>
                </div>
                <el-timeline class="case-timeline">
                  <el-timeline-item
                    v-for="(m, i) in c.milestones" :key="m.name"
                    :timestamp="m.date" placement="top"
                    :type="i === c.milestones.length - 1 ? 'primary' : ''"
                    :hollow="i !== c.milestones.length - 1"
                  >
                    <div class="milestone-row">
                      <b>{{ m.name }}</b>
                      <span v-if="m.source_filename" class="dim milestone-src">来源:{{ m.source_filename }}</span>
                    </div>
                  </el-timeline-item>
                </el-timeline>
              </section>
            </div>
            <el-empty v-else description="暂无案件里程碑,递交/批复/签收类文件提取后自动生成" :image-size="60" />
          </el-tab-pane>

          <el-tab-pane :label="`文件清单(${filesTotal})`" name="files">
            <el-table :data="files" v-loading="filesLoading" stripe size="small" empty-text="暂无文件">
              <el-table-column label="文件编码" min-width="95" show-overflow-tooltip prop="file_code" />
              <el-table-column label="文件名" min-width="170" show-overflow-tooltip prop="filename" />
              <el-table-column label="文件夹" min-width="110" show-overflow-tooltip prop="folder_name" />
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag :type="fileTag(row.status)" size="small">{{ fileLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="OCR 来源" width="100" align="center">
                <template #default="{ row }">{{ ocrSourceLabel(row.ocr_source) }}</template>
              </el-table-column>
              <el-table-column label="识别类型" width="110" align="center">
                <template #default="{ row }">
                  <span v-if="row.doc_type">{{ docTypeLabel(row.doc_type) }}</span>
                  <span v-else class="dim">-</span>
                </template>
              </el-table-column>
              <el-table-column label="分类依据" width="110" align="center">
                <template #default="{ row }">
                  <span v-if="row.classify_by !== 'none'">{{ classifyLabel(row.classify_by) }}{{ row.classify_score != null ? ' ' + row.classify_score : '' }}</span>
                  <span v-else class="dim">-</span>
                </template>
              </el-table-column>
              <el-table-column label="复核" width="110" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.review_status === 'needs_review'" type="danger" size="small" class="review-link" @click="openReviewCenter(row)">
                    {{ reviewReasonLabel(row.review_reason) }}
                  </el-tag>
                  <span v-else-if="row.review_status === 'reviewed'" class="dim">已复核</span>
                  <span v-else class="dim">-</span>
                </template>
              </el-table-column>
              <el-table-column label="错误" min-width="150" show-overflow-tooltip>
                <template #default="{ row }"><span class="err-text">{{ row.error_msg || '' }}</span></template>
              </el-table-column>
              <el-table-column label="操作" width="170" align="center" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" link :disabled="!(row.char_count > 0)" @click="openOcr(row)">查看 OCR</el-button>
                  <el-button size="small" type="primary" link @click="openRaw(row)">原件</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="pagination-row">
              <el-pagination
                v-model:current-page="filesPage"
                v-model:page-size="filesPageSize"
                :page-sizes="[10, 20, 50, 100]"
                :total="filesTotal"
                layout="total, sizes, prev, pager, next"
                @current-change="onFilesPageChange"
                @size-change="onFilesSizeChange"
              />
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>

    <!-- 复核中心抽屉(共享组件;复核中心页用同一组件,不传 importTaskId 即全局队列) -->
    <ReviewDrawer ref="reviewDrawerRef" :import-task-id="profile?.task?.id || null" @done="onReviewDone" />

    <!-- 原件查看弹窗(图片) -->
    <el-dialog v-model="rawVisible" title="原件" width="70%" top="4vh" append-to-body>
      <div style="text-align: center">
        <img v-if="rawState.url" :src="rawState.url" style="max-width: 100%; max-height: 76vh" alt="原件" />
      </div>
    </el-dialog>

    <!-- OCR 全文弹窗(原件 | 文本 对照) -->
    <el-dialog v-model="ocrVisible" title="OCR 对照" width="85%" top="4vh" append-to-body>
      <div v-if="ocrLoading" style="padding: 40px; text-align: center"><el-icon class="is-loading"><Loading /></el-icon> 加载中...</div>
      <div v-else-if="ocrFile" class="ocr-body">
        <div class="detail-meta">
          <span><b>文件:</b> {{ ocrFile.filename || '-' }}</span>
          <span><b>编码:</b> {{ ocrFile.file_code }}</span>
          <span><b>类型:</b> {{ docTypeLabel(ocrFile.doc_type) }}</span>
          <span><b>字符数:</b> {{ ocrFile.char_count ?? '-' }}</span>
          <el-tag v-if="ocrFile.ocr_source === 'reused'" type="warning" size="small">复用留底检测的脱敏文本,证件号等已打码</el-tag>
          <el-tag v-else-if="ocrFile.ocr_source === 'fresh'" type="success" size="small">新识别原文</el-tag>
        </div>
        <div class="ocr-panes">
          <div class="pane">
            <div class="pane-title">原件</div>
            <div class="pane-body raw-view">
              <img v-if="rawState.url && rawState.isImage" :src="rawState.url" class="raw-img" alt="原件" />
              <el-button v-else-if="rawState.url" type="primary" size="small" @click="openRawInTab">在新窗口打开 PDF/文件</el-button>
              <span v-else class="dim">{{ rawState.hint }}</span>
            </div>
          </div>
          <div class="pane">
            <div class="pane-title">OCR 文本</div>
            <pre class="pane-body ocr-view">{{ ocrFile.ocr_text || '(无文本)' }}</pre>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- 提取结果 JSON 详情 -->
    <el-dialog v-model="extractVisible" title="提取结果详情" width="70%" top="6vh" append-to-body>
      <div v-if="extractDetail" class="extract-detail">
        <div class="detail-meta">
          <span><b>文件编码:</b> {{ extractDetail.file_id || '-' }}</span>
          <span><b>类型:</b> {{ docTypeLabel(extractDetail.doc_type) }}</span>
          <span><b>规则:</b> {{ extractDetail.rule_id ? `#${extractDetail.rule_id} v${extractDetail.rule_version}` : '-' }}</span>
          <span><b>耗时:</b> {{ extractDetail.elapsed_ms != null ? (extractDetail.elapsed_ms / 1000).toFixed(1) + 's' : '-' }}</span>
        </div>
        <div class="json-block">
          <div class="json-title">提取字段(extracted)</div>
          <pre>{{ prettyJson(extractDetail.extracted) }}</pre>
        </div>
        <div class="json-block">
          <div class="json-title">写入明细(mapped)</div>
          <pre>{{ prettyJson(extractDetail.mapped) }}</pre>
        </div>
        <div class="json-block">
          <div class="json-title">写入统计(write_stats)</div>
          <pre>{{ prettyJson(extractDetail.write_stats) }}</pre>
        </div>
        <div v-if="extractDetail.error_msg" class="json-block">
          <div class="json-title err-text">错误</div>
          <pre class="err-text">{{ extractDetail.error_msg }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, Loading, Refresh, Upload, WarningFilled } from '@element-plus/icons-vue'
import ReviewDrawer from './ReviewDrawer.vue'
import { docTypeLabel, fieldLabelOf, reviewReasonLabel } from '../utils/labels'
import {
  fetchCustomerFileRawUrl,
  getCustomerFile,
  getDocExtractResult,
  getProfileTaskMatrix,
  getProfileTaskProfile,
  importProfileExcel,
  listProfileTaskFiles,
  listProfileTasks,
} from '../api'

const tasks = ref([])
const loading = ref(false)
const uploading = ref(false)

const profileVisible = ref(false)
const profileLoading = ref(false)
const profile = ref(null)
const activeTab = ref('profile')
const files = ref([])
const filesTotal = ref(0)
const filesLoading = ref(false)
const filesPage = ref(1)
const filesPageSize = ref(10)

const extractVisible = ref(false)
const extractDetail = ref(null)

const ocrVisible = ref(false)
const ocrLoading = ref(false)
const ocrFile = ref(null)

const rawVisible = ref(false)
const rawState = ref({ url: '', isImage: false, hint: '原件加载中…', _revoke: null })

const reviewDrawerRef = ref(null)

const matrix = ref({ persons: [], columns: [], cells: {} })
const matrixLoading = ref(false)

let pollTimer = null

async function loadTasks() {
  loading.value = true
  try {
    const data = await listProfileTasks({ limit: 50 })
    tasks.value = data.items
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    loading.value = false
  }
}

async function handleUpload({ file }) {
  uploading.value = true
  try {
    const r = await importProfileExcel(file)
    ElMessage.success(`已创建导入任务 #${r.task_id}: ${r.client_name} 共 ${r.total_files} 个文件`)
    await loadTasks()
    openProfile({ id: r.task_id })
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    uploading.value = false
  }
}

async function openProfile(row) {
  profileVisible.value = true
  activeTab.value = 'profile'
  filesPage.value = 1
  await reloadProfile(row.id)
}

async function reloadProfile(taskId) {
  profileLoading.value = true
  try {
    profile.value = await getProfileTaskProfile(taskId)
    await loadFiles(taskId)
    loadMatrix(taskId)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    profileLoading.value = false
  }
}

async function loadMatrix(taskId) {
  matrixLoading.value = true
  try {
    matrix.value = await getProfileTaskMatrix(taskId)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    matrixLoading.value = false
  }
}

function cellOf(person, col) {
  return (matrix.value.cells?.[person.id] || {})[col.key] || { status: 'missing', files: [] }
}

function cellText(status) {
  return { ok: '✓', warn: '!', missing: '✗', na: '—' }[status] || '?'
}

// 护照到期提醒:后端 attach_passport_expiry 已算好 level/days_left,这里只做文案
const passportAlerts = computed(() => {
  const out = []
  for (const p of profile.value?.persons || []) {
    const e = p.passport_expiry
    if (!e || e.level === 'ok') continue
    out.push({
      level: e.level,
      text: e.level === 'expired'
        ? `${p.name}护照已过期 ${-e.days_left} 天(${e.date})`
        : `${p.name} ${e.date} 到期(剩 ${e.days_left} 天)`,
    })
  }
  return out
})

function expiryTagText(e) {
  return e.level === 'expired' ? `已过期${-e.days_left}天` : `剩${e.days_left}天`
}

// 字段冲突提醒:后端 attach_field_conflicts 已聚合好,这里只做汇总文案
const conflictAlerts = computed(() => {
  const out = []
  for (const p of profile.value?.persons || []) {
    const labels = Object.values(p.field_conflicts || {}).map(c => c.label)
    if (labels.length) out.push(`${p.name} ${labels.join('/')}`)
  }
  return out
})

async function onCellClick(person, col) {
  const cell = cellOf(person, col)
  if (cell.status === 'warn') {
    const target = cell.files.find((f) => f.review_status === 'needs_review') || cell.files[0]
    if (target) await openReviewCenter({ id: target.id })
  } else if (cell.status === 'ok' && cell.files.length) {
    await openOcr({ id: cell.files[0].id })
  }
}

async function loadFiles(taskId) {
  filesLoading.value = true
  try {
    const f = await listProfileTaskFiles(taskId, {
      limit: filesPageSize.value,
      offset: (filesPage.value - 1) * filesPageSize.value,
    })
    files.value = f.items
    filesTotal.value = f.total
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    filesLoading.value = false
  }
}

function onFilesPageChange() {
  if (profile.value?.task?.id) loadFiles(profile.value.task.id)
}

function onFilesSizeChange() {
  filesPage.value = 1
  onFilesPageChange()
}

async function openExtractDetail(row) {
  try {
    extractDetail.value = await getDocExtractResult(row.id)
    extractVisible.value = true
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  }
}

async function openOcr(row) {
  ocrVisible.value = true
  ocrLoading.value = true
  ocrFile.value = null
  loadRaw(row.id)
  try {
    ocrFile.value = await getCustomerFile(row.id)
  } catch (err) {
    ocrVisible.value = false
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    ocrLoading.value = false
  }
}

// ---- 原件查看(blob 带鉴权;图片内联,PDF 新窗口) ----

async function loadRaw(fileId) {
  if (rawState.value._revoke) rawState.value._revoke()
  rawState.value = { url: '', isImage: false, hint: '原件加载中…', _revoke: null }
  try {
    const raw = await fetchCustomerFileRawUrl(fileId)
    rawState.value = {
      url: raw.blobUrl,
      isImage: (raw.mime || '').startsWith('image/'),
      hint: '',
      _revoke: raw.revoke,
    }
  } catch (err) {
    rawState.value = { url: '', isImage: false, hint: '原件不可用(可能已清理且无法重下)', _revoke: null }
  }
}

async function openRaw(row) {
  try {
    const raw = await fetchCustomerFileRawUrl(row.id)
    if ((raw.mime || '').startsWith('image/')) {
      if (rawState.value._revoke) rawState.value._revoke()
      rawState.value = { url: raw.blobUrl, isImage: true, hint: '', _revoke: raw.revoke }
      rawVisible.value = true
    } else {
      window.open(raw.blobUrl, '_blank')  // blob URL 随页面生命周期释放
    }
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '原件不可用')
  }
}

function openRawInTab() {
  if (rawState.value.url) window.open(rawState.value.url, '_blank')
}

// ---- 复核中心(抽屉已抽为 ReviewDrawer 组件,全局复核中心页复用) ----

function openReviewCenter(targetItem = null) {
  reviewDrawerRef.value?.open(targetItem)
}

function onReviewDone() {
  loadTasks()
  if (profile.value?.task?.id) reloadProfile(profile.value.task.id)
}

function startPolling() {
  pollTimer = setInterval(async () => {
    const hasRunning = tasks.value.some((t) => t.status === 'running')
    if (hasRunning) {
      await loadTasks()
      if (profileVisible.value && profile.value?.task?.status === 'running') {
        await reloadProfile(profile.value.task.id)
      }
    }
  }, 3000)
}

function taskTag(s) {
  return { running: 'warning', done: 'success', error: 'danger' }[s] || 'info'
}
function taskLabel(s) {
  return { running: '进行中', done: '完成', error: '失败' }[s] || s
}
function fileTag(s) {
  return { done: 'success', error: 'danger', fetching: 'warning', ocr: 'warning', pending: 'info' }[s] || 'info'
}
function fileLabel(s) {
  return { pending: '待处理', fetching: '下载中', ocr: 'OCR中', done: '完成', error: '失败' }[s] || s
}
function caseStatusTag(s) {
  return { 已签收: 'success', 已获批: 'primary', 已交付: 'warning', 已递交: 'warning' }[s] || 'info'
}
function ocrSourceLabel(s) {
  return { fresh: '新识别', reused: '复用', none: '-' }[s] || s || '-'
}
function assetAttrLabel(k) {
  return {
    address: '坐落', area: '面积(㎡)', usage: '用途', right_type: '权利类型',
    right_status: '权利状态', register_date: '登记日期', cert_no: '权证号',
    co_ownership: '共有情况', land_term: '土地使用期限', other_rights: '他项权利',
  }[k] || k
}
function personName(pid) {
  const p = (profile.value?.persons || []).find((x) => x.id === pid)
  return p ? p.name : `#${pid}`
}
function classifyLabel(s) {
  return { keyword: '关键词', llm: 'AI' }[s] || s
}
function extractTag(s) {
  return { done: 'success', error: 'danger', skipped: 'info' }[s] || 'info'
}
function extractLabel(row) {
  if (row.status === 'skipped') return `跳过(${row.skip_reason || ''})`
  return { done: '完成', error: '失败' }[row.status] || row.status
}
function writeStatsText(ws) {
  if (!ws) return '-'
  const parts = []
  if (ws.client_fields) parts.push(`客户+${ws.client_fields}`)
  if (ws.member_fields) parts.push(`成员+${ws.member_fields}`)
  if (ws.member_created) parts.push('新建成员')
  if (ws.asset_created) parts.push('新建资产')
  if (ws.asset_updated) parts.push('更新资产')
  if (ws.milestone_created) parts.push(`里程碑+${ws.milestone_created}`)
  if (ws.milestone_updated) parts.push('更新里程碑')
  if (ws.matched_by) parts.push(`匹配:${{ id_number: '证件号', name: '姓名', name_en: '拼音名' }[ws.matched_by] || ws.matched_by}`)
  return parts.join(' · ') || '无写入'
}
function prettyJson(v) {
  if (v == null) return '-'
  return JSON.stringify(v, null, 2)
}

onMounted(() => {
  loadTasks()
  startPolling()
})
onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.profile-page {
  padding: 16px 20px;
}
.profile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.profile-title {
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
}
.title-indicator {
  display: inline-block;
  width: 4px;
  height: 18px;
  background: #409eff;
  border-radius: 2px;
  margin-right: 8px;
}
.card {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 14px 16px;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}
.inner-card {
  margin-bottom: 14px;
}
.dim {
  color: #909399;
}
.mono {
  font-family: Consolas, monospace;
}
.err-text {
  color: #f56c6c;
}
.type-counts {
  font-size: 12px;
  color: #606266;
}
.task-summary {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.profile-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
@media (max-width: 1200px) {
  .profile-columns {
    grid-template-columns: 1fr;
  }
}
.detail-meta {
  display: flex;
  gap: 18px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

/* ---- 复核横幅 ---- */
.review-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: #fef0f0;
  border: 1px solid #fde2e2;
  border-radius: 8px;
  color: #f56c6c;
  font-size: 13px;
  cursor: pointer;
}
.review-banner:hover {
  background: #fde2e2;
}

/* ---- 护照到期提醒 ---- */
.expiry-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  color: #e6a23c;
  font-size: 13px;
}
.expiry-banner.expired {
  background: #fef0f0;
  border-color: #fde2e2;
  color: #f56c6c;
}
.expiry-tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
}
.expiry-tag.expiring {
  background: #fdf6ec;
  color: #e6a23c;
}
.expiry-tag.expired {
  background: #fef0f0;
  color: #f56c6c;
}

/* ---- 字段冲突提醒 ---- */
.conflict-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  color: #b88230;
  font-size: 13px;
}
.conflict-tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  background: #fdf6ec;
  color: #b88230;
  border: 1px solid #faecd8;
  cursor: help;
}

/* ---- 成员卡 ---- */
.person-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 14px;
}
@media (max-width: 1200px) {
  .person-grid {
    grid-template-columns: 1fr;
  }
}
.person-card {
  margin-bottom: 0 !important;
}
.person-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid #ebeef5;
}
.person-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #e4e7ed;
  color: #606266;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 600;
}
.person-avatar.main {
  background: #409eff;
  color: #fff;
}
.person-name {
  font-size: 15px;
  font-weight: 600;
  margin-right: 8px;
}
.field-row {
  display: flex;
  padding: 3px 0;
  font-size: 13px;
}
.field-label {
  width: 88px;
  flex-shrink: 0;
  color: #909399;
}
.field-value {
  color: #303133;
  word-break: break-all;
}
.layer-tag {
  margin-left: 6px;
  font-size: 11px;
  color: #909399;
  border: 1px solid #dcdfe6;
  border-radius: 3px;
  padding: 0 4px;
}
.status-mark {
  margin-left: 6px;
  font-size: 11px;
}
.status-mark.corrected {
  color: #409eff;
}
.status-mark.confirmed {
  color: #67c23a;
}
.review-link {
  cursor: pointer;
}

/* ---- OCR 对照弹窗面板 ---- */
.ocr-panes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.pane-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: #606266;
}
.pane-body {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px;
  height: 520px;
  overflow: auto;
}
.raw-view {
  display: flex;
  align-items: flex-start;
  justify-content: center;
}
.raw-img {
  max-width: 100%;
  max-height: 100%;
}
.ocr-view {
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  user-select: text;
  margin: 0;
}

/* ---- 完备度矩阵 ---- */
.matrix-cell {
  display: inline-block;
  width: 26px;
  height: 26px;
  line-height: 26px;
  border-radius: 50%;
  font-weight: 700;
  cursor: pointer;
}
.matrix-cell.ok {
  background: #f0f9eb;
  color: #67c23a;
  border: 1px solid #c2e7b0;
}
.matrix-cell.warn {
  background: #fdf6ec;
  color: #e6a23c;
  border: 1px solid #f5dab1;
}
.matrix-cell.missing {
  background: #fef0f0;
  color: #f56c6c;
  border: 1px solid #fde2e2;
  cursor: default;
}
.matrix-cell.na {
  background: #f4f4f5;
  color: #c0c4cc;
  border: 1px solid #e9e9eb;
  cursor: default;
}
.matrix-legend {
  margin-top: 12px;
  font-size: 12px;
}
.matrix-legend .matrix-cell {
  width: 18px;
  height: 18px;
  line-height: 18px;
  font-size: 11px;
  cursor: default;
}
.case-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.case-timeline {
  margin-top: 12px;
  padding-left: 4px;
}
.milestone-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.milestone-src {
  font-size: 12px;
}
.json-block {
  margin-bottom: 12px;
}
.json-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
}
.json-block pre {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.ocr-text {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px;
  max-height: 60vh;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  user-select: text;
}
</style>
