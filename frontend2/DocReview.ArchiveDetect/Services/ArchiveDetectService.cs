using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using DocReview.ArchiveDetect.Data;
using DocReview.ArchiveDetect.Models;

namespace DocReview.ArchiveDetect.Services;

// 文件留底检测业务编排层:批次提交入队、查询。
// 对标 Python backend/archive_detect_service.py 的 submit_business_batch / get_business_batch。
public class ArchiveDetectService
{
    private readonly AppDbContext _db;

    public ArchiveDetectService(AppDbContext db) => _db = db;

    // ── 业务批量提交:校验 + upsert client/progress + 写 pending 入队,秒回 ──
    public async Task<SubmitBatchResult> SubmitBusinessBatchAsync(BusinessBatchPayload payload, CancellationToken ct = default)
    {
        if (payload.Items is null || payload.Items.Count == 0)
            throw new ArgumentException("items 不能为空");
        if (string.IsNullOrWhiteSpace(payload.Criteria))
            throw new ArgumentException("criteria 不能为空");
        var stage = string.IsNullOrWhiteSpace(payload.Stage) ? "post_submit" : payload.Stage;
        if (stage != "pre_submit" && stage != "post_submit")
            throw new ArgumentException("stage 仅限 pre_submit / post_submit");
        foreach (var it in payload.Items)
            if (string.IsNullOrWhiteSpace(it.Url) || !(it.Url.StartsWith("http://") || it.Url.StartsWith("https://")))
                throw new ArgumentException($"文件 URL 非法: {it.Url}");

        // upsert client by client_code
        var code = payload.Client?.ClientCode ?? "";
        var name = payload.Client?.Name ?? "";
        var client = await _db.Clients.FirstOrDefaultAsync(c => c.ClientCode == code, ct);
        if (client is null)
        {
            client = new Client { ClientCode = code, Name = name, CreatedAt = DateTime.Now, UpdatedAt = DateTime.Now };
            _db.Clients.Add(client);
            await _db.SaveChangesAsync(ct);
        }

        // upsert progress by (client_id, progress_oid)
        var oid = payload.Progress?.ProgressOid ?? "";
        var progress = await _db.Progresses.FirstOrDefaultAsync(p => p.ClientId == client.Id && p.ProgressOid == oid, ct);
        if (progress is null)
        {
            progress = new ArchiveDetectProgress
            {
                ClientId = client.Id,
                ProgressOid = oid,
                Handler = payload.Progress?.Handler,
                ProjectName = payload.Progress?.ProjectName,
                ProjectCode = payload.Progress?.ProjectCode,
                ProjectDetailName = payload.Progress?.ProjectDetailName,
                ProjectDetailCode = payload.Progress?.ProjectDetailCode,
                ProgressName = payload.Progress?.ProgressName,
                CreatedAt = DateTime.Now,
                UpdatedAt = DateTime.Now
            };
            _db.Progresses.Add(progress);
            await _db.SaveChangesAsync(ct);
        }

        var batchId = DateTime.Now.ToString("yyMMddHHmmss") + "_" + Guid.NewGuid().ToString("N")[..6];
        var now = DateTime.Now;
        _db.Batches.Add(new ArchiveDetectBatch
        {
            BatchId = batchId,
            UserPrompt = TextSanitizer.Clean(payload.Criteria, 100_000) ?? "",
            SourceKind = "batch",
            Stage = stage,
            TotalFiles = payload.Items.Count,
            DoneFiles = 0,
            Status = "running",
            ProgressId = progress.Id,
            CreatedAt = now,
            UpdatedAt = now
        });
        for (int i = 0; i < payload.Items.Count; i++)
        {
            var it = payload.Items[i];
            _db.Files.Add(new ArchiveDetectFile
            {
                BatchId = batchId,
                Idx = i,
                ProgressId = progress.Id,
                FileId = TextSanitizer.Clean(it.FileId, TextSanitizer.SmallTextLimit),
                Filename = TextSanitizer.Clean(it.Filename, TextSanitizer.SmallTextLimit),
                SourceUrl = it.Url,
                Version = 1,
                Deleted = false,
                Status = "pending",
                RetryCount = 0,
                CreatedAt = now,
                UpdatedAt = now
            });
        }
        await _db.SaveChangesAsync(ct);

        return new SubmitBatchResult(batchId, progress.Id, payload.Items.Count);
    }

