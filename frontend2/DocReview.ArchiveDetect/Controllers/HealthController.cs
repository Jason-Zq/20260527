using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using DocReview.ArchiveDetect.Data;

namespace DocReview.ArchiveDetect.Controllers;

/// <summary>
/// 健康检查。对标 Python 后端 GET /api/healthz。
/// </summary>
[ApiController]
[Route("api")]
[Produces("application/json")]
public class HealthController : ControllerBase
{
    private readonly AppDbContext _db;

    public HealthController(AppDbContext db) => _db = db;

    /// <summary>
    /// 健康检查探针:真查数据库连通性 + 当前队列深度。
    /// </summary>
    /// <remarks>
    /// 执行 <c>SELECT 1</c> 验证 DB 可达,并统计处于 pending/leased/fetching/ocr/llm 的文件数作为队列深度。
    /// 供 nginx / 外部监控探活使用。
    /// </remarks>
    /// <response code="200">DB 正常,返回 status=ok 与 queue_depth</response>
    /// <response code="503">DB 不可达,返回 status=unhealthy 与错误信息</response>
    [HttpGet("healthz")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status503ServiceUnavailable)]
    public async Task<IActionResult> Healthz(CancellationToken ct)
    {
        try
        {
            await _db.Database.ExecuteSqlRawAsync("SELECT 1", ct);
            var pending = await _db.Files.CountAsync(f =>
                f.Status == "pending" || f.Status == "leased" ||
                f.Status == "fetching" || f.Status == "ocr" || f.Status == "llm", ct);
            return Ok(new { status = "ok", queue_depth = pending });
        }
        catch (Exception ex)
        {
            return StatusCode(503, new { status = "unhealthy", error = ex.Message });
        }
    }
}
