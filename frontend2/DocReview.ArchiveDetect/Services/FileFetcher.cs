using System.Text.Json;

namespace DocReview.ArchiveDetect.Services;

// 复刻 Python backend/file_fetcher.py:下载 URL 到临时文件 + 大小/扩展名校验 +
// OSS 签名地址过期(401/403/404)时用 file_id 调业务方 getFileDownloadUrl 刷新后重试。
public class FileFetcher
{
    public const long MaxDownloadBytes = 50L * 1024 * 1024; // 50MB
    private static readonly string[] SupportedExts =
        { ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif",
          ".docx", ".doc", ".xls", ".xlsx", ".pptx" };

    private readonly HttpClient _http;
    private readonly IConfiguration _cfg;
    private readonly string _tempDir;
    private readonly ILogger<FileFetcher> _log;

    public FileFetcher(HttpClient http, IConfiguration cfg, ILogger<FileFetcher> log)
    {
        _http = http;
        _cfg = cfg;
        _log = log;
        _tempDir = Path.Combine(AppContext.BaseDirectory, "temp_fetched");
        Directory.CreateDirectory(_tempDir);
    }

    public static bool IsSupported(string filename) =>
        SupportedExts.Contains(Path.GetExtension(filename).ToLower());

    public record FetchResult(string LocalPath, string Filename, string? MimeType, bool Refreshed);

    // ── 带刷新的下载:先直连,遇 401/403/404 且有 file_id 则刷新 URL 重试一次 ──
    public async Task<FetchResult> FetchWithRefreshAsync(string url, string? fileId, string? preferredName, CancellationToken ct)
    {
        try
        {
            var r = await FetchAsync(url, preferredName, ct);
            return r with { Refreshed = false };
        }
        catch (Exception ex) when (IsExpiredUrlError(ex) && !string.IsNullOrEmpty(fileId))
        {
            _log.LogWarning("下载 URL 失效({msg}),用 file_id={fid} 刷新地址重试", ex.Message, fileId);
            var newUrl = await RefreshDownloadUrlAsync(fileId!, null, ct);
            var r = await FetchAsync(newUrl, preferredName, ct);
            return r with { Refreshed = true };
        }
    }

    // 判断是否为签名/地址失效(401/403/404)
    private static bool IsExpiredUrlError(Exception ex)
    {
        if (ex is HttpRequestException hre && hre.StatusCode is System.Net.HttpStatusCode code)
            return code is System.Net.HttpStatusCode.Unauthorized
                or System.Net.HttpStatusCode.Forbidden
                or System.Net.HttpStatusCode.NotFound;
        return false;
    }

    // ── 用 file_id 调业务方 getFileDownloadUrl 换新的 OSS 临时地址 ──
    public async Task<string> RefreshDownloadUrlAsync(string fileId, string? type, CancellationToken ct)
    {
        var sec = _cfg.GetSection("FileUrlService");
        if (!sec.GetValue("Enabled", false))
            throw new InvalidOperationException("未启用 FileUrlService");
        var baseUrl = sec.GetValue<string>("BaseUrl") ?? throw new InvalidOperationException("FileUrlService.BaseUrl 未配置");

        // 业务方接口要求带登录人/操作人/来源标识,否则返回"没有登陆人不可查看或者下载文件"
        var query = new Dictionary<string, string?>
        {
            ["file_id"] = fileId,
            ["type"] = type ?? sec.GetValue<string>("DefaultType") ?? "preview",
            ["usr_login"] = sec.GetValue<string>("UsrLogin") ?? "Jason邹启",
            ["operation_user"] = sec.GetValue<string>("OperationUser") ?? "Jason邹启",
            ["url"] = sec.GetValue<string>("Url") ?? "batch",
        };
        var qs = string.Join("&", query.Select(kv =>
            $"{Uri.EscapeDataString(kv.Key)}={Uri.EscapeDataString(kv.Value ?? "")}"));
        var fullUrl = $"{baseUrl}?{qs}";

        using var resp = await _http.GetAsync(fullUrl, ct);
        resp.EnsureSuccessStatusCode();
        var text = await resp.Content.ReadAsStringAsync(ct);

        using var doc = JsonDocument.Parse(text);
        var root = doc.RootElement;
        int ret = root.TryGetProperty("ret", out var r) && r.TryGetInt32(out var ri) ? ri : -1;
        int code = root.TryGetProperty("code", out var c) && c.TryGetInt32(out var ci) ? ci : -1;
        if (ret != 200 || code != 0)
        {
            var msg = root.TryGetProperty("msg", out var m) ? m.GetString() : text;
            throw new InvalidOperationException($"刷新下载地址失败: {msg}");
        }
        if (!root.TryGetProperty("data", out var data) ||
            !data.TryGetProperty("file_url", out var fu) || fu.GetString() is not string fileUrl ||
            string.IsNullOrEmpty(fileUrl))
            throw new InvalidOperationException("刷新下载地址响应缺少 data.file_url");

        _log.LogInformation("file_id={fid} 刷新到新地址成功", fileId);
        return fileUrl;
    }

    // ── 基础下载 ──
    public async Task<FetchResult> FetchAsync(string url, string? preferredName, CancellationToken ct)
    {
        using var resp = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct);
        resp.EnsureSuccessStatusCode();

        var len = resp.Content.Headers.ContentLength;
        if (len is > MaxDownloadBytes)
            throw new InvalidOperationException("文件超过 50MB 上限");

        var filename = preferredName;
        if (string.IsNullOrWhiteSpace(filename))
        {
            filename = resp.Content.Headers.ContentDisposition?.FileNameStar
                       ?? resp.Content.Headers.ContentDisposition?.FileName
                       ?? Path.GetFileName(new Uri(url).AbsolutePath);
            filename = filename?.Trim('"');
        }
        if (string.IsNullOrWhiteSpace(filename)) filename = "download.bin";

        var mime = resp.Content.Headers.ContentType?.MediaType;
        var localPath = Path.Combine(_tempDir, $"{Guid.NewGuid():N}_{Path.GetFileName(filename)}");

        await using (var fs = File.Create(localPath))
        await using (var src = await resp.Content.ReadAsStreamAsync(ct))
        {
            var buffer = new byte[81920];
            long total = 0;
            int read;
            while ((read = await src.ReadAsync(buffer, ct)) > 0)
            {
                total += read;
                if (total > MaxDownloadBytes)
                {
                    fs.Close();
                    File.Delete(localPath);
                    throw new InvalidOperationException("文件超过 50MB 上限");
                }
                await fs.WriteAsync(buffer.AsMemory(0, read), ct);
            }
        }
        return new FetchResult(localPath, filename, mime, false);
    }

    public void Cleanup(string? localPath)
    {
        if (string.IsNullOrEmpty(localPath)) return;
        try { if (File.Exists(localPath)) File.Delete(localPath); }
        catch (Exception ex) { _log.LogWarning("清理临时文件失败(忽略): {msg}", ex.Message); }
    }
}
