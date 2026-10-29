using System;
using System.IO;
using System.Text.RegularExpressions;

internal static class TemplateTests
{
    private static int Main(string[] args)
    {
        var session = new DocumentSession(args[0]);
        var data = DocumentSession.Parse(System.Text.Encoding.UTF8.GetBytes(session.InitialJson));
        data["title"] = "</script><script>window.attack=true</script> 中文";
        string html = EditorWindow.Render(JsonTools.Serializer().Serialize(data));
        if (html.Contains("</script><script>window.attack") || !html.Contains("\\u003c/script\\u003e"))
        { Console.Error.WriteLine("FAIL: untrusted text was embedded as markup"); return 1; }
        // 数据、显示偏好、应用脚本各一个边界
        if (Regex.Matches(html, "</script>", RegexOptions.IgnoreCase).Count != 3)
        { Console.Error.WriteLine("FAIL: unexpected executable script boundaries"); return 1; }
        Console.WriteLine("PASS: hostile document text remains JSON data in the trusted page");
        return 0;
    }
}
