using RapidOcrNet;
using SkiaSharp;
using System.Text;

namespace DocReview.ArchiveDetect.Services;

// 复刻 Python ocr_service + text_extractor 的 OCR 能力(PoC 版:图片 + PDF)。
// 引擎单例 + 锁串行(RapidOcr 多线程推理不保证安全,与 Python 侧 _OCR_ENGINE_LOCK 一致)。
// PDF 用 PDFtoImage 渲染成位图再 OCR(对标 Python 的 pypdfium2)。
public class OcrService : IDisposable
{
    private RapidOcr? _ocr = new();
    private readonly object _lock = new();
    private bool _inited;
    private bool _disposed; // 标记是否已释放
    private readonly ILogger<OcrService> _log;

    private static readonly string[] ImageExts =
        { ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".gif" };

    public OcrService(ILogger<OcrService> log) => _log = log;

    private void EnsureInit()
    {
        lock (_lock)
        {
            // 先判断是否已释放
            if (_disposed)
                throw new ObjectDisposedException(nameof(OcrService), "OCR引擎已释放，无法执行识别操作");

            if (_inited) return;
            // 用 PP-OCRv4 中文模型(与 Python 项目同款 ch_PP-OCRv4_*,复用本地 venv 文件)。
            // 之前用的 latin 识别模型对中文基本不认,是中文识别不出来的根因。
            var v4 = Path.Combine(AppContext.BaseDirectory, "models", "v4");
            _ocr!.InitModels(
                detPath: Path.Combine(v4, "ch_PP-OCRv4_det_infer.onnx"),
                clsPath: Path.Combine(v4, "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
                recPath: Path.Combine(v4, "ch_PP-OCRv4_rec_infer.onnx"),
                keysPath: Path.Combine(v4, "ppocr_keys_v1.txt"));
            _inited = true;
            _log.LogInformation("RapidOcr 引擎已初始化(PP-OCRv4 中文),模型目录={dir}", v4);
        }
    }

    public record ExtractResult(string Text, string Source, int PageCount, int CharCount);

    public ExtractResult Extract(string filePath)
    {
        var ext = Path.GetExtension(filePath).ToLower();
        string rawText;
        int pageCount;
        string source;

        if (ext == ".pdf")
        {
            (rawText, pageCount) = ExtractPdf(filePath);
            source = "pdf_ocr";
        }
        else if (ImageExts.Contains(ext))
        {
            rawText = OcrImageFile(filePath);
            pageCount = 1;
            source = "image_ocr";
        }
        else
        {
            throw new NotSupportedException($"PoC 暂不支持的文件类型: {ext}");
        }

        var cleaned = TextSanitizer.Clean(rawText, TextSanitizer.OcrTextLimit) ?? "";
        return new ExtractResult(cleaned, source, pageCount, cleaned.Length);
    }

    private string OcrImageFile(string imgPath)
    {
        EnsureInit();
        using var bmp = SKBitmap.Decode(imgPath);
        if (bmp is null) return "";
        return OcrBitmap(bmp);
    }

    private (string text, int pages) ExtractPdf(string pdfPath)
    {
        EnsureInit();
        var bytes = File.ReadAllBytes(pdfPath);
        var sb = new StringBuilder();
        int pages = 0;
        foreach (var bmp in PDFtoImage.Conversion.ToImages(bytes, options: new(Dpi: 200)))
        {
            using (bmp)
            {
                pages++;
                var pageText = OcrBitmap(bmp);
                if (!string.IsNullOrWhiteSpace(pageText))
                {
                    if (sb.Length > 0) sb.Append("\n\n");
                    sb.Append(pageText);
                }
            }
        }
        return (sb.ToString(), pages);
    }

    private string OcrBitmap(SKBitmap bmp)
    {
        lock (_lock)
        {
            if (_disposed || _ocr is null)
                return string.Empty;

            var result = _ocr.Detect(bmp, RapidOcrOptions.Default);
            if (result?.TextBlocks == null) return "";
            var lines = new List<string>();
            foreach (var block in result.TextBlocks)
            {
                var avg = block.CharScores is { Length: > 0 } ? block.CharScores.Average() : 1f;
                if (avg > 0.3f && !string.IsNullOrWhiteSpace(block.Text))
                    lines.Add(block.Text);
            }
            return string.Join("\n", lines);
        }
    }

    // 标准IDisposable实现
    public void Dispose()
    {
        Dispose(true);
        GC.SuppressFinalize(this);
    }

    protected virtual void Dispose(bool disposing)
    {
        lock (_lock) // 锁统一，防止识别与销毁并发
        {
            if (_disposed) return;

            if (disposing)
            {
                // 仅当引擎已 InitModels 才释放:未初始化时 RapidOcr.Dispose 内部
                // TextClassifier.Dispose 会空引用崩溃(det/cls/rec session 都是 null)。
                if (_inited)
                {
                    try { _ocr?.Dispose(); }
                    catch (Exception ex) { _log.LogWarning("释放 OCR 引擎异常(忽略): {msg}", ex.Message); }
                }
                _ocr = null; // 置空，彻底杜绝后续访问
            }

            _disposed = true;
            _log.LogDebug("RapidOcr 引擎资源已释放");
        }
    }
}