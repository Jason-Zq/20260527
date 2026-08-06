<template>
  <el-dialog :model-value="modelValue" title="批次详情" width="82%" top="5vh" @update:model-value="$emit('update:modelValue', $event)">
    <div v-if="batch" v-loading="detailLoading" class="batch-dialog-body">
      <div class="dialog-toolbar">
        <span class="dim mono">{{ detail?.batch_id || batch.batch_id }}</span>
        <el-button size="small" @click="loadBatchDetail(batch.batch_id)">刷新详情</el-button>
      </div>

      <div class="summary-row">
        <div class="summary-item"><b>批次ID</b><span class="mono">{{ detail?.batch_id || batch.batch_id }}</span></div>
        <div class="summary-item"><b>状态</b><span>{{ batchStatusLabel(detail?.status || batch.status) }}</span></div>
        <div class="summary-item"><b>来源</b><span>{{ sourceKindLabel(detail?.source_kind || batch.source_kind) }}</span></div>
        <div class="summary-item"><b>进度</b><span>{{ detail?.done_files ?? batch.done_files }}/{{ detail?.total_files ?? batch.total_files }}<span v-if="detail" class="dim">（复用 {{ detail.reused_count ?? 0 }} / 新检 {{ detail.new_count ?? 0 }}）</span></span></div>
        <div class="summary-item"><b>客户</b><span>{{ (detail?.client || batch.client)?.name || '-' }}</span></div>
        <div class="summary-item"><b>客户编码</b><span class="mono">{{ (detail?.client || batch.client)?.client_code || '-' }}</span></div>
        <div class="summary-item"><b>办理人</b><span>{{ curProgress?.handler || '-' }}</span></div>
        <div class="summary-item"><b>项目</b><span>{{ curProgress?.project_name || '-' }}</span></div>
        <div class="summary-item"><b>项目详情</b><span>{{ curProgress?.project_detail_name || '-' }}</span></div>
        <div class="summary-item"><b>进展名称</b><span>{{ curProgress?.progress_name || '-' }}</span></div>
        <div class="summary-item"><b>进展OID</b><span class="mono">{{ curProgress?.progress_oid || '-' }}</span></div>
        <div class="summary-item"><b>创建时间</b><span class="mono">{{ detail?.created_at || batch.created_at || '-' }}</span></div>
        <div class="summary-item"><b>识别完成时间</b><span class="mono">{{ (detail?.status || batch.status) === 'done' ? (detail?.updated_at || batch.updated_at || '-') : '-' }}</span></div>
      </div>

      <div class="criteria-box" v-if="detail?.criteria || detail?.user_prompt">
        <b>判定标准</b>
        <p>{{ detail.criteria || detail.user_prompt }}</p>
      </div>

      <div v-if="detail?.overall_reason" class="overall-box">
        <div class="overall-title">{{ verdictLabel(detail.overall_verdict) }} · {{ detail.overall_score }}/100</div>
        <p>{{ detail.overall_reason }}</p>
      </div>

      <div v-if="detail?.overall_verdict2" class="overall-box overall-box-2">
        <div class="overall-title">总体判定2 · {{ verdictLabel(detail.overall_verdict2) }} · {{ detail.overall_score2 }}/100</div>
        <p v-if="detail.overall_reason2">{{ detail.overall_reason2 }}</p>
      </div>

      <el-table :data="detail?.files || []" stripe empty-text="暂无文件" max-height="420">
        <el-table-column label="文件" min-width="220">
          <template #default="{ row }">
            <div class="file-name" :title="row.filename || row.source_url || ''">
              {{ row.filename || row.source_url || '-' }}
            </div>
            <el-tooltip
              v-if="row.status === 'error'"
              :content="row.error_msg || '未记录详细原因'"
              placement="top"
            >
              <div class="file-error-msg">失败原因：{{ row.error_msg || '未记录详细原因' }}</div>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="文件编码" width="130" align="center">
          <template #default="{ row }">
            <span class="mono">{{ row.file_id || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }"><el-tag :type="batchStatusTag(row.status)" size="small">{{ batchStatusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="分类" width="140" show-overflow-tooltip prop="doc_category" />
        <el-table-column label="判断" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.verdict" :type="verdictTag(row.verdict)" size="small">{{ verdictLabel(row.verdict) }}</el-tag>
            <span v-else class="dim">-</span>
          </template>
        </el-table-column>
        <el-table-column label="符合度" width="70" align="center" prop="match_score" />
        <el-table-column label="复用" width="70" align="center">
          <template #default="{ row }"><el-tag v-if="row.is_reused" size="small" type="info">复用</el-tag><span v-else>-</span></template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" link :disabled="!row.id" @click.stop="openFileDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-dialog>

  <!-- 文件详情:上下拉满 100%、宽 70% 居中;底部 OCR 文本(左)与文件预览(右)等高分栏,footer 挂「展示文件」 -->
  <el-dialog v-model="fileDialogVisible" title="文件详情" width="70%" top="0" append-to-body class="file-detail-dialog">
    <div v-loading="fileLoading" class="file-detail-body">
      <template v-if="fileDetail">
        <div class="detail-meta">
          <div><b>文件名：</b>{{ fileDetail.filename || '-' }}</div>
          <div><b>分类：</b>{{ fileDetail.doc_category || '-' }}</div>
          <div><b>判断：</b>{{ verdictLabel(fileDetail.verdict) }} {{ fileDetail.match_score ?? '' }}</div>
        </div>
        <template v-if="fileDetail.status === 'error'">
          <el-divider content-position="left">失败原因</el-divider>
          <p class="reason-text error-text">{{ fileDetail.error_msg || '未知错误' }}</p>
        </template>
        <el-divider content-position="left">判断依据</el-divider>
        <p class="reason-text">{{ fileDetail.reason || '-' }}</p>
        <div class="detail-split">
          <div class="split-col">
            <div class="split-title">OCR 文本（已脱敏）</div>
            <pre class="ocr-text">{{ fileDetail.ocr_text || '无 OCR 文本' }}</pre>
          </div>
          <div class="split-col">
            <div class="split-title">文件预览</div>
            <FilePreviewPane
              class="split-preview"
              :file="previewFile"
              :fetch-raw="fetchArchiveDetectFileRawUrl"
              :fetch-preview-pdf="fetchArchiveDetectFilePreviewPdfUrl"
            />
          </div>
        </div>
      </template>
    </div>
    <template #footer>
      <el-button type="primary" :disabled="!canPreview(fileDetail)" @click="showPreview">展示文件</el-button>
      <el-button @click="fileDialogVisible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
defineOptions({ name: 'BatchDetailDialog' })
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getArchiveAdminFileDetail, pollBusinessBatch, pollArchiveDetect, fetchArchiveDetectFileRawUrl, fetchArchiveDetectFilePreviewPdfUrl } from '../api.js'
import { verdictLabel, verdictTag, batchStatusLabel, batchStatusTag, sourceKindLabel } from '../utils/labels.js'
import FilePreviewPane from './FilePreviewPane.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  batch: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const detail = ref(null)
const detailLoading = ref(false)
// 详情弹窗里的进展信息:优先 detail(接口全量),回退列表行
const curProgress = computed(() => detail.value?.progress || props.batch?.progress || null)

