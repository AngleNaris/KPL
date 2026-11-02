using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

/* 字幕导入：只读现成的 srt、lrc、ass 文件，不生成文字、不做自动对齐。
   ASS 的卡拉 OK 标签（{\kN}、{\kfN}、{\koN}，单位 10 毫秒）和 LRC 的 <mm:ss.xx> 给出逐字或逐段的时间；
   每段时间落在所属字幕条目之内，按顺序、不重叠。 */
internal static class Subtitle
{
    internal sealed class Part
    {
        internal long Start, End;
        internal string Text;
    }

    internal sealed class Cue
    {
        internal long Start, End;
        internal string Text;
        internal readonly List<Part> Words = new List<Part>();
    }

    private const int MaxBytes = 8 * 1024 * 1024, MaxCues = 20000;
    private static readonly Regex AssTag = new Regex(@"\{[^}]*\}", RegexOptions.CultureInvariant);
    private static readonly Regex AssKaraoke = new Regex(@"\\(kf|ko|k)\s*(\d+)", RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);
    private static readonly Regex LrcStamp = new Regex(@"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]", RegexOptions.CultureInvariant);
    private static readonly Regex LrcMeta = new Regex(@"\[[A-Za-z][^\]\r\n]*\]", RegexOptions.CultureInvariant);
    private static readonly Regex LrcWord = new Regex(@"<(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)>", RegexOptions.CultureInvariant);
    private static readonly Regex SrtStamp = new Regex(@"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", RegexOptions.CultureInvariant);

    /* 读文件并按扩展名解析；无法解析时抛出带原因的 InvalidDataException。 */
    internal static List<Cue> Read(string path)
    {
        string text = Decode(path);
        string ext = Path.GetExtension(path).ToLowerInvariant();
        List<Cue> cues = ext == ".ass" || ext == ".ssa" ? ParseAss(text) : ext == ".srt" ? ParseSrt(text)
            : ext == ".lrc" ? ParseLrc(text) : null;
        if (cues == null) throw new InvalidDataException("只支持 srt、lrc、ass 字幕文件。");
        if (cues.Count == 0) throw new InvalidDataException("字幕里没有找到带时间的文字。");
        if (cues.Count > MaxCues) throw new InvalidDataException("字幕条目过多，请先在制作字幕的工具里精简。");
        cues.Sort((a, b) => a.Start.CompareTo(b.Start));
        return cues;
    }

    /* 语言标签只做粗分（按文件里是否出现汉字/假名/韩文），页面上仍可改；看不出就留空。 */
    internal static string LanguageOf(string extension)
    {
        return extension == ".lrc" ? "zh" : "";
    }

    /* 交给页面的是纯数据：条目与逐段时间都是整数毫秒，文字已清理过样式标签。 */
    internal static List<object> ToJson(IEnumerable<Cue> cues)
    {
        var list = new List<object>();
        foreach (Cue cue in cues)
        {
            var words = new List<object>();
            foreach (Part word in cue.Words)
                words.Add(new Dictionary<string, object> { { "start_us", word.Start * 1000 }, { "end_us", word.End * 1000 }, { "text", word.Text } });
            list.Add(new Dictionary<string, object>
            {
                { "start_us", cue.Start * 1000 }, { "end_us", cue.End * 1000 }, { "text", cue.Text }, { "words", words }
            });
        }
        return list;
    }

    /* UTF-16（有 BOM）、UTF-8（含 BOM）与 GB18030；其他编码按 GB18030 尽力读取。 */
    internal static string Decode(string path)
    {
        byte[] raw = File.ReadAllBytes(path);
        if (raw.Length > MaxBytes) throw new InvalidDataException("字幕文件过大。");
        if (raw.Length >= 2 && ((raw[0] == 0xFF && raw[1] == 0xFE) || (raw[0] == 0xFE && raw[1] == 0xFF)))
            return new UnicodeEncoding(raw[0] == 0xFE, false, true).GetString(raw, 2, raw.Length - 2);
        try { return new UTF8Encoding(false, true).GetString(raw).TrimStart('\uFEFF'); }
        catch (DecoderFallbackException)
        {
            return Encoding.GetEncoding(54936, EncoderFallback.ReplacementFallback, DecoderFallback.ReplacementFallback).GetString(raw);
        }
    }