    // ── 业务批次轮询:返回 batch + files 完整结果 ──
    public async Task<object?> GetBusinessBatchAsync(string batchId, CancellationToken ct = default)
    {
        var b = await _db.Batches.FirstOrDefaultAsync(x => x.BatchId == batchId, ct);
        if (b is null) return null;

        var files = await _db.Files.Where(f => f.BatchId == batchId).OrderBy(f => f.Idx).ToListAsync(ct);
        var fileDtos = files.Select(f => new
        {
            id = f.Id,
            idx = f.Idx,
            file_id = f.FileId,
            filename = f.Filename,
            status = f.Status,
            verdict = f.Verdict,
            match_score = f.MatchScore,
            doc_category = f.DocCategory,
            reason = f.Reason,
            key_points = string.IsNullOrEmpty(f.KeyPoints)
                ? new List<string>()
                : JsonSerializer.Deserialize<List<string>>(f.KeyPoints) ?? new List<string>(),
            char_count = f.CharCount,
            page_count = f.PageCount,
            elapsed_sec = f.ElapsedSec,
            error_msg = f.ErrorMsg
        });

        return new
        {
            batch_id = b.BatchId,
            criteria = b.UserPrompt,
            source_kind = b.SourceKind,
            stage = b.Stage,
            total_files = b.TotalFiles,
            done_files = b.DoneFiles,
            status = b.Status,
            overall_verdict = b.OverallVerdict,
            overall_score = b.OverallScore,
            overall_reason = b.OverallReason,
            files = fileDtos
        };
    }

    // ── 后台批次列表(join client/progress) ──
    public async Task<object> ListAdminBatchesAsync(int limit, int offset, CancellationToken ct = default)
    {
        limit = limit <= 0 ? 100 : Math.Min(limit, 500);
        var q = from b in _db.Batches
                join p in _db.Progresses on b.ProgressId equals p.Id into pj
                from p in pj.DefaultIfEmpty()
                join c in _db.Clients on (p == null ? (int?)null : p.ClientId) equals c.Id into cj
                from c in cj.DefaultIfEmpty()
                orderby b.CreatedAt descending
                select new { b, p, c };

        var total = await _db.Batches.CountAsync(ct);
        var rows = await q.Skip(offset).Take(limit).ToListAsync(ct);
        var items = rows.Select(r => new
        {
            batch_id = r.b.BatchId,
            source_kind = r.b.SourceKind,
            status = r.b.Status,
            total_files = r.b.TotalFiles,
            done_files = r.b.DoneFiles,
            overall_verdict = r.b.OverallVerdict,
            overall_score = r.b.OverallScore,
            created_at = r.b.CreatedAt.ToString("yyyy-MM-dd HH:mm:ss"),
            client = r.c == null ? null : new { id = r.c.Id, client_code = r.c.ClientCode, name = r.c.Name },
            progress = r.p == null ? null : new
            {
                id = r.p.Id,
                handler = r.p.Handler,
                project_name = r.p.ProjectName,
                project_detail_name = r.p.ProjectDetailName,
                progress_name = r.p.ProgressName,
                progress_oid = r.p.ProgressOid
            }
        });
        return new { items, total };
    }

    // ── 队列统计 ──
    public async Task<object> GetQueueStatsAsync(CancellationToken ct = default)
    {
        var depth = await _db.Files.CountAsync(f =>
            f.Status == "pending" || f.Status == "leased" ||
            f.Status == "fetching" || f.Status == "ocr" || f.Status == "llm", ct);
        var running = await _db.Batches.CountAsync(b => b.Status == "running", ct);
        return new { queue_depth = depth, in_flight_batches = running };
    }

    public async Task<int> GetQueueDepthAsync(CancellationToken ct = default) =>
        await _db.Files.CountAsync(f =>
            f.Status == "pending" || f.Status == "leased" ||
            f.Status == "fetching" || f.Status == "ocr" || f.Status == "llm", ct);
}
