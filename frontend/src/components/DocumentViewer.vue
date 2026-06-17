<template>
  <div class="document-viewer">
    <!-- 证件类型标签 -->
    <div class="doc-type-badge" v-if="docType">
      {{ docType }}
    </div>

    <!-- 纵向滚动图片列表 -->
    <div class="image-scroll">
      <div v-for="(img, idx) in images" :key="idx" class="image-page">
        <div class="page-label">第 {{ idx + 1 }} 页</div>
        <div class="image-frame">
          <img
            :src="getImageUrl(img)"
            alt="证件图片"
            class="page-image"
          />
        </div>
      </div>
      <div v-if="images.length === 0" class="empty-viewer">
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#cbd5e1" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
        <span>等待文件上传</span>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  images: { type: Array, default: () => [] },
  fields: { type: Object, default: () => ({}) },
  docType: { type: String, default: '' }
})

function getImageUrl(img) {
  return `/uploads/${img}`
}
</script>

<style scoped>
.document-viewer {
  position: relative;
  background: #ffffff;
  border-radius: 12px;
  overflow: hidden;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
}

.doc-type-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  padding: 4px 14px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  z-index: 10;
  letter-spacing: 0.5px;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
}

.image-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  align-items: center;
}

.image-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}

.page-label {
  font-size: 11px;
  color: #94a3b8;
  font-weight: 500;
  margin-bottom: 6px;
  align-self: flex-start;
  letter-spacing: 0.5px;
}

.image-frame {
  width: 100%;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  transition: box-shadow 0.25s;
}

.image-frame:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

.page-image {
  width: 100%;
  display: block;
}

.empty-viewer {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #cbd5e1;
  font-size: 13px;
  gap: 10px;
}
</style>
