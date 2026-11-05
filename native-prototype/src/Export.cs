using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

/* 导出 HTML 交接包：与编辑器相同界面的只读页面，连同页面用到的资产图与时间线素材打成一个 zip。
   zip 里的 #kpl-export 写入“文档路径 → 包内路径”的映射，页面从包内读图与视频；
   接收方解压后直接打开 HTML 即可，不需要原项目文件夹。 */
internal sealed partial class EditorWindow
{
    // 导出页面在浏览器里用 file:// 打开：允许直接读包内的图片与视频，脚本仍不能联网或读取文件内容
    private const string ExportCsp = "default-src 'none'; img-src file: data: 'self'; media-src file: 'self'; style-src 'unsafe-inline'; " +
        "script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'";
    private const string ExportMarker = "<script type=\"application/json\" id=\"kpl-export\">null</script>";
    private const string ViewOnlyMeta = "<meta name=\"kpl-view-only\" content=\"true\">";
    private const string HandoffHtmlSuffix = "_交接查看.html";

    private Dictionary<string, object> ExportHtml()
    {
        string specDir = Path.GetDirectoryName(session.FilePath), target;
        Dictionary<string, object> model = JsonTools.Serializer().DeserializeObject(session.InitialJson) as Dictionary<string, object>;
        string stem = HandoffStem(model, session.FilePath);
        using (var dialog = new SaveFileDialog
        {
            Title = "导出 HTML 交接包", Filter = "交接包 (*.zip)|*.zip", DefaultExt = "zip", AddExtension = true,
            InitialDirectory = specDir, FileName = stem + "_交接包.zip", OverwritePrompt = false
        })
        {
            if (dialog.ShowDialog(this) != DialogResult.OK) return Result("cancelled", null);
            target = Path.GetFullPath(dialog.FileName);
        }
        if (!String.Equals(Path.GetExtension(target), ".zip", StringComparison.OrdinalIgnoreCase)) return Result("invalid_target", target);
        // 只覆盖以前导出的交接包，不覆盖其他压缩文件
        if (File.Exists(target) && !IsHandoffZip(target)) return Result("not_overwritten", target);
        try { return WriteHandoff(session.InitialJson, session.FilePath, target, Prefs.Load()); }
        catch (IOException) { return Result("write_failed", target); }
        catch (UnauthorizedAccessException) { return Result("write_failed", target); }
        catch (InvalidDataException) { return Result("write_failed", target); }
    }

    /* 项目名与版本一起进入 zip 名和包内 HTML 名，例如“清晨面馆_v001_交接包.zip”。 */
    private static string HandoffStem(Dictionary<string, object> model, string specPath)
    {
        string title = model != null && model.ContainsKey("title") && model["title"] is string ? ((string)model["title"]).Trim() : "";
        if (title.Length == 0) title = Path.GetFileNameWithoutExtension(specPath);
        string version = model != null && model.ContainsKey("version") && model["version"] is string ? ((string)model["version"]).Trim() : "";
        string stem = SafeFileName(title, 60) + (version.Length > 0 ? "_" + SafeFileName(version, 20) : "");
        return stem.Trim('_').Length > 0 ? stem.Trim('_') : "KPL_交接";
    }

    private static string SafeFileName(string value, int limit)
    {
        string clean = Regex.Replace(value, @"[<>:""/\\|?*\x00-\x1f]", "_").Trim().TrimEnd('.');
        clean = Regex.Replace(clean, @"\s+", " ");
        return clean.Length > limit ? clean.Substring(0, limit).TrimEnd('.', ' ') : clean;
    }

