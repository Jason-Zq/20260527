<template>
  <div class="kv-tab">
    <div class="tab-toolbar">
      <span class="muted">共 {{ list.length }} 项 KV 字段（未纳入主表/子表的边角信息）</span>
    </div>

    <div v-if="list.length === 0" class="empty-inline">暂无 KV 信息</div>

    <div v-else class="kv-grid">
      <div v-for="info in list" :key="info.id" class="kv-row">
        <div class="kv-key">{{ info.info_key }}</div>
        <div class="kv-value">
          {{ info.info_value }}
          <el-tag v-if="info.confirmed" size="small" type="success" effect="light">已确认</el-tag>
          <el-tag v-if="info.valid_until" size="small" type="warning" effect="light">至 {{ info.valid_until }}</el-tag>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  list: { type: Array, default: () => [] },
})
</script>

<style scoped>
.tab-toolbar {
  margin-bottom: 12px;
}
.muted { font-size: 12px; color: #94a3b8; }
.empty-inline { text-align: center; color: #94a3b8; padding: 32px 0; font-size: 13px; }
.kv-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 16px;
}
.kv-row {
  background: #f8fafc;
  padding: 8px 12px;
  border-radius: 6px;
  border-left: 2px solid #6366f1;
}
.kv-key {
  font-size: 11px;
  color: #94a3b8;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  margin-bottom: 2px;
  font-weight: 500;
}
.kv-value {
  font-size: 13px;
  color: #1e293b;
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  word-break: break-all;
}
</style>
