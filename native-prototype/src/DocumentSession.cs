using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

internal sealed class DocumentSession
{
    internal const int MaxBytes = 5 * 1024 * 1024;
    private readonly string path;
    private Dictionary<string, object> model;
    private string baseline;
    private readonly JavaScriptSerializer serializer = JsonTools.Serializer();
    private static readonly string[] Fields = { "number", "duration_seconds", "thumbnail", "content", "description", "shot_scale", "camera_motion", "prompt", "video_prompt", "mark" };
    private static readonly string[] AssetFields = { "id", "name", "path" };
    private static readonly string[] RootFields = { "schema", "document_id", "version", "status", "confirmation", "revision", "title", "summary", "global_settings", "layout_aspect", "assets", "shots" };
    // v3 只比 v2 多一个 timeline；加入媒体后保存时由 v2 原地升级
    internal const string SchemaV2 = "kpl-user-spec-v2", SchemaV3 = "kpl-user-spec-v3";
    // 音频轨只能关声音；视频轨可分别关声音（muted）与画面（hidden）；字幕轨只能关画面
    private static readonly string[] TrackFields = { "id", "name", "muted", "locked", "gain", "clips" };
    private static readonly string[] VideoTrackFields = { "id", "name", "muted", "hidden", "locked", "gain", "clips" };
    // takes：同一镜头的其他候选版本，顶层字段是当前使用的版本；切换就是两者互换
    private static readonly string[] MainClipFields = { "id", "shot_id", "source", "in_us", "out_us", "audio", "takes" };
    private static readonly string[] TakeFields = { "id", "source", "in_us", "out_us", "audio" };
    private static readonly string[] VideoClipFields = { "id", "start_us", "source", "in_us", "out_us", "audio" };
    private static readonly string[] AudioClipFields = { "id", "start_us", "source", "in_us", "out_us" };
    private static readonly string[] MarkerFields = { "id", "start_us", "end_us", "text", "author" };
    private static readonly string[] SubtitleTrackFields = { "id", "name", "language", "hidden", "locked", "cues" };
    private static readonly string[] CueFields = { "id", "start_us", "end_us", "text", "words" };
    private static readonly string[] WordFields = { "start_us", "end_us", "text" };
    private const long MaxUs = 86400000000L;
    private static readonly HashSet<string> Marks = new HashSet<string>(StringComparer.Ordinal) { "", "done", "revise", "wip", "question", "hold" };

