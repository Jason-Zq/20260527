import axios from 'axios'
import router from './router'

const API_BASE = '/api'

// 鉴权:token 存 localStorage,axios 拦截器自动带 Bearer + 401 跳登录。
// 直接给 axios 默认实例挂拦截器(不新建实例),下面 63 处 axios.xxx 调用零改动即生效。
const TOKEN_KEY = 'doc_review_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

// 请求拦截:自动带 Authorization: Bearer <token>
axios.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截:401 清 token 跳登录
axios.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response && error.response.status === 401) {
      clearToken()
      if (router.currentRoute.value.path !== '/login') {
        router.push('/login')
      }
    }
    return Promise.reject(error)
  }
)

// 登录校验:账号密码
export async function login(username, password) {
  const resp = await axios.post(`${API_BASE}/auth/login`, { username, password })
  return resp.data
}

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
 * 轮询批次状态（含每文件状态与脱敏后结果）。
 */
export async function pollArchiveDetect(batchId) {
  const r = await axios.get(`${API_BASE}/archive-detect/${batchId}`)
  return r.data
}

// NOTE: 留底检测已支持 DB 双写持久化,history/delete 接口已恢复。

/**
 * 历史 batch 列表（不含 files 详情）。
 * @param {number} limit
 * @returns {Promise<{items: Array, total: number}>}
 */
export async function listArchiveDetectHistory(limit = 200) {
  const r = await axios.get(`${API_BASE}/archive-detect/history`, { params: { limit } })
  return r.data
}

/**
 * 删除一条历史 batch。
 * @param {string} batchId
 */
export async function deleteArchiveDetectBatch(batchId) {
  const r = await axios.delete(`${API_BASE}/archive-detect/${batchId}`)
  return r.data
}

// ==================== 业务接口(阶段三): 增量复用 + 业务字段透传 ====================

/**
 * 业务方批量提交进展包(JSON + OSS URL 模式)。
 * @param {Object} payload
 * @param {string} payload.criteria
 * @param {{client_code:string, name:string}} payload.client
 * @param {{progress_oid:string, handler?:string, project_name?:string, project_code?:string, project_detail_name?:string, project_detail_code?:string, progress_name?:string}} payload.progress
 * @param {Array<{file_id:string, filename?:string, url:string}>} payload.items
 * @returns {Promise<{batch_id:string, progress_id:number, total_files:number, reused_count:number, new_count:number}>}
 */
export async function submitBusinessBatch(payload) {
  const r = await axios.post(`${API_BASE}/archive-detect/business/batch`, payload, {
    timeout: 60000,
  })
  return r.data
}

/**
 * 业务接口轮询。返回完整结果含 client/progress/files/overall/reused_count/new_count。
 * @param {string} batchId
 */
export async function pollBusinessBatch(batchId) {
  const r = await axios.get(`${API_BASE}/archive-detect/business/batch/${batchId}`)
  return r.data
}

/**
 * 重新审核当前批次:复用 OCR 文本重新跑 AI,并生成新的 recheck batch。
 * @param {string} batchId 原批次 ID
 * @param {string} criteria 当前最新判定提示词
 * @param {string|null} stage pre_submit | post_submit | null
 * @param {boolean} regenerateCriteria 是否重新生成新规则 criteria
 */
export async function recheckArchiveDetectBatch(batchId, criteria, stage = null, regenerateCriteria = false) {
  const r = await axios.post(`${API_BASE}/archive-detect/recheck/${batchId}`, { criteria, stage, regenerate_criteria: regenerateCriteria }, {
    timeout: 60000,
  })
  return r.data
}

/**
 * 原地重跑批次：复用已有结果，只补跑缺失的
 * @param {string} batchId 批次 ID
 * @param {string} criteria 当前判定提示词
 * @param {string|null} stage pre_submit | post_submit | null
 * @param {boolean} forceAll 是否无视已有 AI 结果，全部用新 criteria 重跑
 * @param {boolean} regenerateCriteria 是否重新生成新规则 criteria
 */
export async function rerunArchiveDetectBatch(batchId, criteria, stage = null, forceAll = false, regenerateCriteria = false) {
  const r = await axios.post(
    `${API_BASE}/archive-detect/rerun/${batchId}?force_all=${forceAll}`,
    { criteria, stage, regenerate_criteria: regenerateCriteria },
    { timeout: 60000 },
  )
  return r.data
}

