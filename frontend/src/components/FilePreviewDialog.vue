<template>
  <!-- 最外层文件预览弹窗:宽 70%、上下拉满,整窗只显示文件原件(img/iframe)。 -->
  <el-dialog
    v-model="visible" append-to-body width="70%" top="0"
    class="file-preview-dialog" :show-close="true"
    @closed="onClosed"
  >
    <template #header>
      <div class="preview-head">
        <b class="preview-name">{{ file?.filename || file?.file_code || '文件预览' }}</b>
        <el-tag v-if="file?.doc_type" size="small">{{ docTypeLabel(file.doc_type) }}</el-tag>
      </div>
    </template>
    <div class="preview-body" v-loading="loading">
      <img v-if="rawState.url && rawState.isImage" :src="rawState.url" class="raw-img" alt="原件" />
      <iframe v-else-if="rawState.url && rawState.isPdf" :src="rawState.url" class="raw-iframe" title="原件"></iframe>
      <span v-else-if="rawState.url" class="dim">该文件类型不支持在线预览</span>
      <span v-else-if="!loading" class="dim">{{ rawState.hint }}</span>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { fetchCustomerFileRawUrl, fetchCustomerFilePreviewPdfUrl } from '../api'
import { docTypeLabel } from '../utils/labels'

const OFFICE_EXTS = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']

function isOfficeFile(filename) {
  const lower = (filename || '').toLowerCase()
  return OFFICE_EXTS.some((e) => lower.endsWith(e))
}

const props = defineProps({
  visible: { type: Boolean, default: false },
  file: { type: Object, default: null },
  // 取数函数可注入:默认客户画像原件接口;文件留底检测等场景传自己的 wrapper
  fetchRaw: { type: Function, default: fetchCustomerFileRawUrl },
  fetchPreviewPdf: { type: Function, default: fetchCustomerFilePreviewPdfUrl },
})
const emit = defineEmits(['update:visible'])

const visible = computed({ get: () => props.visible, set: (v) => emit('update:visible', v) })
const loading = ref(false)
const rawState = ref({ url: '', isImage: false, isPdf: false, hint: '原件加载中…', _revoke: null })

watch([() => props.visible, () => props.file?.id], ([v, fid]) => {
  if (v && fid) loadRaw(fid)
})

async function loadRaw(fileId) {
  revokeUrl()
  loading.value = true
  rawState.value = { url: '', isImage: false, isPdf: false, hint: '原件加载中…', _revoke: null }
  // Office 原件:优先走 soffice 转 PDF 预览;501/失败回落「不支持在线预览」
  if (isOfficeFile(props.file?.filename)) {
    try {
      const raw = await props.fetchPreviewPdf(fileId)
      rawState.value = { url: raw.blobUrl, isImage: false, isPdf: true, hint: '', _revoke: raw.revoke }
    } catch (err) {
      rawState.value = { url: '', isImage: false, isPdf: false, hint: err?.message || '该文件类型不支持在线预览', _revoke: null }
    } finally {
      loading.value = false
    }
    return
  }
  try {
    const raw = await props.fetchRaw(fileId)
    rawState.value = {
      url: raw.blobUrl,
      isImage: (raw.mime || '').startsWith('image/'),
      isPdf: (raw.mime || '').includes('pdf'),
      hint: '',
      _revoke: raw.revoke,
    }
  } catch (err) {
    rawState.value = { url: '', isImage: false, isPdf: false, hint: err?.message || '原件不可用(可能已清理且无法重下)', _revoke: null }
  } finally {
    loading.value = false
  }
}

function revokeUrl() {
  if (rawState.value._revoke) rawState.value._revoke()
}

function onClosed() {
  revokeUrl()
  rawState.value = { url: '', isImage: false, isPdf: false, hint: '原件加载中…', _revoke: null }
}
</script>

<style scoped>
.preview-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}
.preview-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-body {
  height: calc(100vh - 110px);
  overflow: auto;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}
.raw-img {
  max-width: 100%;
}
.raw-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
.dim {
  color: #909399;
  font-size: 13px;
  padding: 40px 0;
}
</style>

<style>
/* 上下拉满:el-dialog append-to-body 渲染在 body 下,需非 scoped 才能命中 */
.file-preview-dialog {
  height: 100vh;
  margin-top: 0;
  margin-bottom: 0;
  display: flex;
  flex-direction: column;
}
.file-preview-dialog .el-dialog__body {
  flex: 1;
  min-height: 0;
}
</style>