    /* mm:ss、hh:mm:ss 或多级写法，逗号与句点都当小数点；无法解析返回 -1。 */
    private static long Clock(string value)
    {
        string[] parts = value.Trim().Replace(',', '.').Split(':');
        double total = 0;
        for (int i = 0; i < parts.Length; i++)
        {
            double part;
            if (!Double.TryParse(parts[parts.Length - 1 - i], NumberStyles.Float, CultureInfo.InvariantCulture, out part)) return -1;
            total += part * Math.Pow(60, i);
        }
        return (long)Math.Round(total * 1000);
    }

    private static List<Cue> ParseSrt(string text)
    {
        var cues = new List<Cue>();
        foreach (string block in Regex.Split(text.Replace("\r\n", "\n"), @"\n\s*\n"))
        {
            string[] lines = block.Trim().Split('\n');
            for (int i = 0; i < lines.Length; i++)
            {
                Match m = SrtStamp.Match(lines[i]);
                if (!m.Success) continue;
                string body = Regex.Replace(String.Join("\n", lines, i + 1, lines.Length - i - 1).Trim(), @"<[^>]*>", "");
                cues.Add(Build(Clock(m.Groups[1].Value), Clock(m.Groups[2].Value), body));
                break;
            }
        }
        return cues.Where(cue => cue != null).ToList();
    }

    private static List<Cue> ParseLrc(string text)
    {
        long offset = 0;
        Match shift = Regex.Match(text, @"\[offset:([+-]?\d+)\]", RegexOptions.IgnoreCase);
        if (shift.Success) offset = Convert.ToInt64(shift.Groups[1].Value, CultureInfo.InvariantCulture);
        var cues = new List<Cue>();
        foreach (string raw in text.Replace("\r\n", "\n").Split('\n'))
        {
            // [ti:][ar:][offset:] 是元数据，不是歌词
            string line = LrcMeta.Replace(raw, "");
            var stamps = LrcStamp.Matches(line).Cast<Match>()
                .Select(m => Clock(m.Groups[1].Value + ":" + m.Groups[2].Value)).Where(at => at >= 0).ToList();
            if (stamps.Count == 0) continue;
            string body = LrcStamp.Replace(line, "").Trim();
            // 一行可以带多个句首时间；逐字标记 <mm:ss.xx> 是该段开始的绝对时间
            var words = LrcWord.Matches(body).Cast<Match>()
                .Select(m => new Part { Start = Clock(m.Groups[1].Value + ":" + m.Groups[2].Value) + offset, Text = null }).ToList();
            string plain = LrcWord.Replace(body, "").Replace("\n", " ").Trim();
            if (plain.Length == 0) continue;
            foreach (long stamp in stamps)
            {
                long start = stamp + offset;
                var cue = Build(start, start + 1, plain);
                if (words.Count > 0)
                {
                    // <mm:ss.xx> 标在它后面那段文字之前。2 个捕获组让 Split 每段产出 3 个元素，
                    // 所以第 k 个标记之后的文字在 3(k+1) 处；最后一个标记没有收尾时间，
                    // 按 300 毫秒估计，并把整句撑到那个位置（后续会与下一句对齐）。
                    string[] pieces = LrcWord.Split(body);
                    long until = Math.Max(start + 1, words[words.Count - 1].Start + 300);
                    cue.End = until;
                    for (int i = 0; i < words.Count; i++)
                    {
                        string piece = pieces[3 * (i + 1)].Replace("\n", " ").Trim();
                        if (piece.Length == 0) continue;
                        long from = words[i].Start, to = i + 1 < words.Count ? words[i + 1].Start : until;
                        cue.Words.Add(new Part { Start = from, End = Math.Max(from + 1, to), Text = piece });
                    }
                    cue.Words.Sort((a, b) => a.Start.CompareTo(b.Start));
                }
                cues.Add(cue);
            }
        }
        return cues;
    }