/** 批量重新检测单文件（重跑，复用 ocr_text）。支持指定 batch_ids 或按 verdicts 筛选 */
export async function startRerunFilesBatch({ verdicts = ['partial', 'mismatch'], batch_ids, regenerateCriteria = false } = {}) {
  const body = { verdicts, regenerate_criteria: regenerateCriteria }
  if (batch_ids && batch_ids.length) {
    body.batch_ids = batch_ids
  }
  const r = await axios.post(`${API_BASE}/archive-detect/admin/rerun-files-batch`, body)
  return r.data
}

/** 查询批量重新检测进度 */
export async function getRerunFilesProgress() {
  const r = await axios.get(`${API_BASE}/archive-detect/admin/rerun-files-batch/progress`)
  return r.data
}

// ==================== 客户档案结构化生成 ====================

export async function listClientProfileSourceFiles(clientId) {
  const r = await axios.get(`${API_BASE}/client-profile/source-files/${clientId}`)
  return r.data
}

export async function generateClientProfile(clientId, sourceFileIds) {
  const r = await axios.post(`${API_BASE}/client-profile/generate/${clientId}`, {
    source_file_ids: sourceFileIds,
  })
  return r.data
}

export async function getClientProfileGenerationTask(taskId) {
  const r = await axios.get(`${API_BASE}/client-profile/generate/${taskId}`)
  return r.data
}

export async function listClientProfileGenerationTasks(clientId, limit = 20) {
  const r = await axios.get(`${API_BASE}/client-profile/generate/list/${clientId}`, { params: { limit } })
  return r.data
}

// ==================== 销售线索 ====================

export async function listChildAgeLeads(params = {}) {
  const r = await axios.get(`${API_BASE}/sales/child-age-leads`, { params })
  return r.data
}

// ==================== 文件留底检测后台管理(只读) ====================

export async function listArchiveAdminBatches(params = {}) {
  const r = await axios.get(`${API_BASE}/archive-detect/admin/batches`, { params })
  return r.data
}

export async function listArchiveAdminProgress(params = {}) {
  const r = await axios.get(`${API_BASE}/archive-detect/admin/progress`, { params })
  return r.data
}

export async function listFileInfos(params = {}) {
  const r = await axios.get(`${API_BASE}/admin/file-infos`, { params })
  return r.data
}

