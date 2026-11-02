using System;
using System.Collections.Generic;
using System.IO;
using System.Windows.Forms;

/* 时间线媒体：用系统对话框选文件，经只读虚拟主机供页面的 <video>/<audio> 播放。
   WebView2 只认导航前设置的映射，素材又可能来自任意文件夹，所以启动时按盘符各映射一个主机；
   页面的 CSP 只允许把它们用作 media-src，DenyCors 下脚本读不到内容，地址也只由宿主按文档路径生成。
   路径相对 .kpldoc 所在目录记录，素材在其他盘时才记绝对路径。 */
internal sealed partial class EditorWindow
{
    private const string MediaHostSuffix = ".kpl-media.local";
    internal static readonly string[] VideoTypes = { ".mp4", ".m4v", ".mov", ".webm" };
    internal static readonly string[] AudioTypes = { ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus" };
    private readonly HashSet<string> mediaDrives = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    private string mediaFolder;   // 上次选择素材的文件夹；首次为项目根目录

    private void OnMediaMessage(string type, Dictionary<string, object> message)
    {
        object request; message.TryGetValue("request", out request);
        if (type == "pickMedia")
        {
            object kind, multiple; message.TryGetValue("kind", out kind); message.TryGetValue("multiple", out multiple);
            var reply = PickMedia(kind as string == "audio", multiple is bool && (bool)multiple);
            reply["type"] = "mediaPicked"; reply["request"] = request;
            PostRaw(reply);
            return;
        }
        // 字幕：只读取现成的 srt/as/s/lrc 文件并解析成条目，不在这里生成或对齐
        if (type == "pickSubtitle")
        {
            var reply = PickSubtitle();
            reply["type"] = "subtitlePicked"; reply["request"] = request;
            PostRaw(reply);
            return;
        }
        // resolveMedia：把文档里的素材路径换成可播放地址；缺失或不支持的格式给空字符串
        object raw; message.TryGetValue("sources", out raw);
        var urls = new Dictionary<string, object>(StringComparer.Ordinal);
        var sources = raw as object[];
        if (sources != null) foreach (object item in sources)
        {
            var source = item as string;
            if (source != null && !urls.ContainsKey(source)) urls[source] = MediaUrl(source) ?? "";
        }
        PostRaw(new Dictionary<string, object> { { "type", "mediaUrls" }, { "request", request }, { "urls", urls } });
    }

    /* 选字幕文件并解析成条目；格式不支持、读不出或没有任何带时间的条目都在这里拦下，页面只收条目。 */
    private Dictionary<string, object> PickSubtitle()
    {
        string pattern = "*.ass;*.ssa;*.srt;*.lrc";
        using (var dialog = new OpenFileDialog
        {
            Title = "选择字幕或歌词",
            Filter = "字幕与歌词 (" + pattern + ")|" + pattern,
            CheckFileExists = true,
            Multiselect = false
        })
        {
            string folder = Path.GetDirectoryName(session.FilePath);
            dialog.InitialDirectory = mediaFolder ?? Path.GetDirectoryName(folder) ?? folder;
            if (dialog.ShowDialog(this) != DialogResult.OK) return new Dictionary<string, object> { { "status", "cancelled" } };
            mediaFolder = Path.GetDirectoryName(dialog.FileName);
            List<Subtitle.Cue> cues;
            try { cues = Subtitle.Read(dialog.FileName); }
            catch (InvalidDataException ex) { return new Dictionary<string, object> { { "status", "invalid" }, { "message", ex.Message } }; }
            return new Dictionary<string, object>
            {
                { "status", "picked" },
                { "name", Path.GetFileNameWithoutExtension(dialog.FileName) },
                { "language", Subtitle.LanguageOf(Path.GetExtension(dialog.FileName)) },
                { "cues", Subtitle.ToJson(cues) }
            };
        }
    }

    /* multiple：一次选多个文件（同一镜头的多次生成结果）。items 列出全部可用的文件，不支持的跳过；
       source/url 是第一个，单选的调用方照旧使用。 */
    private Dictionary<string, object> PickMedia(bool audio, bool multiple)
    {
        string[] types = audio ? AudioTypes : VideoTypes;
        string pattern = "*" + String.Join(";*", types);
        using (var dialog = new OpenFileDialog
        {
            Title = audio ? "选择音频" : "选择视频",
            Filter = (audio ? "音频" : "视频") + " (" + pattern + ")|" + pattern,
            CheckFileExists = true,
            Multiselect = multiple
        })
        {
            string folder = Path.GetDirectoryName(session.FilePath);
            dialog.InitialDirectory = mediaFolder ?? Path.GetDirectoryName(folder) ?? folder;
            if (dialog.ShowDialog(this) != DialogResult.OK) return new Dictionary<string, object> { { "status", "cancelled" } };
            mediaFolder = Path.GetDirectoryName(dialog.FileName);
            var items = new List<object>();
            foreach (string file in dialog.FileNames)
            {
                string source = SourceFor(file);
                string url = DocumentSession.ValidSource(source) ? MediaUrl(source) : null;
                if (url != null) items.Add(new Dictionary<string, object> { { "source", source }, { "url", url } });
            }
            if (items.Count == 0) return new Dictionary<string, object> { { "status", "unsupported" } };
            var first = (Dictionary<string, object>)items[0];
            return new Dictionary<string, object> { { "status", "picked" }, { "source", first["source"] }, { "url", first["url"] }, { "items", items.ToArray() } };
        }
    }

    /* 相对 .kpldoc 所在目录的路径，统一用 /；不在同一个盘时返回绝对路径。 */
    internal string SourceFor(string file)
    {
        return RelativeSource(Path.GetDirectoryName(session.FilePath), file);
    }

    internal static string RelativeSource(string folder, string file)
    {
        string[] from = Path.GetFullPath(folder).TrimEnd(Path.DirectorySeparatorChar).Split(Path.DirectorySeparatorChar);
        string[] to = Path.GetFullPath(file).Split(Path.DirectorySeparatorChar);
        if (!String.Equals(from[0], to[0], StringComparison.OrdinalIgnoreCase)) return String.Join("/", to);
        int common = 0;
        while (common < from.Length && common < to.Length - 1 && String.Equals(from[common], to[common], StringComparison.OrdinalIgnoreCase)) common++;
        var parts = new List<string>();
        for (int i = common; i < from.Length; i++) parts.Add("..");
        for (int i = common; i < to.Length; i++) parts.Add(to[i]);
        return String.Join("/", parts.ToArray());
    }

    /* 解析为本机普通媒体文件；不存在、链接或扩展名不在白名单都返回 null。 */
    internal static string ResolveMedia(string folder, string source)
    {
        if (source == null || !DocumentSession.ValidSource(source)) return null;
        try
        {
            string native = source.Replace('/', Path.DirectorySeparatorChar);
            string full = Path.GetFullPath(Path.IsPathRooted(native) ? native : Path.Combine(folder, native));
            string ext = Path.GetExtension(full).ToLowerInvariant();
            if (Array.IndexOf(VideoTypes, ext) < 0 && Array.IndexOf(AudioTypes, ext) < 0) return null;
            var info = new FileInfo(full);
            if (!info.Exists || (info.Attributes & FileAttributes.ReparsePoint) != 0) return null;
            return full;
        }
        catch (Exception) { return null; }
    }

    /* 导航前调用：本机、可移动与网络盘的根目录各映射为 drive-<盘符>.kpl-media.local。 */
    private void MapMediaDrives()
    {
        foreach (DriveInfo drive in DriveInfo.GetDrives())
        {
            string root = drive.Name, letter = root.Substring(0, 1).ToLowerInvariant();
            if (!(drive.DriveType == DriveType.Fixed || drive.DriveType == DriveType.Removable || drive.DriveType == DriveType.Network)
                || root.Length != 3 || !Char.IsLetter(root[0])) continue;
            try
            {
                view.SetVirtualHostNameToFolderMapping("drive-" + letter + MediaHostSuffix, root, Microsoft.Web.WebView2.Core.CoreWebView2HostResourceAccessKind.DenyCors);
                mediaDrives.Add(letter);
            }
            catch (Exception) { /* 这个盘不可用就不映射，上面的素材显示为缺失 */ }
        }
    }

    internal static string MediaPath(string full)
    {
        string[] parts = full.Substring(3).Split(Path.DirectorySeparatorChar);
        for (int i = 0; i < parts.Length; i++) parts[i] = Uri.EscapeDataString(parts[i]);
        return "https://drive-" + full.Substring(0, 1).ToLowerInvariant() + MediaHostSuffix + "/" + String.Join("/", parts);
    }

    private string MediaUrl(string source)
    {
        string full = ResolveMedia(Path.GetDirectoryName(session.FilePath), source);
        if (full == null || full.Length < 4 || full[1] != ':' || !mediaDrives.Contains(full.Substring(0, 1))) return null;
        return MediaPath(full);
    }
}
