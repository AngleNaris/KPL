using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
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
    private readonly Timer loadTimer = new Timer { Interval = 15000 };
    internal bool Ready { get { return loaded; } }

    internal EditorWindow(DocumentSession current)
    {
        session = current;
        assetRoot = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(current.FilePath), "..", "05_参考资产"));
        Text = "KPL 规格 — " + Path.GetFileName(current.FilePath);
        Width = 1120; Height = 820; MinimumSize = new Size(720, 480);
        StartPosition = FormStartPosition.CenterScreen;
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
        if (type == "ready") { loaded = true; everReady = true; loadTimer.Stop(); return; }
        if (!loaded) return;
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

    /* 只复制参考资产目录内的普通图片文件，以位图放入剪贴板。 */
    private string CopyImage(string rel)
    {
        try
        {
            if (rel == null || !rel.StartsWith(AssetPrefix, StringComparison.Ordinal) || !DocumentSession.SafeThumbnail(rel)) return "failed";
            string full = Path.GetFullPath(Path.Combine(assetRoot, rel.Substring(AssetPrefix.Length).Replace('/', Path.DirectorySeparatorChar)));
            if (!full.StartsWith(assetRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) return "failed";
            var info = new FileInfo(full);
            if (!info.Exists || (info.Attributes & FileAttributes.ReparsePoint) != 0 || info.Length > 64L * 1024 * 1024) return "failed";
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

/* 显示偏好（深浅色、主题色）与布局尺寸（时间线面板高度、详情栏宽度）保存在当前用户的本地应用数据中；只接受白名单内的取值。 */
internal static class Prefs
{
    private static readonly string FilePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KPL", "prefs.json");
    private static readonly string[] Modes = { "system", "light", "dark" }, Accents = { "amber", "red", "teal", "blue" }, Sizes = { "panelH", "asideW" };

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
        return clean;
    }

    internal static string Load()
    {
        try
        {
            var info = new FileInfo(FilePath);
            if (!info.Exists || info.Length > 4096) return "{}";
            var raw = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(FilePath)) as Dictionary<string, object>;
            return new JavaScriptSerializer().Serialize(Clean(raw));
        }
        catch (Exception) { return "{}"; }
    }

    internal static void Save(Dictionary<string, object> raw)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
            string temp = FilePath + ".tmp";
            File.WriteAllText(temp, new JavaScriptSerializer().Serialize(Clean(raw)));
            if (File.Exists(FilePath)) File.Replace(temp, FilePath, null); else File.Move(temp, FilePath);
        }
        catch (Exception) { /* 偏好写入失败不影响编辑 */ }
    }
}
