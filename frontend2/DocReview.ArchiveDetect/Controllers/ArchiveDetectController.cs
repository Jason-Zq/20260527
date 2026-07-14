using Microsoft.AspNetCore.Mvc;
using DocReview.ArchiveDetect.Models;
using DocReview.ArchiveDetect.Services;

namespace DocReview.ArchiveDetect.Controllers;

/// <summary>
/// 文件留底检测 / 业务审核。对标 Python 后端 /api/archive-detect/business/*。
/// </summary>
/// <remarks>
/// 业务方以 JSON + 文件 URL 提交进展包批次;接口只校验 + 写库(pending)后秒回,
/// 真正的下载 / OCR / LLM 判定由后台 worker(<see cref="ArchiveWorker"/>)异步串行处理。
/// </remarks>
[ApiController]
[Route("api/archive-detect")]
[Produces("application/json")]
public class ArchiveDetectController : ControllerBase
{
    private readonly ArchiveDetectService _svc;
    private readonly ILogger<ArchiveDetectController> _log;

    public ArchiveDetectController(ArchiveDetectService svc, ILogger<ArchiveDetectController> log)
    {
        _svc = svc;
        _log = log;
    }

    /// <summary>
    /// 业务批量提交:提交一个进展包及其文件列表,写入队列后秒回批次号。
    /// </summary>
    /// <remarks>
    /// 处理步骤:
    /// 1. 校验 criteria / stage / 每个文件 URL(必须 http/https);
    /// 2. 按 client_code upsert 客户、按 (client_id, progress_oid) upsert 进展包;
    /// 3. 写入 1 条 batch(running) + N 条 file(pending)行;
    /// 4. 立即返回 batch_id,不等待 OCR/LLM。
    ///
    /// 请求体示例:
    /// <code>
    /// {
    ///   "criteria": "审核此文件是否为公司留底相关文件",
    ///   "stage": "post_submit",
    ///   "client": { "client_code": "U-123", "name": "张三" },
    ///   "progress": { "progress_oid": "OID-1", "handler": "李四", "project_name": "递交" },
    ///   "items": [ { "file_id": "f1", "filename": "护照.pdf", "url": "https://.../a.pdf" } ]
    /// }
    /// </code>
    /// </remarks>
    /// <param name="payload">批次提交请求体</param>
    /// <param name="ct">取消令牌</param>
    /// <response code="200">入队成功,返回 batch_id / progress_id / total_files</response>
    /// <response code="400">参数校验失败(items 为空、criteria 为空、stage 非法、URL 非法)</response>
    [HttpPost("business/batch")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> SubmitBusinessBatch([FromBody] BusinessBatchPayload payload, CancellationToken ct)
    {
        try
        {
            var result = await _svc.SubmitBusinessBatchAsync(payload, ct);
            return Ok(new
            {
                batch_id = result.BatchId,
                progress_id = result.ProgressId,
                total_files = result.TotalFiles
            });
        }
        catch (ArgumentException ex)
        {
            return BadRequest(new { error = ex.Message });
        }
    }

    /// <summary>
    /// 业务批次轮询:按批次号查询整批的处理进度与每个文件的判定结果。
    /// </summary>
    /// <remarks>
    /// 返回 batch 概要(status / overall_verdict / overall_score / overall_reason)
    /// 以及 files 明细(每个文件的 status / verdict / match_score / doc_category / reason / key_points 等)。
    /// 前端提交后应轮询本接口直到 status=done。
    /// </remarks>
    /// <param name="batchId">批次号(提交接口返回的 batch_id)</param>
    /// <param name="ct">取消令牌</param>
    /// <response code="200">返回批次完整结果</response>
    /// <response code="404">批次不存在</response>
    [HttpGet("business/batch/{batchId}")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> GetBusinessBatch(string batchId, CancellationToken ct)
    {
        var result = await _svc.GetBusinessBatchAsync(batchId, ct);
        if (result is null) return NotFound(new { error = "批次不存在" });
        return Ok(result);
    }
}
