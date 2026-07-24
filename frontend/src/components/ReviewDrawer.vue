<template>
  <!-- 复核抽屉:队列 + 三栏(原件 | OCR 文本 | 归属与字段修正)。
       父组件用 ref 调 open(targetItem?) 打开;每次处理完 emit('done') 由父刷新计数/列表。
       importTaskId 不传时为跨任务全局队列(复核中心页)。 -->
  <el-drawer v-model="visible" title="复核中心" size="92%">
    <div class="review-layout">
      <aside class="review-queue">
        <div class="queue-title">待复核({{ reviewQueue.length }})</div>
        <div
          v-for="item in reviewQueue" :key="item.id"
          class="queue-item" :class="{ active: reviewDetail?.file?.id === item.id }"
          @click="openReviewItem(item)"
        >
          <div class="queue-name">{{ item.filename || item.file_code }}</div>
          <div class="queue-meta">
            <el-tag type="danger" size="small">{{ reviewReasonLabel(item.review_reason) }}</el-tag>
            <span class="dim">分 {{ item.quality_score }}</span>
            <span v-if="!importTaskId" class="dim">{{ item.client_name }}</span>
          </div>
        </div>
        <el-empty v-if="!reviewQueue.length" description="没有待复核文件" :image-size="60" />
      </aside>
      <main class="review-main" v-loading="reviewLoading">
        <template v-if="reviewDetail">
          <div class="review-file-head">
            <b>{{ reviewDetail.file.filename || reviewDetail.file.file_code }}</b>
            <span class="dim mono">{{ reviewDetail.file.file_code }}</span>
            <el-tag size="small">{{ docTypeLabel(reviewDetail.file.doc_type) }}</el-tag>
            <el-tag type="danger" size="small">{{ reviewReasonLabel(reviewDetail.file.review_reason) }}</el-tag>
          </div>
          <div class="review-panes">
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
              <pre class="pane-body ocr-view">{{ reviewDetail.file.ocr_text || '(无文本)' }}</pre>
            </div>
            <div class="pane">
              <div class="pane-title">归属与字段修正</div>
              <div class="pane-body edit-view">
                <el-select v-model="reviewForm.person_id" placeholder="选择归属人" size="small" clearable style="width: 100%">
                  <el-option v-for="p in reviewDetail.household_persons" :key="p.id" :label="`${p.name}(${p.is_main ? '户主' : p.relation_to_main})`" :value="p.id" />
                </el-select>
                <el-input v-model="reviewForm.new_person_name" placeholder="或输入新建人姓名" size="small" style="margin-top: 6px" />
                <el-select v-model="reviewForm.person_relation" placeholder="修正关系(可选)" size="small" clearable style="width: 100%; margin-top: 6px">
                  <el-option v-for="r in ['户主', '配偶', '子', '女', '父', '母', '待确认']" :key="r" :label="r" :value="r" />
                </el-select>
                <div class="edit-fields">
                  <div v-for="(v, k) in reviewForm.fields" :key="k" class="edit-field">
                    <span class="edit-label">{{ fieldLabelOf(k) }}</span>
                    <el-input v-model="reviewForm.fields[k]" size="small" :placeholder="k" />
                  </div>
                  <div v-if="!Object.keys(reviewForm.fields).length" class="dim" style="margin-top: 8px">该文件无可修正字段(无提取结果)</div>
                </div>
                <div class="review-actions">
                  <el-button type="success" size="small" :loading="reviewSaving" @click="submitReviewConfirm">确认无误</el-button>
                  <el-button type="primary" size="small" :loading="reviewSaving" @click="submitReviewCorrect">保存修正</el-button>
                  <el-button size="small" :loading="reviewSaving" @click="submitReviewDismiss">忽略</el-button>
                </div>
              </div>
            </div>
          </div>
        </template>
        <el-empty v-else description="从左侧队列选择文件开始复核" />
      </main>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  confirmReviewFile,
  correctReviewFile,
  dismissReviewFile,
  fetchCustomerFileRawUrl,
  getReviewFile,
  listReviewFiles,
} from '../api'
import { docTypeLabel, fieldLabelOf, reviewReasonLabel } from '../utils/labels'

const props = defineProps({
  importTaskId: { type: Number, default: null },  // 不传 = 跨任务全局队列
})
const emit = defineEmits(['done'])

