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

            // 媒体路径：相对 .kpldoc 所在目录，统一用 /；不同盘记绝对路径；只解析白名单格式的普通文件
            string docDir = Path.Combine(root, "项目", "01_项目说明"), mediaDir = Path.Combine(root, "项目", "07_预演交付");
            Directory.CreateDirectory(docDir); Directory.CreateDirectory(mediaDir);
            string clip = Path.Combine(mediaDir, "S01 #1.mp4"), text = Path.Combine(mediaDir, "说明.txt");
            File.WriteAllText(clip, "x"); File.WriteAllText(text, "x");
            Check(EditorWindow.RelativeSource(docDir, clip) == "../07_预演交付/S01 #1.mp4", "relative media path " + EditorWindow.RelativeSource(docDir, clip));
            Check(EditorWindow.RelativeSource(docDir, Path.Combine(docDir, "a.wav")) == "a.wav", "same-folder media path");
            Check(EditorWindow.RelativeSource(@"C:\项目", @"Z:\素材\a.mov") == "Z:/素材/a.mov", "other-drive media path not absolute");
            Check(EditorWindow.ResolveMedia(docDir, "../07_预演交付/S01 #1.mp4") == clip, "relative media not resolved");
            Check(EditorWindow.ResolveMedia(docDir, clip.Replace('\\', '/')) == clip, "absolute media not resolved");
            Check(EditorWindow.ResolveMedia(docDir, "../07_预演交付/说明.txt") == null, "non-media file resolved");
            Check(EditorWindow.ResolveMedia(docDir, "../07_预演交付/缺失.mp4") == null, "missing media resolved");
            Check(EditorWindow.ResolveMedia(docDir, "..\\07_预演交付\\S01 #1.mp4") == null, "backslash source accepted");
            Check(EditorWindow.MediaPath(@"D:\素材 A\S01 #1.mp4") == "https://drive-d.kpl-media.local/%E7%B4%A0%E6%9D%90%20A/S01%20%231.mp4",
                "media url " + EditorWindow.MediaPath(@"D:\素材 A\S01 #1.mp4"));

            // 导出视频：镜头按顺序排；有视频的用视频，否则用参考画面；静音轨不进混音；声音时间戳归零后再补齐总长
            var model = (Dictionary<string, object>)JsonTools.Serializer().DeserializeObject(
                "{\"shots\":[{\"id\":\"S01\",\"duration_seconds\":\"2\",\"thumbnail\":\"a.png\"},{\"id\":\"S02\",\"duration_seconds\":\"3\",\"thumbnail\":\"\"}," +
                "{\"id\":\"S03\",\"duration_seconds\":\"\",\"thumbnail\":\"a.png\"}]," +
                "\"timeline\":{\"video\":[{\"id\":\"V1\",\"clips\":[{\"shot_id\":\"S02\",\"source\":\"../07_预演交付/S01 #1.mp4\",\"in_us\":500000,\"out_us\":9000000,\"audio\":true}]}]," +
                "\"audio\":[{\"id\":\"A1\",\"muted\":true,\"gain\":1,\"clips\":[{\"source\":\"../07_预演交付/S01 #1.mp4\",\"start_us\":0,\"in_us\":0,\"out_us\":1000000}]}]}}");
            ExportPlan plan = EditorWindow.PlanExport(model, docDir, path => path == "a.png" ? source : null);
            Check(plan.Missing.Count == 0 && plan.Total == 5, "export total " + plan.Total);
            Check(plan.Video.Count == 2 && plan.Video[0].Image && plan.Video[1].Start == 2 && plan.Video[1].In == 0.5 && plan.Video[1].Length == 3,
                "export video segments");
            Check(plan.Audio.Count == 1 && plan.Audio[0].Start == 2, "muted track mixed or shot audio misplaced");
            var inputs = new List<string>();
            string graph = EditorWindow.BuildGraph(plan, 1280, 720, "25", true, inputs);
            Check(inputs.Count == 8 + 6 + 6 && graph.Contains("asetpts=N/SR/TB,apad,atrim=0:5[aout]") && graph.Contains("adelay=2000:all=1"),
                "export graph " + graph);

            Console.WriteLine("PASS: " + assertions + " thumbnail / prefs / media path / export assertions");
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); return 1; }
    }
}