    internal DocumentSession(string file)
    {
        path = Path.GetFullPath(file);
        if (!String.Equals(Path.GetExtension(path), ".kpldoc", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("只能打开 .kpldoc 规格文件。");
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidDataException("暂不打开链接文件，请选择原始规格文件。");
        byte[] bytes = ReadBounded(path);
        model = Parse(bytes);
        Validate(model, false);  // 打开时接受旧文件的多位小数时长，由界面规整；保存时严格
        baseline = Hash(bytes);
    }

    internal string FilePath { get { return path; } }
    internal string InitialJson { get { return serializer.Serialize(model); } }
    internal string Baseline { get { return baseline; } }

    internal static Dictionary<string, object> Parse(byte[] bytes)
    {
        if (bytes.Length > MaxBytes) throw new InvalidDataException("规格文件过大。");
        string text = new UTF8Encoding(false, true).GetString(bytes).TrimStart('\uFEFF');
        JsonTools.RejectDuplicateKeys(text);
        var result = JsonTools.Serializer().DeserializeObject(text) as Dictionary<string, object>;
        if (result == null) throw new InvalidDataException("规格必须是 JSON 对象。");
        return result;
    }

    private static string StringField(Dictionary<string, object> obj, string key)
    {
        object value;
        if (!obj.TryGetValue(key, out value) || !(value is string)) throw new InvalidDataException("规格缺少文本字段：" + key);
        return (string)value;
    }

    private static void ExactFields(Dictionary<string, object> obj, IEnumerable<string> fields)
    {
        var allowed = new HashSet<string>(fields, StringComparer.Ordinal);
        if (obj.Count != allowed.Count) throw new InvalidDataException("规格包含未知或缺失字段，未写入。");
        foreach (string name in obj.Keys) if (!allowed.Contains(name)) throw new InvalidDataException("规格包含未知字段：" + name);
    }

    internal static void Validate(Dictionary<string, object> obj) { Validate(obj, true); }

    // strictDuration：时长只允许精确到 0.1 秒（保存与新文件）；打开旧文件时放宽为任意正小数
    internal static void Validate(Dictionary<string, object> obj, bool strictDuration)
    {
        object schemaValue;
        bool v3 = obj.TryGetValue("schema", out schemaValue) && SchemaV3.Equals(schemaValue);
        var rootFields = new List<string>(RootFields);
        if (v3) rootFields.Add("timeline");
        ExactFields(obj, rootFields);
        string schema = StringField(obj, "schema");
        if (!(schema == SchemaV2 || schema == SchemaV3) || StringField(obj, "document_id").Trim().Length == 0)
            throw new InvalidDataException("规格类型或文档标识无效。");
        if (!Regex.IsMatch(StringField(obj, "version"), @"^v(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2})$"))
            throw new InvalidDataException("版本无效。");
        string status = StringField(obj, "status"), confirmation = StringField(obj, "confirmation");
        if (!(status == "draft" || status == "review" || status == "final") || !(confirmation == "confirmed" || confirmation == "unconfirmed"))
            throw new InvalidDataException("规格状态无效。");
        object rev;
        if (!obj.TryGetValue("revision", out rev) || !(rev is int) || (int)rev < 1 || (int)rev == Int32.MaxValue)
            throw new InvalidDataException("修订号无效。");
        StringField(obj, "title"); StringField(obj, "summary"); StringField(obj, "global_settings");
        if (!ValidAspect(StringField(obj, "layout_aspect"))) throw new InvalidDataException("预览画幅应为空或“宽:高”。");
        object assets;
        if (!obj.TryGetValue("assets", out assets) || !(assets is object[])) throw new InvalidDataException("资产列表无效。");
        var assetIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (object item in (object[])assets)
        {
            var asset = item as Dictionary<string, object>;
            if (asset == null) throw new InvalidDataException("资产内容无效。");
            ExactFields(asset, AssetFields);
            string assetId = StringField(asset, "id");
            if (!Regex.IsMatch(assetId, @"^[A-Za-z0-9][A-Za-z0-9_-]*$") || !assetIds.Add(assetId))
                throw new InvalidDataException("资产标识无效或重复。");
            if (StringField(asset, "name").Trim().Length == 0) throw new InvalidDataException("资产名称不能为空。");
            if (!SafeThumbnail(StringField(asset, "path"))) throw new InvalidDataException("资产路径必须位于参考资产目录内。");
        }
        object shots;
        if (!obj.TryGetValue("shots", out shots) || !(shots is object[])) throw new InvalidDataException("镜头列表无效。");
        var ids = new HashSet<string>(StringComparer.Ordinal);
        var numbers = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var allowedShot = new List<string>(Fields); allowedShot.Add("id"); allowedShot.Add("layout_camera");
        foreach (object item in (object[])shots)
        {
            var shot = item as Dictionary<string, object>;
            if (shot == null) throw new InvalidDataException("镜头内容无效。");
            ExactFields(shot, allowedShot);
            string id = StringField(shot, "id");
            if (id.Length == 0 || !ids.Add(id)) throw new InvalidDataException("镜头标识缺失或重复。");
            foreach (string field in Fields) StringField(shot, field);
            string number = StringField(shot, "number").Trim();
            if (number.Length == 0 || !numbers.Add(number)) throw new InvalidDataException("镜号缺失或重复。");
            string duration = StringField(shot, "duration_seconds").Trim();
            decimal seconds;
            if (duration.Length > 0 && (!Regex.IsMatch(duration, strictDuration ? @"^(?:\d+(?:\.\d)?|\.\d)$" : @"^(?:\d+(?:\.\d+)?|\.\d+)$") ||
                !Decimal.TryParse(duration, NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out seconds) || seconds <= 0))
                throw new InvalidDataException("镜头时长应是大于零的秒数，最多精确到 0.1 秒。");
            string thumb = StringField(shot, "thumbnail");
            if (thumb.Length > 0 && !SafeThumbnail(thumb)) throw new InvalidDataException("缩略图引用无效。");
            if (!Marks.Contains(StringField(shot, "mark"))) throw new InvalidDataException("镜头标记无效。");
            if (!ValidCamera(shot["layout_camera"])) throw new InvalidDataException("预览运镜须为空，或 static/linear 加两个有效裁切框。");
        }
        if (v3) ValidateTimeline(obj["timeline"], ids);
    }

    // 时间为整数微秒，片段采用素材的 [in_us, out_us)；video[0] 是主轨，序号越大越靠上并覆盖下层
    private static void ValidateTimeline(object value, HashSet<string> shotIds)
    {
        var timeline = value as Dictionary<string, object>;
        if (timeline == null) throw new InvalidDataException("时间线无效。");
        ExactFields(timeline, new[] { "video", "audio", "subtitles", "markers", "markers_locked" });
        BoolField(timeline, "markers_locked");
        var video = timeline["video"] as object[]; var audio = timeline["audio"] as object[]; var markers = timeline["markers"] as object[];
        var subtitles = timeline["subtitles"] as object[];
        if (video == null || video.Length == 0 || audio == null || markers == null || subtitles == null) throw new InvalidDataException("时间线需要主视频轨，以及轨道与标记列表。");
        var ids = new HashSet<string>(StringComparer.Ordinal); var linked = new HashSet<string>(StringComparer.Ordinal);
        for (int pass = 0; pass < 2; pass++)
        {
            object[] tracks = pass == 0 ? video : audio;
            for (int index = 0; index < tracks.Length; index++)
            {
                var track = tracks[index] as Dictionary<string, object>;
                if (track == null) throw new InvalidDataException("轨道无效。");
                ExactFields(track, pass == 0 ? VideoTrackFields : TrackFields);
                TimelineId(StringField(track, "id"), ids); StringField(track, "name");
                if (!(track["muted"] is bool)) throw new InvalidDataException("轨道静音状态无效。");
                if (pass == 0) BoolField(track, "hidden");
                BoolField(track, "locked");
                decimal gain;
                if (!Number(track["gain"], out gain) || gain < 0 || gain > 1) throw new InvalidDataException("轨道音量应在 0 到 1 之间。");
                var clips = track["clips"] as object[];
                if (clips == null) throw new InvalidDataException("片段列表无效。");
                bool main = pass == 0 && index == 0;
                var spans = new List<long[]>();
                foreach (object item in clips)
                {
                    var clip = item as Dictionary<string, object>;
                    if (clip == null) throw new InvalidDataException("片段无效。");
                    ExactFields(clip, main ? MainClipFields : pass == 0 ? VideoClipFields : AudioClipFields);
                    TimelineId(StringField(clip, "id"), ids);
                    if (!ValidSource(StringField(clip, "source"))) throw new InvalidDataException("片段素材路径无效。");
                    long from = Us(clip["in_us"]), to = Us(clip["out_us"]);
                    if (to <= from) throw new InvalidDataException("片段出点必须晚于入点。");
                    if (pass == 0 && !(clip["audio"] is bool)) throw new InvalidDataException("片段声音状态无效。");
                    if (main)
                    {
                        // 每个镜头最多关联一个视频
                        string shot = StringField(clip, "shot_id");
                        if (!shotIds.Contains(shot) || !linked.Add(shot)) throw new InvalidDataException("主轨片段须关联现有镜头，且每个镜头只关联一个视频。");
                        var takes = clip["takes"] as object[];
                        if (takes == null) throw new InvalidDataException("视频版本列表无效。");
                        foreach (object entry in takes)
                        {
                            var take = entry as Dictionary<string, object>;
                            if (take == null) throw new InvalidDataException("视频版本无效。");
                            ExactFields(take, TakeFields);
                            TimelineId(StringField(take, "id"), ids);
                            if (!ValidSource(StringField(take, "source"))) throw new InvalidDataException("视频版本的素材路径无效。");
                            if (Us(take["out_us"]) <= Us(take["in_us"])) throw new InvalidDataException("视频版本的出点必须晚于入点。");
                            if (!(take["audio"] is bool)) throw new InvalidDataException("视频版本的声音状态无效。");
                        }
                        continue;
                    }
                    long start = Us(clip["start_us"]);
                    if (start + to - from > MaxUs) throw new InvalidDataException("片段超出时间线范围。");
                    spans.Add(new[] { start, start + to - from });
                }
                spans.Sort((a, b) => a[0].CompareTo(b[0]));
                for (int i = 1; i < spans.Count; i++)
                    if (spans[i - 1][1] > spans[i][0]) throw new InvalidDataException("同一轨道的片段不能重叠。");
            }
        }
        // 字幕轨：条目是不依赖素材的文本，words 为零个或多个逐段时间（来自 ASS 卡拉 OK 标签）
        foreach (object item in subtitles)
        {
            var track = item as Dictionary<string, object>;
            if (track == null) throw new InvalidDataException("字幕轨无效。");
            ExactFields(track, SubtitleTrackFields);
            TimelineId(StringField(track, "id"), ids); StringField(track, "name"); StringField(track, "language");
            BoolField(track, "hidden"); BoolField(track, "locked");
            var cues = track["cues"] as object[];
            if (cues == null) throw new InvalidDataException("字幕条目列表无效。");
            var spans = new List<long[]>();
            foreach (object entry in cues)
            {
                var cue = entry as Dictionary<string, object>;
                if (cue == null) throw new InvalidDataException("字幕条目无效。");
                ExactFields(cue, CueFields);
                TimelineId(StringField(cue, "id"), ids);
                if (StringField(cue, "text").Length > 2000) throw new InvalidDataException("字幕文字过长。");
                long start = Us(cue["start_us"]), end = Us(cue["end_us"]);
                if (end <= start) throw new InvalidDataException("字幕的结束时间必须晚于开始时间。");
                var words = cue["words"] as object[];
                if (words == null) throw new InvalidDataException("逐字时间列表无效。");
                long last = start;
                foreach (object part in words)
                {
                    var word = part as Dictionary<string, object>;
                    if (word == null) throw new InvalidDataException("逐字时间无效。");
                    ExactFields(word, WordFields);
                    long from = Us(word["start_us"]), to = Us(word["end_us"]);
                    if (StringField(word, "text").Length == 0 || to <= from || from < last || to > end)
                        throw new InvalidDataException("逐字时间须按顺序落在本句范围内、互不重叠。");
                    last = to;
                }
                spans.Add(new[] { start, end });
            }
            spans.Sort((a, b) => a[0].CompareTo(b[0]));
            for (int i = 1; i < spans.Count; i++)
                if (spans[i - 1][1] > spans[i][0]) throw new InvalidDataException("同一条字幕轨的条目不能重叠。");
        }
        foreach (object item in markers)
        {
            var marker = item as Dictionary<string, object>;
            if (marker == null) throw new InvalidDataException("标记无效。");
            ExactFields(marker, MarkerFields);
            TimelineId(StringField(marker, "id"), ids);
            long start = Us(marker["start_us"]);
            if (marker["end_us"] != null && Us(marker["end_us"]) <= start) throw new InvalidDataException("范围标记的终点必须晚于起点。");
            StringField(marker, "text");
            string author = StringField(marker, "author");
            if (!(author == "user" || author == "agent")) throw new InvalidDataException("标记作者无效。");
        }
    }

    private static void TimelineId(string value, HashSet<string> ids)
    {
        if (!Regex.IsMatch(value, @"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$") || !ids.Add(value)) throw new InvalidDataException("时间线标识无效或重复：" + value);
    }

    private static bool BoolField(Dictionary<string, object> item, string key)
    {
        object value;
        if (!item.TryGetValue(key, out value) || !(value is bool)) throw new InvalidDataException(key + " 须为布尔值。");
        return (bool)value;
    }

    private static long Us(object value)
    {
        if (!(value is int || value is long)) throw new InvalidDataException("时间须为整数微秒。");
        long result = Convert.ToInt64(value, CultureInfo.InvariantCulture);
        if (result < 0 || result > MaxUs) throw new InvalidDataException("时间超出支持范围。");
        return result;
    }

    private static bool Number(object value, out decimal result)
    {
        result = 0;
        if (value is int) result = (int)value;
        else if (value is long) result = (long)value;
        else if (value is decimal) result = (decimal)value;
        else return false;
        return true;
    }

    // 媒体路径相对 .kpldoc 所在目录（素材在其他盘时可为绝对路径），统一用 / 分隔
    internal static bool ValidSource(string value)
    {
        return value.Length > 0 && value.Trim() == value && !Regex.IsMatch(value, @"[\\\x00-\x1f]");
    }

    // 预览画幅：空（按 16:9 预览）或“宽:高”，两边都是正数。
    private static bool ValidAspect(string value)
    {
        if (value.Length == 0) return true;
        Match m = Regex.Match(value, @"^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$");
        return m.Success && Decimal.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) > 0 &&
            Decimal.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture) > 0;
    }

