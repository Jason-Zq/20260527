using System.Text;

namespace DocReview.ArchiveDetect.Services;

// 复刻 Python backend/db/archive_detect_crud.py 的 _clean_text:
// 去除 NUL(0x00)/C0 控制字符(保留 \t \n \r)和 DEL(0x7F),可选长度截断。
// 这是防止 PostgreSQL "invalid byte sequence 0x00" 报错的关键。
public static class TextSanitizer
{
    public const int OcrTextLimit = 1_000_000;
    public const int ReasonLimit = 50_000;
    public const int SmallTextLimit = 4_096;
    public const int ErrorMsgLimit = 10_000;

    public static string? Clean(string? s, int? limit = null)
    {
        if (s is null) return null;
        var sb = new StringBuilder(s.Length);
        foreach (var ch in s)
        {
            // 保留 \t(0x09) \n(0x0a) \r(0x0d),去掉其它 C0 控制符和 DEL
            if (ch < 0x20 && ch != '\t' && ch != '\n' && ch != '\r') continue;
            if (ch == 0x7F) continue;
            sb.Append(ch);
        }
        var cleaned = sb.ToString();
        if (limit is int lim && cleaned.Length > lim)
            cleaned = cleaned[..lim] + $"\n...[已截断,原长 {cleaned.Length} 字]";
        return cleaned;
    }
}
