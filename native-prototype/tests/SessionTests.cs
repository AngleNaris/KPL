using System;
using System.Collections.Generic;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Text;
using System.Threading;

internal static class SessionTests
{
    private static int assertions;
    private static void Check(bool condition, string message) { assertions++; if (!condition) throw new Exception(message); }
    private static Dictionary<string, object> Clone(string json) { return DocumentSession.Parse(Encoding.UTF8.GetBytes(json)); }
    private static Dictionary<string, object> Next(DocumentSession session, string value)
    {
        var result = Clone(session.InitialJson); result["title"] = value; result["revision"] = (int)result["revision"] + 1; return result;
    }
    private static void Main(string[] args)
    {
        if (args.Length == 3 && args[0] == "--hold")
        {
            using (var locked = new FileStream(args[1], FileMode.Open, FileAccess.Read, FileShare.None))
            { File.WriteAllText(args[2], "ready"); Thread.Sleep(3000); }
            return;
        }
        string root = Path.GetFullPath(args[0]), file = Path.Combine(root, "01_合成规格_v001_draft.kpldoc");
        try
        {
            var first = new DocumentSession(file);
            string malicious = "中文 </script><script>throw 'not code'</script> 1.9 秒";
            Check(first.Save(Next(first, malicious), null) == "saved", "first save failed");
            var reopened = new DocumentSession(file);
            Check((string)Clone(reopened.InitialJson)["title"] == malicious, "reopen or unicode failed");
            Check(first.Save(Next(first, "再次保存"), null) == "saved", "repeat save failed");
            var current = new DocumentSession(file);
            byte[] before = File.ReadAllBytes(file);
            Check(reopened.Save(Next(reopened, "旧窗口不应覆盖"), null) == "conflict", "stale second session not blocked");
            Check(Convert.ToBase64String(before) == Convert.ToBase64String(File.ReadAllBytes(file)), "conflict changed bytes");
            var rejected = Next(current, "不应写入"); rejected["document_id"] = "different";
            Check(current.Save(rejected, null) == "identity_changed", "identity change allowed");
            rejected = Next(current, "不应写入"); rejected["revision"] = 99;
            Check(current.Save(rejected, null) == "revision_changed", "bad revision allowed");
            var locked = Clone(current.InitialJson); locked["status"] = "final";
            string lockedPath = Path.Combine(root, "02_合成锁定_v001_final.kpldoc");
            File.WriteAllText(lockedPath, JsonTools.Serializer().Serialize(locked), new UTF8Encoding(false));
            var lockedSession = new DocumentSession(lockedPath);
            Check(lockedSession.Save(Next(lockedSession, "locked"), null) == "locked", "final changed");
            File.SetAttributes(file, File.GetAttributes(file) | FileAttributes.ReadOnly);
            try { Check(current.Save(Next(current, "只读"), null) == "read_only", "readonly changed"); }
            finally { File.SetAttributes(file, File.GetAttributes(file) & ~FileAttributes.ReadOnly); }
            before = File.ReadAllBytes(file);
            Check(current.Save(Next(current, "中断前"), step => { if (step == "staged") throw new IOException("test"); }) == "io_error", "staged interruption status");
            Check(Convert.ToBase64String(before) == Convert.ToBase64String(File.ReadAllBytes(file)), "staged interruption changed target");
            Check(current.Save(Next(current, "中断后"), step => { if (step == "replaced") throw new IOException("test"); }) == "write_unconfirmed", "post-replace interruption status");
            Check((string)Clone(new DocumentSession(file).InitialJson)["title"] == "中断后", "post-replace outcome not inspectable");
            Check(Directory.GetFiles(root, ".kpl-save-*.tmp").Length == 0, "temporary save not cleaned");
            var separate = new DocumentSession(file);
            string ready = Path.Combine(root, "lock-ready");
            var info = new ProcessStartInfo(Assembly.GetExecutingAssembly().Location,
                "--hold \"" + file + "\" \"" + ready + "\"") { UseShellExecute = false, CreateNoWindow = true };
            using (var other = Process.Start(info))
            {
                var watch = Stopwatch.StartNew();
                while (!File.Exists(ready) && watch.ElapsedMilliseconds < 5000) Thread.Sleep(25);
                Check(File.Exists(ready), "other process did not acquire file lock");
                Check(separate.Save(Next(separate, "外部占用"), null) == "io_error", "external process lock ignored");
                other.WaitForExit(); Check(other.ExitCode == 0, "other process failed");
            }
            string dup = "{\"document_id\":\"first\",\"document_id\":\"second\"}";
            bool duplicateRejected = false; try { DocumentSession.Parse(Encoding.UTF8.GetBytes(dup)); } catch { duplicateRejected = true; }
            Check(duplicateRejected, "duplicate keys accepted");
            var v2 = Clone(current.InitialJson);
            v2["assets"] = new object[] { new Dictionary<string, object> { { "id", "A01" }, { "name", "店员" }, { "path", "../05_参考资产/02_店员_idA01_v001_draft.png" } } };
            v2["shots"] = new object[] { new Dictionary<string, object> {
                { "id", "s1" }, { "number", "S01" }, { "duration_seconds", "2.5" }, { "thumbnail", "" }, { "content", "店员回头。" },
                { "description", "@{店员} 回头。" }, { "shot_scale", "近景" }, { "camera_motion", "固定" }, { "prompt", "{镜头:近景}，@{A01}" }, { "video_prompt", "{动态:@{A01} 回头}" }, { "mark", "done" },
                { "layout_camera", new Dictionary<string, object> { { "type", "linear" }, { "start_crop", new object[] { 0, 0, 1, 1 } },
                    { "end_crop", new object[] { 0.1m, 0.1m, 0.9m, 0.9m } } } } } };
            v2["layout_aspect"] = "2.39:1";
            var fresh = new DocumentSession(file);
            v2["revision"] = (int)Clone(fresh.InitialJson)["revision"] + 1;
            Check(fresh.Save(v2, null) == "saved", "v2 fields not saved");
            var reread = Clone(new DocumentSession(file).InitialJson);
            Check((string)((Dictionary<string, object>)((object[])reread["shots"])[0])["mark"] == "done", "mark lost");
            Check((string)((Dictionary<string, object>)((object[])reread["assets"])[0])["name"] == "店员", "asset lost");
            Check((string)reread["layout_aspect"] == "2.39:1", "aspect lost");
            var camera = (Dictionary<string, object>)((Dictionary<string, object>)((object[])reread["shots"])[0])["layout_camera"];
            Check((string)camera["type"] == "linear" && ((object[])camera["end_crop"]).Length == 4, "layout camera lost");
            foreach (string bad in new[] { "mark:urgent", "extra:action", "asset:path", "camera:range", "camera:static", "aspect:text" })
            {
                var broken = Clone(new DocumentSession(file).InitialJson);
                var badShot = (Dictionary<string, object>)((object[])broken["shots"])[0];
                if (bad == "mark:urgent") badShot["mark"] = "urgent";
                else if (bad == "extra:action") badShot["action"] = "旧字段";
                else if (bad == "camera:range") ((Dictionary<string, object>)badShot["layout_camera"])["start_crop"] = new object[] { 0, 0, 1.2m, 1 };
                else if (bad == "camera:static") ((Dictionary<string, object>)badShot["layout_camera"])["type"] = "static";
                else if (bad == "aspect:text") broken["layout_aspect"] = "16x9";
                else ((Dictionary<string, object>)((object[])broken["assets"])[0])["path"] = "../../secret.png";
                bool refused = false; try { DocumentSession.Validate(broken); } catch (InvalidDataException) { refused = true; }
                Check(refused, "invalid v2 model accepted: " + bad);
            }
            // 时长精度：保存与新文件只接受 0.1 秒；打开旧文件时放宽，交给界面规整后再保存
            foreach (string duration in new[] { "1.25", "1.4166666666666667", "0.05" })
            {
                var model = Clone(new DocumentSession(file).InitialJson);
                ((Dictionary<string, object>)((object[])model["shots"])[0])["duration_seconds"] = duration;
                bool strict = false; try { DocumentSession.Validate(model); } catch (InvalidDataException) { strict = true; }
                Check(strict, "strict validation accepted duration " + duration);
                bool lenient = true; try { DocumentSession.Validate(model, false); } catch (InvalidDataException) { lenient = false; }
                Check(lenient, "lenient open rejected duration " + duration);
            }
            string legacy = Path.Combine(root, "04_旧时长_v001_draft.kpldoc");
            var old = Clone(new DocumentSession(file).InitialJson);
            ((Dictionary<string, object>)((object[])old["shots"])[0])["duration_seconds"] = "1.4166666666666667";
            File.WriteAllText(legacy, JsonTools.Serializer().Serialize(old), new UTF8Encoding(false));
            var legacySession = new DocumentSession(legacy);
            bool unsavedRejected = false; try { legacySession.Save(Next(legacySession, "原样保存"), null); } catch (InvalidDataException) { unsavedRejected = true; }
            Check(unsavedRejected, "saving an unnormalized duration was allowed");
            var fixedModel = Next(legacySession, "规整后保存");
            ((Dictionary<string, object>)((object[])fixedModel["shots"])[0])["duration_seconds"] = "1.4";
            Check(legacySession.Save(fixedModel, null) == "saved", "normalized duration not saved");
            Check((string)((Dictionary<string, object>)((object[])Clone(new DocumentSession(legacy).InitialJson)["shots"])[0])["duration_seconds"] == "1.4", "normalized duration lost");
            string extra = Path.Combine(root, "03_伪文件_v001_draft.kpldoc");
            File.WriteAllText(extra, "{\"schema\":\"kpl-user-spec-v2\",\"evil\":\"x\"}");
            bool invalidRejected = false; try { new DocumentSession(extra); } catch { invalidRejected = true; }
            Check(invalidRejected, "invalid model accepted");
            Console.WriteLine("PASS: " + assertions + " actual disk / validation assertions");
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); Environment.ExitCode = 1; }
    }
}