    // 预览运镜与 layout 预演清单的 camera 相同：null，或 static/linear 加两个 [左, 上, 右, 下] 归一化裁切框。
    private static bool ValidCamera(object value)
    {
        if (value == null) return true;
        var camera = value as Dictionary<string, object>;
        if (camera == null || camera.Count != 3 || !camera.ContainsKey("type") || !camera.ContainsKey("start_crop") || !camera.ContainsKey("end_crop")) return false;
        string type = camera["type"] as string;
        decimal[] start = Crop(camera["start_crop"]), end = Crop(camera["end_crop"]);
        if (start == null || end == null || !(type == "static" || type == "linear")) return false;
        if (type == "linear") return true;
        for (int i = 0; i < 4; i++) if (start[i] != end[i]) return false;
        return true;
    }

    private static decimal[] Crop(object value)
    {
        var items = value as object[];
        if (items == null || items.Length != 4) return null;
        var box = new decimal[4];
        for (int i = 0; i < 4; i++)
        {
            if (items[i] is int) box[i] = (int)items[i];
            else if (items[i] is decimal) box[i] = (decimal)items[i];
            else return null;
            if (box[i] < 0 || box[i] > 1) return null;
        }
        return box[0] < box[2] && box[1] < box[3] ? box : null;
    }

