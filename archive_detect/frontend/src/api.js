import axios from 'axios'

const API_BASE = '/api'

/**
 * 上传多文件 + 用户判定提示词。
 * @param {File[]} files
 * @param {string} userPrompt
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
 * URL 列表 + 提示词模式。
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
