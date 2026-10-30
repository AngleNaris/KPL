using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;

/* 宿主中不依赖 WebView2 窗口的部分：缩略图缓存与偏好合并。只写参数给出的临时目录。 */
internal static class HostTests
{
    private static int assertions;
    private static void Check(bool condition, string message) { assertions++; if (!condition) throw new Exception(message); }

    private static int Main(string[] args)
    {
        try
        {
            string root = Path.GetFullPath(args[0]);
            Directory.CreateDirectory(root);

            // 缩略图：缩到 480 宽、保持比例、输出 JPEG，不留临时文件
            string source = Path.Combine(root, "source.png"), thumb = Path.Combine(root, "thumb.jpg");
            using (var image = new Bitmap(1000, 500)) image.Save(source, ImageFormat.Png);
            Check(EditorWindow.MakeThumb(source, thumb), "thumbnail not created");
            using (var small = Image.FromFile(thumb))
            {
                Check(small.Width == 480 && small.Height == 240, "thumbnail size " + small.Width + "x" + small.Height);
                Check(small.RawFormat.Guid == ImageFormat.Jpeg.Guid, "thumbnail is not JPEG");
            }
            string narrow = Path.Combine(root, "narrow.png"), narrowThumb = Path.Combine(root, "narrow.jpg");
            using (var image = new Bitmap(200, 100)) image.Save(narrow, ImageFormat.Png);
            Check(EditorWindow.MakeThumb(narrow, narrowThumb), "small image not thumbnailed");
            using (var small = Image.FromFile(narrowThumb)) Check(small.Width == 200, "small image was enlarged");
            string broken = Path.Combine(root, "broken.png");
            File.WriteAllText(broken, "not an image");
            Check(!EditorWindow.MakeThumb(broken, Path.Combine(root, "broken.jpg")), "undecodable image reported success");
            Check(!File.Exists(Path.Combine(root, "broken.jpg")), "failed thumbnail left a file");
            Check(Directory.GetFiles(root, "*.tmp").Length == 0, "temporary thumbnail file left behind");

            // 缓存名：同一文件稳定；内容变了就换名
            string name = EditorWindow.ThumbName("../05_参考资产/a.png", new FileInfo(source));
            Check(name == EditorWindow.ThumbName("../05_参考资产/a.png", new FileInfo(source)), "thumbnail name not stable");
            Check(name != EditorWindow.ThumbName("../05_参考资产/b.png", new FileInfo(source)), "different paths share a name");
            File.AppendAllText(source, "x");
            File.SetLastWriteTimeUtc(source, DateTime.UtcNow.AddMinutes(1));
            Check(name != EditorWindow.ThumbName("../05_参考资产/a.png", new FileInfo(source)), "changed source kept its name");

            // 偏好：页面写入不能改窗口大小，窗口写入不能抹掉显示偏好；越界值丢弃
            Prefs.FilePath = Path.Combine(root, "prefs", "prefs.json");
            int w, h; bool max;
            Check(!Prefs.Window(out w, out h, out max), "window size reported before any save");
            Prefs.SaveWindow(1600, 900, true);
            Prefs.Save(new Dictionary<string, object> { { "mode", "light" }, { "panelH", 260 }, { "winW", 300 }, { "winMax", false }, { "evil", "x" } });
            Check(Prefs.Window(out w, out h, out max) && w == 1600 && h == 900 && max, "page prefs overwrote the window size");
            string loaded = Prefs.Load();
            Check(loaded.Contains("\"mode\":\"light\"") && loaded.Contains("\"panelH\":260") && !loaded.Contains("evil"), "display prefs lost or unknown key kept: " + loaded);
            Prefs.SaveWindow(1280, 720, false);
            Check(Prefs.Load().Contains("\"mode\":\"light\""), "window save erased display prefs");
            Check(Prefs.Window(out w, out h, out max) && w == 1280 && h == 720 && !max, "window size not updated");
            Prefs.SaveWindow(50, 99999, false);
            Check(!Prefs.Window(out w, out h, out max), "out-of-range window size accepted");

            Console.WriteLine("PASS: " + assertions + " thumbnail / prefs assertions");
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); return 1; }
    }
}
