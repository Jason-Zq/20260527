<template>
  <!-- 文件清单抽屉:表格(文件名称/文件类型/操作);点「查看」弹最外层预览弹窗。文件数据由父组件注入。 -->
  <el-drawer v-model="visible" :title="title" :size="drawerWidth + 'px'" :modal="false" modal-class="side-drawer-overlay" append-to-body>
    <div class="drawer-resize-handle" @mousedown="startResize"></div>
    <el-table :data="files" v-loading="loading" stripe size="small" empty-text="无关联文件">
      <el-table-column label="文件名称" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.filename || row.file_code }}</template>
      </el-table-column>
      <el-table-column label="文件类型" width="110" align="center">
        <template #default="{ row }">
          <el-tag size="small">{{ docTypeLabel(row.doc_type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="onPreview(row)">查看</el-button>
        </template>
      </el-table-column>
    </el-table>
    <FilePreviewDialog v-model:visible="previewVisible" :file="previewFile" />
  </el-drawer>
</template>

<script setup>
import { computed, ref } from 'vue'
import FilePreviewDialog from './FilePreviewDialog.vue'
import { docTypeLabel } from '../utils/labels'

const props = defineProps({
  visible: { type: Boolean, default: false },
  drawerWidth: { type: Number, default: () => Math.round(window.innerWidth / 3) },
  title: { type: String, default: '查看文件' },
  files: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'update:drawerWidth'])

const visible = computed({ get: () => props.visible, set: (v) => emit('update:visible', v) })
const drawerWidth = computed({ get: () => props.drawerWidth, set: (v) => emit('update:drawerWidth', v) })

const previewVisible = ref(false)
const previewFile = ref(null)

function onPreview(row) {
  previewFile.value = row
  previewVisible.value = true
}

function startResize(e) {
  e.preventDefault()
  const onMove = (ev) => {
    const w = window.innerWidth - ev.clientX
    drawerWidth.value = Math.min(Math.max(w, 300), window.innerWidth - 120)
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
</script>

<style scoped>
.drawer-resize-handle {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  cursor: col-resize;
  background: #dcdfe6;
  z-index: 10;
}
.drawer-resize-handle:hover,
.drawer-resize-handle:active {
  background: #409eff;
}
</style>
