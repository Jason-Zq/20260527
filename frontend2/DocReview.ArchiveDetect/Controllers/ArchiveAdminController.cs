using Microsoft.AspNetCore.Mvc;
using DocReview.ArchiveDetect.Services;

namespace DocReview.ArchiveDetect.Controllers;

/// <summary>
/// 检测批次管理后台。对标 Python 后端 /api/archive-detect/admin/*。
/// </summary>
[ApiController]
[Route("api/archive-detect/admin")]
[Produces("application/json")]
public class ArchiveAdminController : ControllerBase
{
    private readonly ArchiveDetectService _svc;

    public ArchiveAdminController(ArchiveDetectService svc) => _svc = svc;

    /// <summary>
    /// 队列实时统计:当前未完成文件数(queue_depth)与运行中批次数(in_flight_batches)。
    /// </summary>
    /// <param name="ct">取消令牌</param>
    /// <response code="200">返回队列深度与在途批次数</response>
    [HttpGet("queue-stats")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> QueueStats(CancellationToken ct)
        => Ok(await _svc.GetQueueStatsAsync(ct));

    /// <summary>
    /// 后台批次列表:按创建时间倒序分页,联表返回客户与进展包信息。
    /// </summary>
    /// <param name="limit">每页条数(默认 100,上限 500)</param>
    /// <param name="offset">偏移量(默认 0)</param>
    /// <param name="ct">取消令牌</param>
    /// <response code="200">返回 items 列表与 total 总数</response>
    [HttpGet("batches")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> Batches([FromQuery] int limit = 100, [FromQuery] int offset = 0, CancellationToken ct = default)
        => Ok(await _svc.ListAdminBatchesAsync(limit, offset, ct));
}
