using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

/* 导出 HTML：与编辑器相同界面的只读页面，交给没有编辑器的同事用浏览器查看。
   内容是已保存的文档；图片与视频按相对路径读项目里的原文件，所以要连同项目文件夹一起发给对方。 */
internal sealed partial class EditorWindow
{
    // 导出页面在浏览器里用 file:// 打开：允许直接读本地图片与视频，脚本仍不能联网或读取文件内容
    private const string ExportCsp = "default-src 'none'; img-src file: data: 'self'; media-src file: 'self'; style-src 'unsafe-inline'; " +
        "script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; object-src 'none'";
    private const string ExportMarker = "<script type=\"application/json\" id=\"kpl-export\">null</script>";
    private const string ViewOnlyMeta = "<meta name=\"kpl-view-only\" content=\"true\">";

    private Dictionary<string, object> ExportHtml()
    {
        string specDir = Path.GetDirectoryName(session.FilePath), target;
        using (var dialog = new SaveFileDialog
        {
            Title = "导出 HTML", Filter = "网页 (*.html)|*.html", DefaultExt = "html", AddExtension = true,
            InitialDirectory = specDir, FileName = "99_交接查看.html", OverwritePrompt = true
        })
        {
            if (dialog.ShowDialog(this) != DialogResult.OK) return Result("cancelled", null);
            target = Path.GetFullPath(dialog.FileName);
        }
        if (!String.Equals(Path.GetExtension(target), ".html", StringComparison.OrdinalIgnoreCase)) return Result("invalid_target", target);
        try
        {
            // 只覆盖以前导出的只读页面，不覆盖 HTML 规格或其他网页
            if (File.Exists(target) && File.ReadAllText(target, Encoding.UTF8).IndexOf(ViewOnlyMeta, StringComparison.Ordinal) < 0)
                return Result("not_overwritten", target);
            string html = Render(session.InitialJson, Prefs.Load());
            html = new Regex("<meta http-equiv=\"Content-Security-Policy\" content=\"[^\"]*\">")
                .Replace(html, match => "<meta http-equiv=\"Content-Security-Policy\" content=\"" + ExportCsp + "\">", 1);
            html = ReplaceOnce(html, "<meta charset=\"utf-8\">", "<meta charset=\"utf-8\">" + ViewOnlyMeta);
            var info = new Dictionary<string, object> { { "base", RelativeUrl(Path.GetDirectoryName(target), specDir) } };
            html = ReplaceOnce(html, ExportMarker, "<script type=\"application/json\" id=\"kpl-export\">"
                + ScriptJson(JsonTools.Serializer().Serialize(info)) + "</script>");
            File.WriteAllText(target, html, new UTF8Encoding(false));
            return Result("exported", target);
        }
        catch (IOException) { return Result("write_failed", target); }
        catch (UnauthorizedAccessException) { return Result("write_failed", target); }
    }

    private static Dictionary<string, object> Result(string status, string path)
    {
        return new Dictionary<string, object> { { "status", status }, { "path", path == null ? "" : Path.GetFileName(path) } };
    }

    /* 从导出文件所在目录到规格所在目录的相对地址（已转义，结尾带 /）；不在同一盘时是 file:/// 绝对地址。 */
    private static string RelativeUrl(string from, string to)
    {
        var fromUri = new Uri(from.TrimEnd('\\') + "\\");
        var toUri = new Uri(to.TrimEnd('\\') + "\\");
        Uri relative = fromUri.MakeRelativeUri(toUri);
        return relative.IsAbsoluteUri ? relative.AbsoluteUri : relative.OriginalString;
    }
}
