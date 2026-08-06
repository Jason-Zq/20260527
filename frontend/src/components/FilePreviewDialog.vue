<template>
  <!-- 最外层文件预览弹窗:宽 70%、上下拉满,整窗只显示文件原件(img/iframe)。预览本体在 FilePreviewPane -->
  <el-dialog
    v-model="visible" append-to-body width="70%" top="0"
    class="file-preview-dialog" :show-close="true"
  >
    <template #header>
      <div class="preview-head">
        <b class="preview-name">{{ file?.filename || file?.file_code || '文件预览' }}</b>
        <el-tag v-if="file?.doc_type" size="small">{{ docTypeLabel(file.doc_type) }}</el-tag>
      </div>
    </template>
    <FilePreviewPane
      class="preview-pane-fill"
      :file="visible ? file : null"
      :fetch-raw="fetchRaw"
      :fetch-preview-pdf="fetchPreviewPdf"
    />
  </el-dialog>
</template>

<script setup>
import { computed } from 'vue'
import { fetchCustomerFileRawUrl, fetchCustomerFilePreviewPdfUrl } from '../api'
import { docTypeLabel } from '../utils/labels'
import FilePreviewPane from './FilePreviewPane.vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  file: { type: Object, default: null },
  // 取数函数可注入:默认客户画像原件接口;文件留底检测等场景传自己的 wrapper
  fetchRaw: { type: Function, default: fetchCustomerFileRawUrl },
  fetchPreviewPdf: { type: Function, default: fetchCustomerFilePreviewPdfUrl },
})
const emit = defineEmits(['update:visible'])

const visible = computed({ get: () => props.visible, set: (v) => emit('update:visible', v) })
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
.preview-pane-fill {
  height: calc(100vh - 110px);
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
