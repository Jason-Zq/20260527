using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Npgsql;
using DocReview.ArchiveDetect.Data;

namespace DocReview.ArchiveDetect.Services;

// 复刻 Python worker_runner + archive_detect_service 的队列消费 + finalize + watchdog。
// 单个 BackgroundService 里跑三件事:抢任务处理、finalize 轮询、watchdog 回收。
// 生产可拆成独立进程/服务,PoC 合并简化。
public class ArchiveWorker : BackgroundService
{
    private const int LeaseSeconds = 600;
    private const int MaxRetry = 1;

    private readonly IServiceScopeFactory _scopeFactory;
    private readonly OcrService _ocr;
    private readonly ILogger<ArchiveWorker> _log;
    private readonly string _connStr;

    private DateTime _lastFinalize = DateTime.MinValue;
    private DateTime _lastWatchdog = DateTime.MinValue;

    public ArchiveWorker(IServiceScopeFactory scopeFactory, OcrService ocr,
        IConfiguration cfg, ILogger<ArchiveWorker> log)
    {
        _scopeFactory = scopeFactory;
        _ocr = ocr;
        _log = log;
        _connStr = cfg.GetConnectionString("Default")!;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        _log.LogInformation("ArchiveWorker 启动");
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var claimed = await ProcessOneAsync(ct);

                // 周期性 finalize + watchdog(不阻塞抢任务节奏)
                var now = DateTime.Now;
                if ((now - _lastFinalize).TotalSeconds >= 3)
                {
                    await FinalizeBatchesAsync(ct);
                    _lastFinalize = now;
                }
                if ((now - _lastWatchdog).TotalSeconds >= 30)
                {
                    await ReclaimExpiredAsync(ct);
                    _lastWatchdog = now;
                }

                if (!claimed) await Task.Delay(2000, ct);   // 无任务退避
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogError(ex, "worker 循环异常");
                await Task.Delay(3000, ct);
            }
        }
    }

    // ── SKIP LOCKED 抢一个 pending 文件(原子) ──
    private async Task<ClaimedFile?> ClaimOneAsync(CancellationToken ct)
    {
        await using var conn = new NpgsqlConnection(_connStr);
        await conn.OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = $@"
            UPDATE archive_detect_files
            SET status='leased', worker_lease_until = now() + interval '{LeaseSeconds} seconds', updated_at=now()
            WHERE id = (
                SELECT id FROM archive_detect_files
                WHERE status='pending'
                ORDER BY created_at ASC, id ASC
                LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING id, batch_id, idx, file_id, source_url, filename, mime_type, retry_count, reuse_ocr_text";
        await using var rd = await cmd.ExecuteReaderAsync(ct);
        if (!await rd.ReadAsync(ct)) return null;
        return new ClaimedFile(
            rd.GetInt32(0), rd.GetString(1), rd.GetInt32(2),
            rd.IsDBNull(3) ? null : rd.GetString(3),
            rd.IsDBNull(4) ? null : rd.GetString(4),
            rd.IsDBNull(5) ? null : rd.GetString(5),
            rd.IsDBNull(6) ? null : rd.GetString(6),
            rd.GetInt32(7),
            rd.IsDBNull(8) ? null : rd.GetString(8));
    }

    private record ClaimedFile(int Id, string BatchId, int Idx, string? FileId,
        string? SourceUrl, string? Filename, string? MimeType, int RetryCount, string? ReuseOcrText);

    private async Task<bool> ProcessOneAsync(CancellationToken ct)
    {
        var task = await ClaimOneAsync(ct);
        if (task is null) return false;

        _log.LogInformation("抢到文件 id={id} batch={b} idx={i} file={f}",
            task.Id, task.BatchId, task.Idx, task.Filename);

        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var llm = scope.ServiceProvider.GetRequiredService<LlmService>();
        var fetcher = scope.ServiceProvider.GetRequiredService<FileFetcher>();

        var t0 = DateTime.Now;
        string? tempPath = null;
        var filename = task.Filename ?? "";

        try
        {
            string text;
            int? pageCount = null, charCount = null;

            if (!string.IsNullOrWhiteSpace(task.ReuseOcrText))
            {
                text = task.ReuseOcrText;   // 复用:跳过下载+OCR
            }
            else
            {
                if (string.IsNullOrWhiteSpace(task.SourceUrl))
                    throw new InvalidOperationException("文件来源缺失(无 source_url / reuse_ocr_text)");

                await SetStatusAsync(task.Id, "fetching", ct);
                // URL 可能过期,遇 401/403/404 用 file_id 刷新地址重试一次
                var fetched = await fetcher.FetchWithRefreshAsync(task.SourceUrl, task.FileId, task.Filename, ct);
                tempPath = fetched.LocalPath;
                if (fetched.Refreshed)
                    _log.LogInformation("文件 id={id} 通过刷新地址下载成功", task.Id);
                if (string.IsNullOrEmpty(filename)) filename = fetched.Filename;
                if (!FileFetcher.IsSupported(filename))
                    throw new InvalidOperationException($"不支持的文件类型: {Path.GetExtension(filename)}");

                await SetStatusAsync(task.Id, "ocr", ct);
                var ext = _ocr.Extract(tempPath);
                text = ext.Text;
                pageCount = ext.PageCount;
                charCount = ext.CharCount;

                if (string.IsNullOrWhiteSpace(text))
                {
                    // 无文字:标 no_text + done(不算失败,不重试)
                    await WriteDoneAsync(db, task.BatchId, task.Idx, filename, task.MimeType,
                        pageCount, charCount, false, 0, "no_text", 0,
                        "OCR/抽取后无有效文字", new List<string>(), "无文字", "",
                        (decimal)(DateTime.Now - t0).TotalSeconds, ct);
                    _log.LogInformation("文件 id={id} 无文字,标 no_text", task.Id);
                    return true;
                }
            }

            await SetStatusAsync(task.Id, "llm", ct);
            var ctx = await GetBatchContextAsync(db, task.BatchId, ct);
            var res = await llm.DetectArchivalAsync(text, ctx.Criteria, ctx.Stage,
                ctx.ClientName, ctx.Handler, ct);

            await WriteDoneAsync(db, task.BatchId, task.Idx, filename, task.MimeType,
                pageCount, charCount, res.IsArchival, res.Confidence, res.Verdict, res.MatchScore,
                res.Reason, res.KeyPoints, res.DocCategory, text,
                (decimal)(DateTime.Now - t0).TotalSeconds, ct);
            _log.LogInformation("文件 id={id} 完成 verdict={v} score={s}", task.Id, res.Verdict, res.MatchScore);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "文件 id={id} 处理失败", task.Id);
            await WriteErrorAsync(task.Id, task.BatchId, task.Idx,
                TextSanitizer.Clean(ex.Message, TextSanitizer.ErrorMsgLimit) ?? "错误",
                (decimal)(DateTime.Now - t0).TotalSeconds, ct);
        }
        finally
        {
            fetcher.Cleanup(tempPath);
        }
        return true;
    }

    private record BatchCtx(string Criteria, string Stage, string? ClientName, string? Handler);

    private async Task<BatchCtx> GetBatchContextAsync(AppDbContext db, string batchId, CancellationToken ct)
    {
        var b = await db.Batches.AsNoTracking().FirstOrDefaultAsync(x => x.BatchId == batchId, ct)
                ?? throw new InvalidOperationException($"批次 {batchId} 元信息丢失");
        string? clientName = null, handler = null;
        if (b.ProgressId is int pid)
        {
            var p = await db.Progresses.AsNoTracking().FirstOrDefaultAsync(x => x.Id == pid, ct);
            handler = p?.Handler;
            if (p != null)
            {
                var c = await db.Clients.AsNoTracking().FirstOrDefaultAsync(x => x.Id == p.ClientId, ct);
                clientName = c?.Name;
            }
        }
        return new BatchCtx(b.UserPrompt ?? "", b.Stage ?? "post_submit", clientName, handler);
    }

    // ── 写终态 done(所有 text/JSONB 字段清洗) ──
    private async Task WriteDoneAsync(AppDbContext db, string batchId, int idx,
        string? filename, string? mime, int? pageCount, int? charCount,
        bool isArchival, int confidence, string verdict, int matchScore,
        string reason, List<string> keyPoints, string docCategory, string ocrText,
        decimal elapsed, CancellationToken ct)
    {
        var f = await db.Files.FirstAsync(x => x.BatchId == batchId && x.Idx == idx, ct);
        f.Status = "done";
        f.Filename = TextSanitizer.Clean(filename, TextSanitizer.SmallTextLimit);
        f.MimeType = TextSanitizer.Clean(mime, TextSanitizer.SmallTextLimit);
        f.PageCount = pageCount;
        f.CharCount = charCount;
        f.IsArchival = isArchival;
        f.Confidence = confidence;
        f.Verdict = verdict;
        f.MatchScore = matchScore;
        f.Reason = TextSanitizer.Clean(reason, TextSanitizer.ReasonLimit);
        var cleanKp = keyPoints.Select(k => TextSanitizer.Clean(k, TextSanitizer.SmallTextLimit)).ToList();
        f.KeyPoints = JsonSerializer.Serialize(cleanKp);
        f.DocCategory = TextSanitizer.Clean(docCategory, TextSanitizer.SmallTextLimit);
        f.OcrText = TextSanitizer.Clean(ocrText, TextSanitizer.OcrTextLimit);
        f.ElapsedSec = elapsed;
        f.UpdatedAt = DateTime.Now;
        await db.SaveChangesAsync(ct);
    }

    private async Task WriteErrorAsync(int fileDbId, string batchId, int idx, string msg,
        decimal elapsed, CancellationToken ct)
    {
        await using var conn = new NpgsqlConnection(_connStr);
        await conn.OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = @"UPDATE archive_detect_files
            SET status='error', error_msg=@m, elapsed_sec=@e, updated_at=now()
            WHERE id=@id";
        cmd.Parameters.AddWithValue("m", msg);
        cmd.Parameters.AddWithValue("e", elapsed);
        cmd.Parameters.AddWithValue("id", fileDbId);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    private async Task SetStatusAsync(int fileDbId, string status, CancellationToken ct)
    {
        await using var conn = new NpgsqlConnection(_connStr);
        await conn.OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        // 续租:延长 lease,防 watchdog 误回收
        cmd.CommandText = $@"UPDATE archive_detect_files
            SET status=@s, worker_lease_until = now() + interval '{LeaseSeconds} seconds', updated_at=now()
            WHERE id=@id";
        cmd.Parameters.AddWithValue("s", status);
        cmd.Parameters.AddWithValue("id", fileDbId);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    // ── finalize:所有文件终态的 running 批次生成 overall ──
    private async Task FinalizeBatchesAsync(CancellationToken ct)
    {
        using var scope = _scopeFactory.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();

        var runningBatches = await db.Batches.Where(b => b.Status == "running").ToListAsync(ct);
        foreach (var b in runningBatches)
        {
            var files = await db.Files.Where(f => f.BatchId == b.BatchId).ToListAsync(ct);
            if (files.Count == 0) continue;
            bool allTerminal = files.All(f => f.Status == "done" || f.Status == "error");
            if (!allTerminal) continue;

            // 规则平均分(PoC 简化,不调 LLM 总判):排除 no_text
            var scored = files.Where(f => f.Status == "done" && f.Verdict != "no_text" && f.MatchScore != null).ToList();
            int avg = scored.Count > 0 ? (int)scored.Average(f => f.MatchScore!.Value) : 0;
            string verdict = avg >= 80 ? "match" : avg >= 50 ? "partial" : "mismatch";
            string reason = $"共 {files.Count} 个文件,有效判定 {scored.Count} 个,平均分 {avg}。(PoC 规则平均,未调总判 LLM)";

            b.OverallVerdict = verdict;
            b.OverallScore = avg;
            b.OverallReason = TextSanitizer.Clean(reason, TextSanitizer.ReasonLimit);
            b.DoneFiles = files.Count;
            b.Status = "done";
            b.UpdatedAt = DateTime.Now;
            await db.SaveChangesAsync(ct);
            _log.LogInformation("批次 {b} finalize: {v} {s}", b.BatchId, verdict, avg);
        }
    }

    // ── watchdog:回收超时 leased ──
    private async Task ReclaimExpiredAsync(CancellationToken ct)
    {
        await using var conn = new NpgsqlConnection(_connStr);
        await conn.OpenAsync(ct);
        await using var cmd = conn.CreateCommand();
        cmd.CommandText = $@"
            UPDATE archive_detect_files
            SET status = CASE WHEN retry_count < {MaxRetry} THEN 'pending' ELSE 'error' END,
                worker_lease_until = NULL,
                retry_count = CASE WHEN retry_count < {MaxRetry} THEN retry_count + 1 ELSE retry_count END,
                error_msg = CASE WHEN retry_count >= {MaxRetry} THEN COALESCE(error_msg,'worker 多次失败,放弃重试') ELSE error_msg END,
                updated_at = now()
            WHERE status='leased' AND worker_lease_until < now()";
        var n = await cmd.ExecuteNonQueryAsync(ct);
        if (n > 0) _log.LogInformation("watchdog 回收 {n} 个超时任务", n);
    }
}