    internal static bool SafeThumbnail(string s)
    {
        const string prefix = "../05_参考资产/";
        if (!s.StartsWith(prefix, StringComparison.Ordinal) || Regex.IsMatch(s, @"[\\:%?#\x00-\x1f]")) return false;
        string[] parts = s.Substring(prefix.Length).Split('/');
        foreach (string p in parts) if (p.Length == 0 || p == "." || p == "..") return false;
        return Regex.IsMatch(s, @"\.(png|jpe?g|webp|gif)$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    }

    internal static string Hash(byte[] bytes)
    {
        using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
    }

    private static byte[] ReadBounded(string path)
    {
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            if (stream.Length > MaxBytes) throw new InvalidDataException("规格文件过大。");
            var result = new byte[(int)stream.Length]; int offset = 0;
            while (offset < result.Length)
            {
                int count = stream.Read(result, offset, result.Length - offset);
                if (count == 0) throw new IOException("读取期间文件发生变化。");
                offset += count;
            }
            if (stream.ReadByte() != -1) throw new IOException("读取期间文件发生变化。");
            return result;
        }
    }

    /* 外部程序（Agent）改了文件时重新读入：内容与当前基准相同（包括本程序刚保存的）返回 null；
       读不到抛 IOException（对方可能还在写），不符合格式抛 InvalidDataException，两种情况都保留原基准，保存仍按冲突拦截。 */
    internal Dictionary<string, object> ReloadIfChanged()
    {
        byte[] bytes = ReadBounded(path);
        string hash = Hash(bytes);
        if (hash == baseline) return null;
        var next = Parse(bytes);
        Validate(next, false);
        model = next; baseline = hash;
        return model;
    }

    internal string Save(Dictionary<string, object> next, Action<string> testHook)
    {
        Validate(next);
        if ((string)model["status"] == "final" || (string)model["confirmation"] == "confirmed") return "locked";
        foreach (string key in new[] { "document_id", "version", "status", "confirmation" })
            if (!Object.Equals(model[key], next[key])) return "identity_changed";
        // 规格类型只允许不变，或在加入媒体后由 v2 升级为 v3
        if (!Object.Equals(model["schema"], next["schema"]) && !(SchemaV2.Equals(model["schema"]) && SchemaV3.Equals(next["schema"])))
            return "identity_changed";
        if ((int)next["revision"] != (int)model["revision"] + 1) return "revision_changed";
        byte[] payload = new UTF8Encoding(false).GetBytes(serializer.Serialize(next) + "\n");
        if (payload.Length > MaxBytes) return "too_large";
        string mutexName = "Local\\KPLDocument-" + Hash(Encoding.UTF8.GetBytes(path.ToUpperInvariant()));
        bool acquired = false, replaceAttempted = false;
        string temp = Path.Combine(Path.GetDirectoryName(path), ".kpl-save-" + Guid.NewGuid().ToString("N") + ".tmp");
        using (var mutex = new Mutex(false, mutexName))
        {
            try
            {
                try { acquired = mutex.WaitOne(TimeSpan.FromSeconds(5)); }
                catch (AbandonedMutexException) { acquired = true; }
                if (!acquired) return "busy";
                FileAttributes attr = File.GetAttributes(path);
                if ((attr & FileAttributes.ReparsePoint) != 0) return "link_changed";
                if ((attr & FileAttributes.ReadOnly) != 0) return "read_only";
                using (var output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
                { output.Write(payload, 0, payload.Length); output.Flush(true); }
                if (testHook != null) testHook("staged");
                using (var current = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete))
                using (var sha = SHA256.Create())
                {
                    if (current.Length > MaxBytes) return "conflict";
                    string observed = BitConverter.ToString(sha.ComputeHash(current)).Replace("-", "").ToLowerInvariant();
                    if (observed != baseline) return "conflict";
                    if (testHook != null) testHook("checked");
                    replaceAttempted = true;
                    File.Replace(temp, path, null, true);
                }
                if (testHook != null) testHook("replaced");
                byte[] actual = ReadBounded(path);
                if (Hash(actual) != Hash(payload)) return "write_unconfirmed";
                model = Parse(actual); baseline = Hash(actual);
                return "saved";
            }
            catch (UnauthorizedAccessException) { return replaceAttempted ? "write_unconfirmed" : "read_only"; }
            catch (IOException) { return replaceAttempted ? "write_unconfirmed" : "io_error"; }
            finally
            {
                try { if (File.Exists(temp)) File.Delete(temp); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                if (acquired) mutex.ReleaseMutex();
            }
        }
    }
}

// Validate duplicate object names independently of JavaScriptSerializer's last-value-wins behavior.
internal sealed class JsonTools
{
    private readonly string source; private int at; private int depth;
    private readonly JavaScriptSerializer serializer = Serializer();
    private JsonTools(string input) { source = input; }
    internal static JavaScriptSerializer Serializer() { return new JavaScriptSerializer { MaxJsonLength = DocumentSession.MaxBytes, RecursionLimit = 64 }; }
    internal static void RejectDuplicateKeys(string input)
    {
        var p = new JsonTools(input); p.Value(); p.White();
        if (p.at != input.Length) throw new InvalidDataException("JSON 尾部有无效内容。");
    }
    private void White() { while (at < source.Length && (source[at] == ' ' || source[at] == '\t' || source[at] == '\r' || source[at] == '\n')) at++; }
    private string StringToken()
    {
        if (at >= source.Length || source[at] != '"') throw new InvalidDataException("JSON 字符串无效。");
        int start = at++; bool escaped = false;
        while (at < source.Length)
        {
            char c = source[at++];
            if (escaped) { escaped = false; continue; }
            if (c == '\\') { escaped = true; continue; }
            if (c == '"') return serializer.Deserialize<string>(source.Substring(start, at - start));
            if (c < 0x20) throw new InvalidDataException("JSON 字符串包含控制字符。");
        }
        throw new InvalidDataException("JSON 字符串未结束。");
    }
    private void Value()
    {
        White(); if (++depth > 64) throw new InvalidDataException("JSON 嵌套过深。");
        if (at >= source.Length) throw new InvalidDataException("JSON 不完整。");
        char c = source[at];
        if (c == '{')
        {
            at++; White(); var keys = new HashSet<string>(StringComparer.Ordinal);
            if (at < source.Length && source[at] == '}') { at++; depth--; return; }
            while (true)
            {
                White(); string key = StringToken(); if (!keys.Add(key)) throw new InvalidDataException("JSON 字段重复：" + key);
                White(); if (at >= source.Length || source[at++] != ':') throw new InvalidDataException("JSON 对象无效。");
                Value(); White(); if (at >= source.Length) throw new InvalidDataException("JSON 对象不完整。");
                char delimiter = source[at++]; if (delimiter == '}') break; if (delimiter != ',') throw new InvalidDataException("JSON 对象无效。");
            }
        }
        else if (c == '[')
        {
            at++; White(); if (at < source.Length && source[at] == ']') { at++; depth--; return; }
            while (true) { Value(); White(); if (at >= source.Length) throw new InvalidDataException("JSON 数组不完整。"); char delimiter = source[at++]; if (delimiter == ']') break; if (delimiter != ',') throw new InvalidDataException("JSON 数组无效。"); }
        }
        else if (c == '"') StringToken();
        else { int start = at; while (at < source.Length && ",}] \t\r\n".IndexOf(source[at]) < 0) at++; if (start == at) throw new InvalidDataException("JSON 值无效。"); }
        depth--;
    }
}
