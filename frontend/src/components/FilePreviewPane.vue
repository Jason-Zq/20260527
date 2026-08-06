<template>
  <!-- 内嵌原件预览面板:file=null 显示占位;office 走 preview-pdf 转 PDF,其余按 mime 选 img/iframe -->
  <div class="preview-pane" v-loading="loading">
    <img v-if="rawState.url && rawState.isImage" :src="rawState.url" class="raw-img" alt="原件" />
    <iframe v-else-if="rawState.url && rawState.isPdf" :src="rawState.url" class="raw-iframe" title="原件"></iframe>
    <span v-else-if="rawState.url" class="dim">该文件类型不支持在线预览</span>
    <span v-else-if="!loading" class="dim">{{ rawState.hint }}</span>
  </div>
</template>

<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { fetchCustomerFileRawUrl, fetchCustomerFilePreviewPdfUrl } from '../api'

const OFFICE_EXTS = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']

function isOfficeFile(filename) {
  const lower = (filename || '').toLowerCase()
  return OFFICE_EXTS.some((e) => lower.endsWith(e))
}

const props = defineProps({
  file: { type: Object, default: null },
  // 取数函数可注入:默认客户画像原件接口;文件留底检测等场景传自己的 wrapper
  fetchRaw: { type: Function, default: fetchCustomerFileRawUrl },
  fetchPreviewPdf: { type: Function, default: fetchCustomerFilePreviewPdfUrl },
})

const PLACEHOLDER_HINT = '点击底部「展示文件」加载原件预览'

const loading = ref(false)
const rawState = ref({ url: '', isImage: false, isPdf: false, hint: PLACEHOLDER_HINT, _revoke: null })

// 按对象引用监听:父组件每次点击都传新对象,id 相同也重新加载(失败可重试);file 变 null 则复位
watch(() => props.file, (f) => {
  resetState()
  if (f?.id) loadRaw(f.id)
})

async function loadRaw(fileId) {
  loading.value = true
  // Office 原件:优先走 soffice 转 PDF 预览;失败回落「不支持在线预览」
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

function resetState() {
  if (rawState.value._revoke) rawState.value._revoke()
  rawState.value = { url: '', isImage: false, isPdf: false, hint: PLACEHOLDER_HINT, _revoke: null }
}

onBeforeUnmount(() => {
  if (rawState.value._revoke) rawState.value._revoke()
})
</script>

<style scoped>
.preview-pane {
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
