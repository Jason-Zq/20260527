import axios from 'axios'

const API_BASE = '/api'

/**
 * 上传文件（PDF/图片），返回 task_id（异步模式）
 * @param {File} file
 * @param {number|null} [clientId] 可选：A1 批量队列模式下绑定到指定客户，
 *   解析完成后会自动归档（跳过人工复核）
 */
export async function uploadFile(file, clientId = null) {
  const formData = new FormData()
  formData.append('file', file)
  if (clientId != null) {
    formData.append('client_id', String(clientId))
  }
  const response = await axios.post(`${API_BASE}/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30000
  })
  return response.data
}

/**
 * 轮询获取任务结果/进度
 */
export async function pollResult(taskId) {
  const response = await axios.get(`${API_BASE}/result/${taskId}`)
  return response.data
}

/**
 * 保存人工复核修正结果
 *
 * 第二个参数支持两种形态：
 *   1) 旧：array — items 数组（向后兼容）
 *   2) 新：object — { items, archive: {client_id, entity, target_id, sub_meta} }
 *
 * 新归档 payload 走 backend 的 archive_document 路径，把字段精准路由到
 * clients/family/assets 表（未映射的进 client_info KV）。
 */
export async function saveReview(taskId, itemsOrPayload) {
  let body
  if (Array.isArray(itemsOrPayload)) {
    body = { task_id: taskId, items: itemsOrPayload }
  } else {
    body = { task_id: taskId, ...itemsOrPayload }
  }
  const response = await axios.put(`${API_BASE}/result/${taskId}`, body)
  return response.data
}

/**
 * 获取历史记录列表
 */
export async function getHistory() {
  const response = await axios.get(`${API_BASE}/history`)
  return response.data
}

/**
 * 导出解析结果 JSON
 */
export function exportResult(taskId) {
  window.open(`${API_BASE}/export/${taskId}`, '_blank')
}

/**
 * 删除一条历史记录
 */
export async function deleteHistory(taskId) {
  const response = await axios.delete(`${API_BASE}/history/${taskId}`)
  return response.data
}

/**
 * 全文搜索文档
 */
export async function searchDocuments(keyword) {
  const response = await axios.get(`${API_BASE}/search`, {
    params: { keyword }
  })
  return response.data
}

/**
 * 客户列表
 */
export async function listClients(keyword, options = {}) {
  const params = {}
  if (keyword) params.keyword = keyword
  if (options.visa_type) params.visa_type = options.visa_type
  if (options.expiring_soon_days != null) params.expiring_soon_days = options.expiring_soon_days
  if (options.sort_by) params.sort_by = options.sort_by
  const response = await axios.get(`${API_BASE}/clients`, { params })
  return response.data
}

/**
 * 客户详情（含 family / assets / infos / documents）
 */
export async function getClientDetail(clientId) {
  const response = await axios.get(`${API_BASE}/clients/${clientId}`)
  return response.data
}

/**
 * 新建客户（"+新建"按钮触发）
 */
export async function createClient(payload) {
  const response = await axios.post(`${API_BASE}/clients`, payload)
  return response.data
}

/**
 * 更新客户主表（部分字段）
 */
export async function updateClient(clientId, payload) {
  const response = await axios.put(`${API_BASE}/clients/${clientId}`, payload)
  return response.data
}

/**
 * 客户智能匹配（OCR 后查找现有客户候选）
 * @param {Object} criteria { id_number?, passport_no?, name?, birth_date? }
 * @returns {Promise<{candidates, best_match_client_id, total}>}
 */
export async function matchClients(criteria) {
  const response = await axios.post(`${API_BASE}/clients/match`, criteria)
  return response.data
}

// ==================== 家庭成员 ====================

export async function listFamily(clientId) {
  const r = await axios.get(`${API_BASE}/clients/${clientId}/family`)
  return r.data
}

export async function createFamily(clientId, payload) {
  const r = await axios.post(`${API_BASE}/clients/${clientId}/family`, payload)
  return r.data
}

export async function updateFamily(memberId, payload) {
  const r = await axios.put(`${API_BASE}/family/${memberId}`, payload)
  return r.data
}

export async function deleteFamily(memberId) {
  const r = await axios.delete(`${API_BASE}/family/${memberId}`)
  return r.data
}

// ==================== 资产 ====================

export async function listAssets(clientId) {
  const r = await axios.get(`${API_BASE}/clients/${clientId}/assets`)
  return r.data
}

export async function createAsset(clientId, payload) {
  const r = await axios.post(`${API_BASE}/clients/${clientId}/assets`, payload)
  return r.data
}

export async function updateAsset(assetId, payload) {
  const r = await axios.put(`${API_BASE}/assets/${assetId}`, payload)
  return r.data
}

export async function deleteAsset(assetId) {
  const r = await axios.delete(`${API_BASE}/assets/${assetId}`)
  return r.data
}

// ==================== 字段路由器元数据 ====================

/**
 * 获取已知 doc_type 列表（DocTypeSelector 下拉数据源）
 */
export async function getDocTypes() {
  const r = await axios.get(`${API_BASE}/field-router/doc-types`)
  return r.data
}

// ==================== 文件解析（URL → 摘要） ====================

/**
 * 同步：URL → OCR/抽取 → AI 摘要+相关性判断 → 入库 → 返回完整结果
 * 总耗时通常 30-200s，超时 5 分钟
 * @param {string} url
 * @param {string} progressName 必填，进展名称（如"美国EB5-资金来源证明"）
 * @returns {Promise<Object>}  含 summary/key_points/relevance 等
 */
export async function summarizeFile(url, progressName) {
  const r = await axios.post(`${API_BASE}/file-summary`, {
    url,
    progress_name: progressName,
  }, { timeout: 300000 })
  return r.data
}

/**
 * 摘要历史列表（不含 extracted_text，按时间倒序）
 */
export async function listSummaries(limit = 100, offset = 0) {
  const r = await axios.get(`${API_BASE}/summaries`, { params: { limit, offset } })
  return r.data
}

/**
 * 摘要详情（含 extracted_text）
 */
export async function getSummary(summaryId) {
  const r = await axios.get(`${API_BASE}/summaries/${summaryId}`)
  return r.data
}

/**
 * 删除摘要记录
 */
export async function deleteSummary(summaryId) {
  const r = await axios.delete(`${API_BASE}/summaries/${summaryId}`)
  return r.data
}

// ==================== 文件留底检测（archive-detect） ====================

/**
 * 上传多个文件 + 用户判定提示词，提交一个检测批次。
 * @param {File[]} files
 * @param {string} userPrompt 用户多行提示词
 * @returns {Promise<{batch_id: string, total_files: number}>}
 */
export async function submitArchiveDetectUpload(files, userPrompt) {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  fd.append('user_prompt', userPrompt)
  const r = await axios.post(`${API_BASE}/archive-detect/upload`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return r.data
}

/**
 * URL 列表 + 提示词模式提交。
 * @param {string[]} urls
 * @param {string} userPrompt
 */
export async function submitArchiveDetectUrls(urls, userPrompt) {
  const r = await axios.post(`${API_BASE}/archive-detect/urls`,
    { urls, user_prompt: userPrompt },
    { timeout: 60000 })
  return r.data
}

/**
 * 轮询批次状态（含每文件状态与脱敏后结果）。
 */
export async function pollArchiveDetect(batchId) {
  const r = await axios.get(`${API_BASE}/archive-detect/${batchId}`)
  return r.data
}

/**
 * 历史 batch 列表（不含 files）。
 */
export async function listArchiveDetectHistory(limit = 200) {
  const r = await axios.get(`${API_BASE}/archive-detect/history`, { params: { limit } })
  return r.data
}

/**
 * 删除一条历史。
 */
export async function deleteArchiveDetect(batchId) {
  const r = await axios.delete(`${API_BASE}/archive-detect/${batchId}`)
  return r.data
}

// ==================== Word 模板 相关接口 (v2: anchor-based) ====================

/**
 * v2：上传 docx 解析（不入库）：
 *   - mammoth 转 HTML
 *   - scan_anchors 输出 anchor 候选列表
 *   - enrich_anchors_with_llm 给每个 anchor 加 description + field_hint
 *   - 渲染 Word 原貌 PNG
 *
 * 返回：{html, anchors: [{id, anchor, label_context, description, field_hint, default_fmt}], pages, temp_token, filename}
 */
export async function parseTemplate(file) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await axios.post(`${API_BASE}/templates/parse`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000
  })
  return response.data
}

/**
 * v2：保存模板（手动路径：用户编辑/删除 anchor 列表后保存）
 * payload: {name, filename, anchors: [{id, anchor, description, field_hint, default_fmt}], temp_token}
 */
export async function saveTemplate(payload) {
  const response = await axios.post(`${API_BASE}/templates`, payload, {
    timeout: 60000
  })
  return response.data
}

/**
 * v2：快速保存（自动采纳所有 anchor 候选 + parse 阶段缓存的 enrich）
 * payload: {name, filename, temp_token}
 * @returns {Promise<{id:number, name:string, placeholder_count:number, message:string}>}
 */
export async function quickSaveTemplate(payload) {
  const response = await axios.post(`${API_BASE}/templates/quick-save`, payload, {
    timeout: 60000
  })
  return response.data
}

/**
 * 模板列表
 */
export async function listTemplates() {
  const response = await axios.get(`${API_BASE}/templates`)
  return response.data
}

/**
 * 模板详情
 */
export async function getTemplate(id) {
  const response = await axios.get(`${API_BASE}/templates/${id}`)
  return response.data
}

/**
 * 获取模板预览 HTML + 占位符元数据
 */
export async function getTemplatePreviewHtml(id) {
  const response = await axios.get(`${API_BASE}/templates/${id}/preview-html`)
  return response.data
}

/**
 * 获取模板 Word 原貌 PNG 页面 URL 列表
 */
export async function getTemplatePreviewPages(id) {
  const response = await axios.get(`${API_BASE}/templates/${id}/preview-pages`, {
    timeout: 120000
  })
  return response.data
}

/**
 * 删除模板
 */
export async function deleteTemplate(id) {
  const response = await axios.delete(`${API_BASE}/templates/${id}`)
  return response.data
}

/**
 * v2：选客户 → anchor 字段匹配（field_hint 规则优先 + LLM 兜底，带缓存）
 * 返回：{matched: {strN: value}, unmatched: [strN], from_cache}
 */
export async function mapClientToTemplate(id, clientId) {
  const response = await axios.post(`${API_BASE}/templates/${id}/map-client`, {
    client_id: clientId
  }, {
    timeout: 120000
  })
  return response.data
}

/**
 * v2：生成 PDF（基于 anchor + apply_value 渲染）
 * payload: {client_id?, anchor_values: {strN: value}}
 */
export async function generateTemplatePdf(id, payload) {
  const response = await axios.post(`${API_BASE}/templates/${id}/generate`, payload, {
    responseType: 'blob',
    timeout: 180000
  })
  // 推断文件名
  let filename = 'output.pdf'
  const isFallback = response.headers['x-fallback-docx'] === '1'
  const cd = response.headers['content-disposition'] || ''
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd)
  if (star) {
    try { filename = decodeURIComponent(star[1]) } catch (e) { /* ignore */ }
  } else {
    const plain = /filename="?([^";]+)"?/i.exec(cd)
    if (plain) filename = plain[1]
  }
  if (isFallback && !/\.docx$/i.test(filename)) {
    filename = filename.replace(/\.pdf$/i, '') + '.docx'
  }

  const blob = response.data
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)

  return { filename, isFallback }
}

/**
 * 字段字典（前端下拉选项）—— 简化版，列出 v2 hardcoded 字段。
 * 用于 TemplateFillPage 中让用户把 anchor 强制关联到某个 field_hint。
 *
 * locked: true 标记法定/核心字段（身份证、护照号、出生日期、姓名、国籍）。
 * 模板填写时这些字段渲染为只读，不允许在生成界面就地修改，
 * 必须回到客户档案统一编辑——对齐 Immigo "主数据只读、业务案件仅引用"原则。
 */
export const FIELD_DICTIONARY_OPTIONS = [
  { value: '',                   label: '（不指定）',     locked: false },
  { value: 'name',               label: '姓名',           locked: true },
  { value: 'id_number',          label: '证件号',         locked: true },
  { value: 'gender',             label: '性别',           locked: false },
  { value: 'birth_date',         label: '出生日期',       locked: true },
  { value: 'nationality',        label: '国籍',           locked: true },
  { value: 'consultant',         label: '顾问',           locked: false },
  { value: 'issuing_authority',  label: '签发机关',       locked: false },
  { value: 'issue_date',         label: '签发日期',       locked: false },
  { value: 'expiry_date',        label: '有效期至',       locked: false },
  { value: 'address',            label: '地址',           locked: false },
  { value: 'phone',              label: '电话',           locked: false },
  { value: 'email',              label: '邮箱',           locked: false },
  { value: 'occupation',         label: '职业',           locked: false },
  { value: 'employer',           label: '工作单位',       locked: false },
  { value: 'marital_status',     label: '婚姻状况',       locked: false },
  { value: 'emergency_contact',  label: '紧急联系人',     locked: false },
  { value: 'today',              label: '今日日期',       locked: false },
  { value: 'signature_place',    label: '签字地点',       locked: false },
  { value: 'amount',             label: '金额',           locked: false },
  { value: 'notes',              label: '备注',           locked: false },
]

/** 锁定字段集合，便于 O(1) 判断。 */
export const LOCKED_FIELD_HINTS = new Set(
  FIELD_DICTIONARY_OPTIONS.filter(o => o.locked).map(o => o.value)
)

/**
 * 反向同步主数据（B1）。
 * @param {number} clientId
 * @param {Record<string, string>} keyValues  键为字典 label（例如 "地址"）或 anchor description
 */
export async function upsertClientInfo(clientId, keyValues) {
  const response = await axios.post(`${API_BASE}/clients/${clientId}/info`, {
    key_values: keyValues,
  })
  return response.data
}

/**
 * 客户的模板生成历史（B2）。
 */
export async function getClientFills(clientId) {
  const response = await axios.get(`${API_BASE}/clients/${clientId}/fills`)
  return response.data
}

// ==================== 处理超长PDF文件（自动按证件拆分） ====================

/**
 * 上传多证件 PDF,立即返回 task_id。
 * @param {File} file PDF 文件(.pdf)
 * @returns {Promise<{task_id: string, status: string}>}
 */
export async function uploadSplitPdf(file) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await axios.post(`${API_BASE}/split`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000
  })
  return response.data
}

/**
 * 轮询拆分任务进度/结果。
 * @param {string} taskId
 * @returns {Promise<{status: 'ocr'|'llm'|'splitting'|'done'|'error', progress: string, error: string, result: object|null}>}
 */
export async function pollSplit(taskId) {
  const response = await axios.get(`${API_BASE}/split/${taskId}`)
  return response.data
}

/**
 * 拼接单份子 PDF 的下载 URL。
 * @param {string} taskId
 * @param {number} idx 0-based 索引
 * @returns {string}
 */
export function downloadSplitUrl(taskId, idx) {
  return `${API_BASE}/split/${taskId}/download/${idx}`
}

/**
 * 拼接打包下载 zip 的 URL。
 * @param {string} taskId
 * @returns {string}
 */
export function downloadSplitAllUrl(taskId) {
  return `${API_BASE}/split/${taskId}/download-all`
}

/**
 * 拉取拆分任务历史列表(按 created_at 倒序)。
 * @param {number} [limit=200]
 * @returns {Promise<{history: Array, total: number}>}
 */
export async function listSplitHistory(limit = 200) {
  const response = await axios.get(`${API_BASE}/split/history`, { params: { limit } })
  return response.data
}

/**
 * 删除一条拆分历史(DB 记录 + output/{task_id}/ 目录)。
 * @param {string} taskId
 * @returns {Promise<{message: string, task_id: string}>}
 */
export async function deleteSplitHistory(taskId) {
  const response = await axios.delete(`${API_BASE}/split/history/${encodeURIComponent(taskId)}`)
  return response.data
}