    private static bool IsHandoffZip(string path)
    {
        try
        {
            using (var stream = File.OpenRead(path))
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Read))
                foreach (ZipArchiveEntry entry in zip.Entries)
                    if (entry.FullName.EndsWith(HandoffHtmlSuffix, StringComparison.Ordinal)) return true;
        }
        catch (InvalidDataException) { }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return false;
    }

    /* 生成 HTML 与 zip；素材缺失的不写入包内映射，HTML 里会显示为占位。 */
    internal static Dictionary<string, object> WriteHandoff(string modelJson, string specPath, string target, string prefsJson)
    {
        return WriteHandoffPage(modelJson, specPath, target, Render(modelJson, prefsJson));
    }

    internal static Dictionary<string, object> WriteHandoffPage(string modelJson, string specPath, string target, string html)
    {
        string specDir = Path.GetDirectoryName(specPath);
        Dictionary<string, object> model = JsonTools.Serializer().DeserializeObject(modelJson) as Dictionary<string, object>;
        if (model == null) throw new InvalidDataException("规格内容无效。");
        string stem = HandoffStem(model, specPath);
        var map = new Dictionary<string, object>(StringComparer.Ordinal);
        var resources = new List<string[]>();
        var used = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int missing = 0;
        foreach (KeyValuePair<string, bool> item in ReferencedResources(model))
        {
            string full = ResolveExportResource(specDir, item.Key, item.Value);
            if (full == null) { missing++; continue; }
            if (map.ContainsKey(item.Key)) continue;
            string entry = EntryName(item.Key, full, used);
            map[item.Key] = entry;
            resources.Add(new[] { full, entry });
        }

        html = new Regex("<meta http-equiv=\"Content-Security-Policy\" content=\"[^\"]*\">")
            .Replace(html, match => "<meta http-equiv=\"Content-Security-Policy\" content=\"" + ExportCsp + "\">", 1);
        html = ReplaceOnce(html, "<meta charset=\"utf-8\">", "<meta charset=\"utf-8\">" + ViewOnlyMeta);
        var info = new Dictionary<string, object> { { "base", "" }, { "map", map } };
        html = ReplaceOnce(html, ExportMarker, "<script type=\"application/json\" id=\"kpl-export\">"
            + ScriptJson(JsonTools.Serializer().Serialize(info)) + "</script>");

        string htmlName = stem + HandoffHtmlSuffix;
        string temp = Path.Combine(Path.GetDirectoryName(target),
            "." + Path.GetFileName(target) + "." + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Create))
            {
                WriteEntry(zip, htmlName, new UTF8Encoding(false).GetBytes(html), CompressionLevel.Optimal);
                foreach (string[] resource in resources)
                {
                    using (var input = new FileStream(resource[0], FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        ZipArchiveEntry entry = zip.CreateEntry(resource[1], CompressionLevel.NoCompression);
                        using (Stream output = entry.Open()) input.CopyTo(output);
                    }
                }
            }
            if (File.Exists(target)) File.Replace(temp, target, null); else File.Move(temp, target);
            return Result("exported", target, resources.Count, missing);
        }
        finally
        {
            try { if (File.Exists(temp)) File.Delete(temp); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private static void WriteEntry(ZipArchive zip, string name, byte[] bytes, CompressionLevel level)
    {
        ZipArchiveEntry entry = zip.CreateEntry(name, level);
        using (Stream output = entry.Open()) output.Write(bytes, 0, bytes.Length);
    }

    /* 页面实际会载入的文件：被描述/提示词引用的资产图与分镜缩略图是第一类，时间线里的片段（含备选视频版本）是第二类。 */
    private static List<KeyValuePair<string, bool>> ReferencedResources(Dictionary<string, object> model)
    {
        var list = new List<KeyValuePair<string, bool>>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        Action<string, bool> add = (value, image) =>
        {
            if (!String.IsNullOrEmpty(value) && seen.Add((image ? "i:" : "m:") + value))
                list.Add(new KeyValuePair<string, bool>(value, image));
        };
        // 与页面一致：@{…} 可以写 ID、名称、路径、文件名或主名，只有唯一匹配才算解析
        var aliases = new Dictionary<string, List<string>>(StringComparer.Ordinal);
        object items;
        if (model.TryGetValue("assets", out items) && items is object[])
            foreach (object item in (object[])items)
            {
                var asset = item as Dictionary<string, object>;
                string path = asset != null && asset.ContainsKey("path") ? asset["path"] as string : null;
                if (String.IsNullOrEmpty(path)) continue;
                foreach (string alias in AssetAliases(asset))
                {
                    List<string> paths;
                    if (!aliases.TryGetValue(alias, out paths)) aliases[alias] = paths = new List<string>();
                    if (!paths.Contains(path)) paths.Add(path);
                }
            }
        var reference = new Regex(@"@\{([^{}]+)\}");
        if (model.TryGetValue("shots", out items) && items is object[])
            foreach (object item in (object[])items)
            {
                var shot = item as Dictionary<string, object>;
                if (shot == null) continue;
                foreach (string field in new[] { "description", "prompt", "video_prompt" })
                {
                    string text = shot.ContainsKey(field) ? shot[field] as string : null;
                    if (String.IsNullOrEmpty(text)) continue;
                    foreach (Match match in reference.Matches(text))
                    {
                        List<string> paths;
                        if (aliases.TryGetValue(match.Groups[1].Value.Trim(), out paths) && paths.Count == 1) add(paths[0], true);
                    }
                }
                if (shot.ContainsKey("thumbnail")) add(shot["thumbnail"] as string, true);
            }
        object timelineObj;
        if (model.TryGetValue("timeline", out timelineObj))
        {
            var timeline = timelineObj as Dictionary<string, object>;
            if (timeline != null)
                foreach (string kind in new[] { "video", "audio" })
                    if (timeline.ContainsKey(kind) && timeline[kind] is object[])
                        foreach (object trackItem in (object[])timeline[kind])
                        {
                            var track = trackItem as Dictionary<string, object>;
                            object clips;
                            if (track == null || !track.TryGetValue("clips", out clips) || !(clips is object[])) continue;
                            foreach (object clipItem in (object[])clips)
                            {
                                var clip = clipItem as Dictionary<string, object>;
                                if (clip == null) continue;
                                add(clip.ContainsKey("source") ? clip["source"] as string : null, false);
                                object takes;
                                if (clip.TryGetValue("takes", out takes) && takes is object[])
                                    foreach (object takeItem in (object[])takes)
                                    {
                                        var take = takeItem as Dictionary<string, object>;
                                        if (take != null) add(take["source"] as string, false);
                                    }
                            }
                        }
        }
        return list;
    }

    private static IEnumerable<string> AssetAliases(Dictionary<string, object> asset)
    {
        string id = asset.ContainsKey("id") ? asset["id"] as string : null;
        string name = asset.ContainsKey("name") ? asset["name"] as string : null;
        string path = asset["path"] as string;
        string file = path.Substring(path.LastIndexOf('/') + 1);
        foreach (string alias in new[] { id, name, path, file, Regex.Replace(file, @"\.[^.]+$", "") })
            if (!String.IsNullOrEmpty(alias)) yield return alias;
    }

    private static string ResolveExportResource(string specDir, string source, bool image)
    {
        if (image)
        {
            if (!DocumentSession.SafeThumbnail(source)) return null;
            try
            {
                string full = Path.GetFullPath(Path.Combine(specDir, source.Replace('/', Path.DirectorySeparatorChar)));
                var info = new FileInfo(full);
                return info.Exists && (info.Attributes & FileAttributes.ReparsePoint) == 0 ? full : null;
            }
            catch (Exception) { return null; }
        }
        return ResolveMedia(specDir, source);
    }

    /* 包内路径固定放在“资料/”下：相对路径去掉开头的 ../，绝对路径放到“资料/外部/”。 */
    private static string EntryName(string source, string full, HashSet<string> used)
    {
        string relative;
        if (Path.IsPathRooted(source))
        {
            relative = "外部/" + source.Replace(':', '_');
        }
        else
        {
            relative = source;
            while (relative.StartsWith("../", StringComparison.Ordinal)) relative = relative.Substring(3);
        }
        var parts = new List<string>();
        foreach (string part in relative.Split('/'))
        {
            if (part.Length == 0 || part == "." || part == "..") continue;
            string clean = Regex.Replace(part, @"[<>:""\\|?*\x00-\x1f]", "_").Trim().TrimEnd('.');
            if (clean.Length > 0) parts.Add(clean);
        }
        if (parts.Count == 0) parts.Add(SafeFileName(Path.GetFileName(full), 80));
        string candidate = "资料/" + String.Join("/", parts.ToArray());
        string baseName = candidate;
        int index = 2;
        while (!used.Add(candidate))
        {
            string dir = Path.GetDirectoryName(baseName);
            string name = Path.GetFileNameWithoutExtension(baseName) + "-" + index + Path.GetExtension(baseName);
            candidate = (String.IsNullOrEmpty(dir) ? "" : dir.Replace('\\', '/') + "/") + name;
            index++;
        }
        return candidate;
    }

    private static Dictionary<string, object> Result(string status, string path, int included = 0, int missing = 0)
    {
        return new Dictionary<string, object> {
            { "status", status }, { "path", path == null ? "" : Path.GetFileName(path) },
            { "included", included }, { "missing", missing }
        };
    }
}
