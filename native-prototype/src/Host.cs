using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length != 1)
        {
            MessageBox.Show("请打开一个 .kpldoc 规格文件。", "KPL 规格", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 2;
        }
        try { Application.Run(new EditorWindow(new DocumentSession(args[0]))); return 0; }
        catch (Exception ex) { MessageBox.Show("无法打开规格：" + ex.Message, "KPL 规格", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
    }
}

internal sealed class EditorWindow : Form
{
    private readonly DocumentSession session;
    private readonly string profile;
    private CoreWebView2Controller controller;
    private CoreWebView2 view;
    private bool firstNavigation = true, loaded, everReady, allowClose, closeCheck;
    private readonly string assetRoot;
    private const string AssetHost = "kpl-assets.local", AssetPrefix = "../05_参考资产/";
    // 缩略图缓存放在项目之外（本地应用数据目录下的 KPL/thumbs），按需生成，可随时删除重建
    private const string ThumbHost = "kpl-thumbs.local";
    private const int ThumbWidth = 480;
    private static readonly string ThumbRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KPL", "thumbs");
    private bool thumbsOk;
    // 版本号与更新源的唯一来源；页面只能请求“打开发布页”，网址不由页面决定
    internal const string AppVersion = "0.1.0";
    private const string RepoUrl = "https://github.com/AngleNaris/KPL", LatestApi = "https://api.github.com/repos/AngleNaris/KPL/releases/latest";
    private readonly Timer loadTimer = new Timer { Interval = 15000 };
    internal bool Ready { get { return loaded; } }

    internal EditorWindow(DocumentSession current)
    {
        session = current;
        assetRoot = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(current.FilePath), "..", "05_参考资产"));
        Text = "KPL 规格编辑器 — " + Path.GetFileName(current.FilePath);
        Icon = LoadIcon();
        MinimumSize = new Size(720, 480);
        StartPosition = FormStartPosition.CenterScreen;
        // 默认约 16:9，占工作区九成以内；上次关闭时的大小与最大化状态优先，但不超出当前工作区
        Rectangle area = Screen.PrimaryScreen.WorkingArea;
        int width = Math.Min(1440, area.Width * 9 / 10), height = width * 9 / 16;
        if (height > area.Height * 9 / 10) { height = area.Height * 9 / 10; width = height * 16 / 9; }
        int savedW, savedH; bool maximized;
        if (Prefs.Window(out savedW, out savedH, out maximized)) { width = savedW; height = savedH; }
        Width = Math.Min(Math.Max(width, 720), area.Width); Height = Math.Min(Math.Max(height, 480), area.Height);
        if (maximized) WindowState = FormWindowState.Maximized;
        FormClosed += delegate {
            Size size = WindowState == FormWindowState.Normal ? Size : RestoreBounds.Size;
            Prefs.SaveWindow(size.Width, size.Height, WindowState == FormWindowState.Maximized);
        };
        profile = Path.Combine(Path.GetTempPath(), "kpl-webview-" + Guid.NewGuid().ToString("N"));
        Shown += Start;
        loadTimer.Tick += delegate { loadTimer.Stop(); if (!loaded) MessageBox.Show("编辑器加载超时；原文件未被修改。请检查 WebView2 运行时。", "KPL 规格", MessageBoxButtons.OK, MessageBoxIcon.Error); };
        Resize += delegate { if (controller != null) controller.Bounds = ClientRectangle; };
        FormClosing += BeforeClose;
        FormClosed += delegate { if (controller != null) { controller.Close(); controller = null; } CleanupProfile(); };
    }

    private void CleanupProfile()
    {
        string root = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        string target = Path.GetFullPath(profile);
        if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(target).StartsWith("kpl-webview-", StringComparison.Ordinal) ||
            !String.Equals(Path.GetDirectoryName(target).TrimEnd(Path.DirectorySeparatorChar), root.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase)) return;
        for (int attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                if (!Directory.Exists(target)) return;
                if ((File.GetAttributes(target) & FileAttributes.ReparsePoint) != 0) return;
                Directory.Delete(target, true); return;
            }
            catch (IOException) { System.Threading.Thread.Sleep(200); }
            catch (UnauthorizedAccessException) { return; }
        }
    }

    private static string Template()
    {
        using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("KplNativeTemplate"))
        using (var reader = new StreamReader(stream, System.Text.Encoding.UTF8)) return reader.ReadToEnd();
    }

    private static string ScriptJson(string json)
    {
        return json.Replace("&", "\\u0026").Replace("<", "\\u003c").Replace(">", "\\u003e")
            .Replace("\u2028", "\\u2028").Replace("\u2029", "\\u2029");
    }

    private static string ReplaceOnce(string template, string marker, string value)
    {
        int at = template.IndexOf(marker, StringComparison.Ordinal);
        if (at < 0 || template.IndexOf(marker, at + marker.Length, StringComparison.Ordinal) >= 0)
            throw new InvalidDataException("编辑器模板无效。");
        return template.Substring(0, at) + value + template.Substring(at + marker.Length);
    }

    internal static string Render(string modelJson, string prefsJson = "{}")
    {
        string html = ReplaceOnce(Template(), "__KPL_NATIVE_DATA_PLACEHOLDER__", ScriptJson(modelJson));
        html = ReplaceOnce(html, "__KPL_NATIVE_PREFS_PLACEHOLDER__", ScriptJson(prefsJson));
        if (System.Text.Encoding.UTF8.GetByteCount(html) > 2 * 1024 * 1024)
            throw new InvalidDataException("规格内容过大，原型暂时无法打开。请保留原文件。");
        return html;
    }

    private async void Start(object sender, EventArgs ignored)
    {
        try
        {
            loadTimer.Start();
            var environment = await CoreWebView2Environment.CreateAsync(null, profile, null);
            controller = await environment.CreateCoreWebView2ControllerAsync(Handle);
            controller.Bounds = ClientRectangle;
            controller.AllowExternalDrop = false;
            view = controller.CoreWebView2;
            view.Settings.AreDevToolsEnabled = false;
            view.Settings.AreHostObjectsAllowed = false;
            view.Settings.AreBrowserAcceleratorKeysEnabled = false;
            view.Settings.IsGeneralAutofillEnabled = false;
            view.Settings.IsPasswordAutosaveEnabled = false;
            view.NewWindowRequested += delegate(object o, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
            view.PermissionRequested += delegate(object o, CoreWebView2PermissionRequestedEventArgs e) { e.State = CoreWebView2PermissionState.Deny; };
            view.DownloadStarting += delegate(object o, CoreWebView2DownloadStartingEventArgs e) { e.Cancel = true; };
            view.NavigationStarting += delegate(object o, CoreWebView2NavigationStartingEventArgs e) {
                if (!firstNavigation || !(e.Uri == "about:blank" || e.Uri.StartsWith("data:text/html;", StringComparison.OrdinalIgnoreCase))) e.Cancel = true;
                firstNavigation = false;
            };
            view.NavigationCompleted += delegate(object o, CoreWebView2NavigationCompletedEventArgs e) {
                if (!e.IsSuccess) { loadTimer.Stop(); MessageBox.Show("编辑界面未能加载，文件没有被修改。", "KPL 规格", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            };
            view.ProcessFailed += delegate(object o, CoreWebView2ProcessFailedEventArgs e) {
                if (everReady) loaded = false;
            };
            view.WebMessageReceived += OnMessage;
            // 只读映射文档旁的参考资产目录；DenyCors 允许 <img> 加载，但页面脚本读不到内容
            if (Directory.Exists(assetRoot) && (File.GetAttributes(assetRoot) & FileAttributes.ReparsePoint) == 0)
                view.SetVirtualHostNameToFolderMapping(AssetHost, assetRoot, CoreWebView2HostResourceAccessKind.DenyCors);
            // 缩略图缓存：只读映射，DenyCors 同参考资产；映射失败就不生成，界面只显示占位
            try
            {
                Directory.CreateDirectory(ThumbRoot);
                if ((File.GetAttributes(ThumbRoot) & FileAttributes.ReparsePoint) == 0)
                {
                    view.SetVirtualHostNameToFolderMapping(ThumbHost, ThumbRoot, CoreWebView2HostResourceAccessKind.DenyCors);
                    thumbsOk = true;
                }
            }
            catch (Exception) { /* 缓存不可用不影响打开 */ }
            view.NavigateToString(Render(session.InitialJson, Prefs.Load()));
        }
        catch (Exception ex)
        {
            loadTimer.Stop();
            MessageBox.Show("编辑器启动失败；文件没有被修改。\n" + ex.Message, "KPL 规格", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (view == null || e.Source != "about:blank" || view.Source != "about:blank") return;
        Dictionary<string, object> message;
        try { message = JsonTools.Serializer().DeserializeObject(e.WebMessageAsJson) as Dictionary<string, object>; }
        catch { PostResult("invalid"); return; }
        if (message == null || !message.ContainsKey("type") || !(message["type"] is string)) return;
        string type = (string)message["type"];
        if (type == "ready") { loaded = true; everReady = true; loadTimer.Stop(); PostRaw(new Dictionary<string, object> { { "type", "info" }, { "version", AppVersion } }); CheckUpdate(); if (thumbsOk) BuildThumbs(); return; }
        if (!loaded) return;
        if (type == "openUpdate") { OpenReleasePage(); return; }
        if (type == "prefs") { object prefs; if (message.TryGetValue("prefs", out prefs)) Prefs.Save(prefs as Dictionary<string, object>); return; }
        if (type == "copyImage") { object rel; message.TryGetValue("path", out rel); Post("copyResult", CopyImage(rel as string)); return; }
        if (type == "closeApproved") { allowClose = true; BeginInvoke((Action)Close); return; }
        if (type != "save") return;
        object raw; string result;
        if (!message.TryGetValue("model", out raw) || !(raw is Dictionary<string, object>)) result = "invalid";
        else
        {
            var next = (Dictionary<string, object>)raw;
            try { DocumentSession.Validate(next); }
            catch { PostResult("invalid"); return; }
            try { Render(JsonTools.Serializer().Serialize(next)); }
            catch { PostResult("too_large"); return; }
            try { result = session.Save(next, null); }
            catch { result = "write_unconfirmed"; }
        }
        PostResult(result);
    }

    private void PostResult(string result) { Post("saveResult", result); }

    private void Post(string type, string status)
    {
        if (view != null) view.PostWebMessageAsJson(JsonTools.Serializer().Serialize(new Dictionary<string, object> { { "type", type }, { "status", status } }));
    }

    private void PostRaw(Dictionary<string, object> message)
    {
        if (view != null) view.PostWebMessageAsJson(JsonTools.Serializer().Serialize(message));
    }

    private static Icon LoadIcon()
    {
        try
        {
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("KplIcon"))
                return stream == null ? null : new Icon(stream);
        }
        catch (Exception) { return null; }
    }

    /* 启动后在后台查询 GitHub 最新 Release；只在发现更高版本时通知页面，失败一律静默，不影响编辑。只提示，不下载、不安装。 */
    private void CheckUpdate()
    {
        Task.Run(() =>
        {
            try
            {
                ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072; // TLS 1.2
                var request = (HttpWebRequest)WebRequest.Create(LatestApi);
                request.UserAgent = "KPL-Editor/" + AppVersion;
                request.Timeout = 5000;
                string json;
                using (var response = request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream(), System.Text.Encoding.UTF8)) json = reader.ReadToEnd();
                var release = JsonTools.Serializer().DeserializeObject(json) as Dictionary<string, object>;
                object tag;
                if (release == null || !release.TryGetValue("tag_name", out tag) || !(tag is string)) return;
                string latest = ((string)tag).TrimStart('v', 'V');
                Version newer, current;
                if (!Version.TryParse(latest, out newer) || !Version.TryParse(AppVersion, out current) || newer <= current) return;
                if (IsHandleCreated) BeginInvoke((Action)(() => PostRaw(new Dictionary<string, object> { { "type", "update" }, { "version", latest } })));
            }
            catch (Exception) { /* 离线或限流都当作没有更新 */ }
        });
    }

    private void OpenReleasePage()
    {
        try { Process.Start(new ProcessStartInfo(RepoUrl + "/releases/latest") { UseShellExecute = true }); }
        catch (Exception) { /* 打不开浏览器不影响编辑 */ }
    }

    /* 解析参考资产目录内的普通图片文件；越界、链接、过大或不存在都返回 null。 */
    private string ResolveAsset(string rel)
    {
        if (rel == null || !rel.StartsWith(AssetPrefix, StringComparison.Ordinal) || !DocumentSession.SafeThumbnail(rel)) return null;
        string full = Path.GetFullPath(Path.Combine(assetRoot, rel.Substring(AssetPrefix.Length).Replace('/', Path.DirectorySeparatorChar)));
        if (!full.StartsWith(assetRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return null;
        var info = new FileInfo(full);
        if (!info.Exists || (info.Attributes & FileAttributes.ReparsePoint) != 0 || info.Length > 64L * 1024 * 1024) return null;
        return full;
    }

    private void PostUi(Dictionary<string, object> message)
    {
        try
        {
            if (IsHandleCreated && !IsDisposed) BeginInvoke((Action)(() => { try { PostRaw(message); } catch (Exception) { /* 窗口已关闭 */ } }));
        }
        catch (Exception) { /* 窗口已关闭 */ }
    }

    private static void CollectPaths(Dictionary<string, object> model, string list, string field, List<string> paths, HashSet<string> seen)
    {
        object items;
        if (model == null || !model.TryGetValue(list, out items) || !(items is object[])) return;
        foreach (object item in (object[])items)
        {
            var entry = item as Dictionary<string, object>;
            object value;
            if (entry != null && entry.TryGetValue(field, out value) && value is string && ((string)value).Length > 0 && seen.Add((string)value)) paths.Add((string)value);
        }
    }

    /* 缓存文件名由“相对路径 + 大小 + 修改时间”的哈希决定：原图不变就不重做，原图一变就换新文件。 */
    internal static string ThumbName(string rel, FileInfo info)
    {
        byte[] key = System.Text.Encoding.UTF8.GetBytes(rel + "|" + info.Length + "|" + info.LastWriteTimeUtc.Ticks);
        return DocumentSession.Hash(key).Substring(0, 32) + ".jpg";
    }

    /* 把原图缩成不超过 ThumbWidth 宽的 JPEG；先写临时文件再改名，失败（含 .NET 解不了的格式）返回 false。 */
    internal static bool MakeThumb(string source, string target)
    {
        string temp = target + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            System.Drawing.Imaging.ImageCodecInfo jpeg = null;
            foreach (var codec in System.Drawing.Imaging.ImageCodecInfo.GetImageEncoders()) if (codec.MimeType == "image/jpeg") jpeg = codec;
            if (jpeg == null) return false;
            using (var stream = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var image = Image.FromStream(stream, false, false))
            {
                int width = Math.Min(ThumbWidth, image.Width), height = Math.Max(1, (int)Math.Round((double)image.Height * width / image.Width));
                using (var small = new Bitmap(width, height))
                {
                    using (var g = Graphics.FromImage(small))
                    {
                        g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                        g.DrawImage(image, 0, 0, width, height);
                    }
                    var options = new System.Drawing.Imaging.EncoderParameters(1);
                    options.Param[0] = new System.Drawing.Imaging.EncoderParameter(System.Drawing.Imaging.Encoder.Quality, 82L);
                    small.Save(temp, jpeg, options);
                }
            }
            if (!File.Exists(target)) File.Move(temp, target);
            return true;
        }
        catch (Exception) { return false; }
        finally { try { if (File.Exists(temp)) File.Delete(temp); } catch (Exception) { } }
    }

    /* 后台生成缩略图缓存并向页面汇报进度；页面只载入已就绪的缩略图，不载入原图。生成失败的图保持“未缓存”。 */
    private void BuildThumbs()
    {
        var model = JsonTools.Serializer().DeserializeObject(session.InitialJson) as Dictionary<string, object>;
        var paths = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        CollectPaths(model, "shots", "thumbnail", paths, seen);
        CollectPaths(model, "assets", "path", paths, seen);
        Task.Run(() =>
        {
            var gate = new object();
            var pending = new Dictionary<string, object>();
            var todo = new List<string[]>();
            foreach (string rel in paths)
            {
                string full = ResolveAsset(rel);
                if (full == null) continue;
                string name = ThumbName(rel, new FileInfo(full));
                if (File.Exists(Path.Combine(ThumbRoot, name))) pending[rel] = name; else todo.Add(new[] { rel, full, name });
            }
            int done = pending.Count, total = pending.Count + todo.Count;
            Action<bool> flush = delegate(bool finished)
            {
                Dictionary<string, object> ready;
                lock (gate) { ready = pending; pending = new Dictionary<string, object>(); }
                PostUi(new Dictionary<string, object> { { "type", "thumbs" }, { "done", done }, { "total", total }, { "finished", finished }, { "ready", ready } });
            };
            flush(todo.Count == 0);
            if (todo.Count == 0) return;
            var tick = Stopwatch.StartNew();
            Parallel.ForEach(todo, new ParallelOptions { MaxDegreeOfParallelism = Math.Max(1, Environment.ProcessorCount / 2) }, item =>
            {
                bool ok = MakeThumb(item[1], Path.Combine(ThumbRoot, item[2]));
                System.Threading.Interlocked.Increment(ref done);
                bool send = false;
                lock (gate)
                {
                    if (ok) pending[item[0]] = item[2];
                    if (tick.ElapsedMilliseconds >= 300) { tick.Restart(); send = true; }
                }
                if (send) flush(false);
            });
            flush(true);
        });
    }

    /* 只复制参考资产目录内的普通图片文件，以位图放入剪贴板。 */
    private string CopyImage(string rel)
    {
        try
        {
            string full = ResolveAsset(rel);
            if (full == null) return "failed";
            using (var stream = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var image = Image.FromStream(stream))
            using (var bitmap = new Bitmap(image))
                Clipboard.SetImage(bitmap);
            return "copied";
        }
        catch (Exception) { return "failed"; }
    }

    private async void BeforeClose(object sender, FormClosingEventArgs e)
    {
        if (allowClose) return;
        e.Cancel = true;
        if (closeCheck) return;
        closeCheck = true;
        try
        {
            if ((!loaded || view == null) && !everReady) { allowClose = true; Close(); return; }
            if (!loaded || view == null) throw new InvalidDataException("编辑器进程已停止。");
            Task<string> task = view.ExecuteScriptAsync("window.__kplCloseState ? window.__kplCloseState() : null");
            if (await Task.WhenAny(task, Task.Delay(3000)) != task) throw new TimeoutException();
            string result = await task;
            var state = JsonTools.Serializer().DeserializeObject(result) as Dictionary<string, object>;
            if (state == null || !state.ContainsKey("dirty") || !(state["dirty"] is bool) || !state.ContainsKey("saving") || !(state["saving"] is bool))
                throw new InvalidDataException("无法读取编辑状态。");
            if ((bool)state["saving"]) { MessageBox.Show("正在保存，请稍候再关闭。", "KPL 规格"); return; }
            if ((bool)state["dirty"])
            {
                // 页面弹出“保存 / 放弃修改 / 取消”；用户决定后页面发 closeApproved
                string asked = await view.ExecuteScriptAsync("window.__kplRequestClose ? window.__kplRequestClose() : false");
                if (asked == "true") return;
                if (MessageBox.Show("有未保存修改。放弃这些修改并关闭吗？", "KPL 规格", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;
            }
            allowClose = true; Close();
        }
        catch
        {
            if (MessageBox.Show("无法确认编辑状态，关闭可能丢失未保存的修改。仍要关闭吗？", "KPL 规格", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
            { allowClose = true; Close(); }
        }
        finally { closeCheck = false; }
    }
}

/* 显示偏好（深浅色、主题色）、布局尺寸（时间线面板高度、详情栏宽度）与窗口大小保存在当前用户的本地应用数据中；只接受白名单内的取值。
   页面只能写显示与布局，窗口大小只由宿主在关闭时写入；两边写入都与现有文件合并，互不覆盖。 */
internal static class Prefs
{
    // 测试会改指到临时目录，不碰用户真实偏好
    internal static string FilePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KPL", "prefs.json");
    private static readonly string[] Modes = { "system", "light", "dark" }, Accents = { "amber", "red", "teal", "blue" }, Sizes = { "panelH", "asideW", "winW", "winH" };
    private static readonly string[] WindowKeys = { "winW", "winH", "winMax" };

    private static Dictionary<string, object> Clean(Dictionary<string, object> raw)
    {
        var clean = new Dictionary<string, object>();
        object value;
        if (raw != null && raw.TryGetValue("mode", out value) && Array.IndexOf(Modes, value as string) >= 0) clean["mode"] = value;
        if (raw != null && raw.TryGetValue("accent", out value) && Array.IndexOf(Accents, value as string) >= 0) clean["accent"] = value;
        foreach (string key in Sizes)
        {
            if (raw == null || !raw.TryGetValue(key, out value) || !(value is int || value is decimal || value is double)) continue;
            double size = Convert.ToDouble(value);
            if (size >= 100 && size <= 8000) clean[key] = (int)Math.Round(size);
        }
        if (raw != null && raw.TryGetValue("winMax", out value) && value is bool) clean["winMax"] = value;
        return clean;
    }

    private static Dictionary<string, object> Read()
    {
        try
        {
            var info = new FileInfo(FilePath);
            if (!info.Exists || info.Length > 4096) return new Dictionary<string, object>();
            return Clean(new JavaScriptSerializer().DeserializeObject(File.ReadAllText(FilePath)) as Dictionary<string, object>);
        }
        catch (Exception) { return new Dictionary<string, object>(); }
    }

    private static void Write(Dictionary<string, object> clean)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
            string temp = FilePath + ".tmp";
            File.WriteAllText(temp, new JavaScriptSerializer().Serialize(Clean(clean)));
            if (File.Exists(FilePath)) File.Replace(temp, FilePath, null); else File.Move(temp, FilePath);
        }
        catch (Exception) { /* 偏好写入失败不影响编辑 */ }
    }

    internal static string Load() { return new JavaScriptSerializer().Serialize(Read()); }

    /* 页面发来的偏好：合并进现有文件，忽略窗口相关的键。 */
    internal static void Save(Dictionary<string, object> raw)
    {
        var merged = Read();
        foreach (var pair in Clean(raw)) if (Array.IndexOf(WindowKeys, pair.Key) < 0) merged[pair.Key] = pair.Value;
        Write(merged);
    }

    internal static bool Window(out int width, out int height, out bool maximized)
    {
        var prefs = Read();
        object w, h, max;
        maximized = prefs.TryGetValue("winMax", out max) && (bool)max;
        width = prefs.TryGetValue("winW", out w) ? (int)w : 0;
        height = prefs.TryGetValue("winH", out h) ? (int)h : 0;
        return width > 0 && height > 0;
    }

    internal static void SaveWindow(int width, int height, bool maximized)
    {
        var merged = Read();
        merged["winW"] = width; merged["winH"] = height; merged["winMax"] = maximized;
        Write(merged);
    }
}