export async function getArchiveAdminFileDetail(recordId) {
  const r = await axios.get(`${API_BASE}/archive-detect/admin/file/${recordId}`)
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

/**
 * 系统日志列表查询。
 * @param {Object} params - { severity, category, batch_id, since, until, limit, offset }
 * @returns {Promise<{items: Array, total: number}>}
 */
export async function listSystemEvents(params = {}) {
  const response = await axios.get(`${API_BASE}/admin/events`, { params })
  return response.data
}

/**
 * 系统日志 - category 枚举(下拉用)
 * @returns {Promise<{categories: string[]}>}
 */
export async function listEventCategories() {
  const response = await axios.get(`${API_BASE}/admin/events/categories`)
  return response.data
}


/**
 * 请求记录列表查询。
 * @param {Object} params - { method, path, since, until, limit, offset }
 * @returns {Promise<{items: Array, total: number}>}
 */
export async function listRequestLogs(params = {}) {
  const response = await axios.get(`${API_BASE}/admin/request-logs`, { params })
  return response.data
}

export async function listExternalApiLogs(params = {}) {
  const response = await axios.get(`${API_BASE}/admin/external-api-logs`, { params })
  return response.data
}

/**
 * AI/LLM API 调用记录查询
 */
export async function listAiApiCalls(params = {}) {
  const response = await axios.get(`${API_BASE}/admin/ai-api-calls`, { params })
  return response.data
}

export async function getAiApiCallDetail(rowId) {
  const response = await axios.get(`${API_BASE}/admin/ai-api-calls/${rowId}`)
  return response.data
}

// ==================== 客户画像(业务方接口导入) ====================

/**
 * 从业务方接口拉客户文件清单预览(按客户合并,不写库)
 * payload: {customer_code, operation_user}
 */
export async function previewProfileRemoteImport(payload) {
  const r = await axios.post(`${API_BASE}/profile/import-remote/preview`, payload, {
    timeout: 90000,
  })
  return r.data
}

/**
 * 按选中客户创建导入任务(后台串行跑 OCR/分类/提取)
 * payload: {customer_code, operation_user, customer_names: []}
 */
export async function importProfileRemote(payload) {
  const r = await axios.post(`${API_BASE}/profile/import-remote`, payload, {
    timeout: 90000,
  })
  return r.data
}

export async function listProfileTasks(params = {}) {
  const r = await axios.get(`${API_BASE}/profile/tasks`, { params })
  return r.data
}

export async function getProfileTask(taskId) {
  const r = await axios.get(`${API_BASE}/profile/tasks/${taskId}`)
  return r.data
}

export async function deleteProfileTask(taskId) {
  const r = await axios.delete(`${API_BASE}/profile/tasks/${taskId}`)
  return r.data
}

export async function listProfileTaskFiles(taskId, params = {}) {
  const r = await axios.get(`${API_BASE}/profile/tasks/${taskId}/files`, { params })
  return r.data
}

export async function getProfileTaskProfile(taskId) {
  const r = await axios.get(`${API_BASE}/profile/tasks/${taskId}/profile`)
  return r.data
}

export async function getDocExtractResult(rowId) {
  const r = await axios.get(`${API_BASE}/doc-extract/results/${rowId}`)
  return r.data
}

export async function getCustomerFile(fileId) {
  const r = await axios.get(`${API_BASE}/profile/files/${fileId}`)
  return r.data
}

// ==================== 复核中心 ====================

export async function listReviewFiles(params = {}) {
  const r = await axios.get(`${API_BASE}/review/files`, { params })
  return r.data
}

export async function getReviewFile(fileId) {
  const r = await axios.get(`${API_BASE}/review/files/${fileId}`)
  return r.data
}

export async function confirmReviewFile(fileId, payload = {}) {
  const r = await axios.post(`${API_BASE}/review/files/${fileId}/confirm`, payload)
  return r.data
}

export async function correctReviewFile(fileId, payload) {
  const r = await axios.post(`${API_BASE}/review/files/${fileId}/correct`, payload)
  return r.data
}

export async function dismissReviewFile(fileId, payload = {}) {
  const r = await axios.post(`${API_BASE}/review/files/${fileId}/dismiss`, payload)
  return r.data
}

// ==================== 文件归属 ====================

export async function listFilesForAssign(params = {}) {
  const r = await axios.get(`${API_BASE}/profile/files`, { params })
  return r.data
}

export async function listHouseholdPersons(householdId) {
  const r = await axios.get(`${API_BASE}/profile/households/${householdId}/persons`)
  return r.data
}

export async function regenerateHouseholdProfile(householdId) {
  const r = await axios.post(`${API_BASE}/profile/households/${householdId}/regenerate`)
  return r.data
}

export async function assignFilePerson(fileId, payload = {}) {
  const r = await axios.post(`${API_BASE}/profile/files/${fileId}/assign`, payload)
  return r.data
}

/**
 * 拉取原件(带 Bearer 鉴权),返回 { blobUrl, mime, revoke }
 * 用完调 revoke() 释放 objectURL。
 */
export async function fetchCustomerFileRawUrl(fileId) {
  const r = await axios.get(`${API_BASE}/profile/files/${fileId}/raw`, {
    responseType: 'blob',
    timeout: 120000,
  })
  const mime = r.headers['content-type'] || 'application/octet-stream'
  const blobUrl = URL.createObjectURL(r.data)
  return {
    blobUrl,
    mime,
    revoke: () => URL.revokeObjectURL(blobUrl),
  }
}

export async function correctPersonField(personId, fields, reviewedBy, relation) {
  const payload = { fields, reviewed_by: reviewedBy }
  if (relation !== undefined) payload.relation = relation
  const r = await axios.post(`${API_BASE}/profile/persons/${personId}/field`, payload)
  return r.data
}

export async function listPersonFiles(personId) {
  const r = await axios.get(`${API_BASE}/profile/persons/${personId}/files`)
  return r.data
}

export async function dedupeAssetsPreview(householdId) {
  const r = await axios.post(`${API_BASE}/profile/households/${householdId}/dedupe-assets/preview`)
  return r.data
}

export async function dedupeAssetsCommit(householdId, groups) {
  const r = await axios.post(`${API_BASE}/profile/households/${householdId}/dedupe-assets/commit`, { groups })
  return r.data
}

export async function getProfileTaskMatrix(taskId) {
  const r = await axios.get(`${API_BASE}/profile/tasks/${taskId}/matrix`)
  return r.data
}