const visible = ref(false)
const reviewQueue = ref([])
const reviewLoading = ref(false)
const reviewSaving = ref(false)
const reviewDetail = ref(null)
const reviewForm = ref({ person_id: null, new_person_name: '', person_relation: null, fields: {} })
const rawState = ref({ url: '', isImage: false, hint: '原件加载中…', _revoke: null })

async function open(targetItem = null) {
  visible.value = true
  reviewLoading.value = true
  reviewDetail.value = null
  try {
    const params = { limit: 100 }
    if (props.importTaskId) params.import_task_id = props.importTaskId
    const data = await listReviewFiles(params)
    reviewQueue.value = data.items
    const first = targetItem || data.items[0]
    if (first) await openReviewItem(first)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    reviewLoading.value = false
  }
}

async function openReviewItem(item) {
  reviewLoading.value = true
  try {
    const detail = await getReviewFile(item.id)
    reviewDetail.value = detail
    const src = detail.result?.corrected || detail.result?.extracted || {}
    reviewForm.value = {
      person_id: (detail.result?.write_stats || {}).person_id || null,
      new_person_name: '',
      person_relation: null,
      fields: Object.fromEntries(
        Object.entries(src).filter(([, v]) => v != null && String(v).trim() !== ''),
      ),
    }
    loadRaw(item.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    reviewLoading.value = false
  }
}

async function _afterReviewAction(msg) {
  ElMessage.success(msg)
  const idx = reviewQueue.value.findIndex((i) => i.id === reviewDetail.value?.file?.id)
  if (idx >= 0) reviewQueue.value.splice(idx, 1)
  reviewDetail.value = null
  if (reviewQueue.value.length) {
    await openReviewItem(reviewQueue.value[Math.min(Math.max(idx, 0), reviewQueue.value.length - 1)])
  } else {
    visible.value = false
  }
  emit('done')
}

async function submitReviewConfirm() {
  reviewSaving.value = true
  try {
    await confirmReviewFile(reviewDetail.value.file.id, {})
    await _afterReviewAction('已确认无误')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    reviewSaving.value = false
  }
}

async function submitReviewCorrect() {
  reviewSaving.value = true
  try {
    const f = reviewForm.value
    const fields = Object.fromEntries(
      Object.entries(f.fields).filter(([, v]) => String(v ?? '').trim() !== ''),
    )
    await correctReviewFile(reviewDetail.value.file.id, {
      person_id: f.person_id || undefined,
      new_person_name: f.new_person_name?.trim() || undefined,
      person_relation: f.person_relation || undefined,
      fields,
    })
    await _afterReviewAction('修正已保存')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    reviewSaving.value = false
  }
}

async function submitReviewDismiss() {
  reviewSaving.value = true
  try {
    await dismissReviewFile(reviewDetail.value.file.id, {})
    await _afterReviewAction('已忽略')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    reviewSaving.value = false
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

function openRawInTab() {
  if (rawState.value.url) window.open(rawState.value.url, '_blank')
}

defineExpose({ open })
</script>

<style scoped>
.review-layout {
  display: flex;
  gap: 14px;
  height: calc(100vh - 130px);
}
.review-queue {
  width: 250px;
  flex-shrink: 0;
  overflow-y: auto;
  border-right: 1px solid #e4e7ed;
  padding-right: 12px;
}
.queue-title {
  font-weight: 600;
  margin-bottom: 10px;
}
.queue-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 6px;
  border: 1px solid #ebeef5;
}
.queue-item:hover {
  background: #f5f7fa;
}
.queue-item.active {
  border-color: #409eff;
  background: #ecf5ff;
}
.queue-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.queue-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.review-main {
  flex: 1;
  overflow-y: auto;
  min-width: 0;
}
.review-file-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.review-panes {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
}
@media (max-width: 1100px) {
  .review-panes {
    grid-template-columns: 1fr;
  }
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
.edit-view .edit-fields {
  margin-top: 10px;
}
.edit-field {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.edit-label {
  width: 80px;
  flex-shrink: 0;
  font-size: 12px;
  color: #909399;
}
.review-actions {
  margin-top: 14px;
  display: flex;
  gap: 8px;
}
.dim {
  color: #909399;
  font-size: 12px;
}
.mono {
  font-family: monospace;
}
</style>