const fileDialogVisible = ref(false)
const fileLoading = ref(false)
const fileDetail = ref(null)

// 文件详情弹窗右栏预览(FilePreviewPane)的数据源:null=未加载,点「展示文件」才填
const previewFile = ref(null)

// 历史 quick 批次无 file_id/source_url,原件不可获取,禁用按钮
function canPreview(row) {
  return !!row?.id && !!(row.file_id || row.source_url)
}

// 每次点击传新对象:同 id 也会触发 pane 重新加载(失败可重试)
function showPreview() {
  previewFile.value = { id: fileDetail.value?.id, filename: fileDetail.value?.filename }
}

watch(
  () => [props.modelValue, props.batch?.batch_id],
  ([visible]) => {
    if (visible && props.batch) {
      detail.value = null
      loadBatchDetail(props.batch.batch_id)
    }
  },
)

async function loadBatchDetail(batchId) {
  detailLoading.value = true
  try {
    // 业务批次优先 business 接口；非业务批次回落通用接口。
    let data
    try {
      data = await pollBusinessBatch(batchId)
    } catch (err) {
      if (err.response?.status !== 404) throw err
      data = await pollArchiveDetect(batchId)
    }
    detail.value = data
  } catch (err) {
    ElMessage.error('加载详情失败：' + (err.response?.data?.detail || err.message))
  } finally {
    detailLoading.value = false
  }
}

async function openFileDetail(row) {
  fileDialogVisible.value = true
  fileLoading.value = true
  fileDetail.value = null
  previewFile.value = null
  try {
    fileDetail.value = await getArchiveAdminFileDetail(row.id)
  } catch (err) {
    ElMessage.error('加载文件详情失败：' + (err.response?.data?.detail || err.message))
  } finally {
    fileLoading.value = false
  }
}
</script>

<style scoped>
.dim { color: #94a3b8; font-weight: 400; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }
.batch-dialog-body { min-height: 260px; }
.dialog-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.summary-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.summary-item { background: #f8fafc; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.summary-item b { color: #64748b; font-size: 12px; }
.summary-item .dim { font-weight: 400; margin-left: 4px; }
.criteria-box { background: #f8fafc; border: 1px solid #e8ebf5; border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; }
.criteria-box b { color: #64748b; font-size: 12px; display: block; margin-bottom: 4px; }
.criteria-box p { margin: 0; color: #475569; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.overall-box { background: #fff7ed; border: 1px solid #fed7aa; border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.overall-title { font-weight: 700; color: #c2410c; margin-bottom: 6px; }
.overall-box p { margin: 0; color: #475569; line-height: 1.7; }
.overall-box-2 { background: #eff6ff; border-color: #bfdbfe; }
.overall-box-2 .overall-title { color: #1d4ed8; }
.detail-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; font-size: 13px; color: #334155; }
.reason-text { line-height: 1.7; color: #334155; background: #f8fafc; padding: 10px 12px; border-radius: 8px; max-height: 140px; overflow: auto; }
.reason-text.error-text { color: #b42318; background: #fef3f2; }
.file-detail-body { height: 100%; display: flex; flex-direction: column; }
.detail-split { flex: 1; min-height: 0; display: flex; gap: 12px; }
.split-col { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.split-title { font-size: 13px; font-weight: 600; color: #64748b; }
.split-preview { flex: 1; min-height: 0; }
.ocr-text { flex: 1; min-height: 0; overflow: auto; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; font-size: 12px; line-height: 1.6; margin: 0; }
.file-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file-error-msg { margin-top: 2px; font-size: 12px; color: #b42318; line-height: 1.4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 1200px) { .summary-row { grid-template-columns: repeat(2, 1fr); } }
</style>

<style>
/* 文件详情上下拉满:el-dialog append-to-body 渲染在 body 下,需非 scoped 才能命中 */
.file-detail-dialog {
  height: 100vh;
  margin-top: 0;
  margin-bottom: 0;
  display: flex;
  flex-direction: column;
}
.file-detail-dialog .el-dialog__body {
  flex: 1;
  min-height: 0;
}
</style>