    private static List<Cue> ParseAss(string text)
    {
        var cues = new List<Cue>();
        string[] fields = null; bool events = false;
        foreach (string raw in text.Replace("\r\n", "\n").Split('\n'))
        {
            string line = raw.Trim();
            if (line.StartsWith("[", StringComparison.Ordinal)) { events = line.Equals("[Events]", StringComparison.OrdinalIgnoreCase); continue; }
            if (!events) continue;
            if (line.StartsWith("Format:", StringComparison.OrdinalIgnoreCase))
            {
                fields = line.Substring(7).Split(','); for (int i = 0; i < fields.Length; i++) fields[i] = fields[i].Trim().ToLowerInvariant();
                continue;
            }
            if (!line.StartsWith("Dialogue:", StringComparison.OrdinalIgnoreCase) || fields == null) continue;
            string[] values = line.Substring(9).TrimStart().Split(new[] { ',' }, fields.Length);
            Func<string, string> Get = name =>
            {
                int at = Array.IndexOf(fields, name);
                return at >= 0 && at < values.Length ? values[at].Trim() : null;
            };
            long start = Clock(Get("start") ?? ""), end = Clock(Get("end") ?? "");
            string body = Get("text");
            if (start < 0 || end <= start || body == null) continue;
            // 花括号里是样式与效果，不显示；卡拉 OK 标签另外给出逐段时长（单位 10 毫秒）
            string plain = AssTag.Replace(body, "").Replace("\\N", "\n").Replace("\\n", "\n").Replace("\\h", " ").Replace("\n", " ").Trim();
            if (plain.Length == 0) continue;
            var cue = Build(start, end, plain);
            if (AssKaraoke.IsMatch(body)) FillKaraoke(cue, body);
            cues.Add(cue);
        }
        return cues;
    }

    /* 逐段切分：\k 标签的时长管它后面那段文字；只有标签、没有逐段文字时，把整句文字按时长比例分给各段。
       各段从条目开始时间依次排开，与条目长度不一致时按比例缩放到条目末尾。 */
    private static void FillKaraoke(Cue cue, string body)
    {
        var pieces = new List<KeyValuePair<long, string>>();   // 时长（毫秒）、文字
        MatchCollection marks = AssKaraoke.Matches(body);
        for (int i = 0; i < marks.Count; i++)
        {
            int from = marks[i].Index + marks[i].Length;
            int to = i + 1 < marks.Count ? marks[i + 1].Index : body.Length;
            pieces.Add(new KeyValuePair<long, string>(
                Convert.ToInt64(marks[i].Groups[2].Value, CultureInfo.InvariantCulture) * 10,
                Clean(body.Substring(from, to - from))));
        }
        long sum = pieces.Sum(piece => piece.Key);
        if (sum <= 0) return;
        // 没有逐段文字时，整句文字按各段时长比例切开
        if (pieces.All(piece => piece.Value.Length == 0))
        {
            string text = Clean(body);
            int cut = 0;
            for (int i = 0; i < pieces.Count; i++)
            {
                int size = i == pieces.Count - 1 ? text.Length - cut : Math.Max(0, (int)Math.Round((double)text.Length * pieces[i].Key / sum));
                size = Math.Min(size, text.Length - cut);
                pieces[i] = new KeyValuePair<long, string>(pieces[i].Key, text.Substring(cut, size));
                cut += size;
            }
        }
        long cursor = cue.Start, span = cue.End - cue.Start;
        foreach (var piece in pieces)
        {
            if (piece.Value.Length == 0) continue;
            long stop = Math.Min(cue.End, cursor + (long)Math.Round((double)piece.Key / sum * span));
            if (stop <= cursor) break;
            cue.Words.Add(new Part { Start = cursor, End = stop, Text = piece.Value });
            cursor = stop;
        }
    }

    private static string Clean(string value)
    {
        return AssTag.Replace(value, "").Replace("{", "").Replace("}", "")
            .Replace("\\N", " ").Replace("\\n", " ").Replace("\\h", " ").Replace("\n", " ").Trim();
    }

    private static Cue Build(long start, long end, string text)
    {
        if (start < 0 || end <= start || String.IsNullOrWhiteSpace(text)) return null;
        return new Cue { Start = start, End = end, Text = text.Trim() };
    }
}
