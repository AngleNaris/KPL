using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

/* 导出视频：用系统里已安装的 ffmpeg 按已保存的时间线合成，不随包附带、不自动下载。
   画面：主轨按镜头顺序排，镜头没关联视频时用参考画面，都没有为黑场；自由视频轨按编号依次叠在上面（编号大的在最上）。
   声音：主轨与自由视频轨里开着声音的片段、各音频轨，按轨道音量混在一起。隐藏或静音的轨道不参与。
   先写到同目录的临时文件，成功后才改成目标文件名；取消或失败时删掉临时文件，不留半截输出。 */
internal sealed class ExportSegment
{
    internal string Source;
    internal bool Image;
    internal double Start, In, Length, Gain = 1;
}

internal sealed class ExportPlan
{
    internal readonly List<ExportSegment> Video = new List<ExportSegment>(), Audio = new List<ExportSegment>();
    internal readonly List<string> Missing = new List<string>();
    internal double Total;
}

internal sealed partial class EditorWindow
{
    private static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;
    private Process exportJob;
    private bool exportCancelled;

    /* 按文档整理出要合成的画面与声音片段（单位秒）；找不到的素材记在 Missing。image 把参考画面路径解析成文件。 */
    internal static ExportPlan PlanExport(Dictionary<string, object> model, string specDir, Func<string, string> image)
    {
        var plan = new ExportPlan();
        object raw;
        var timeline = model.TryGetValue("timeline", out raw) ? raw as Dictionary<string, object> : null;
        object[] videoTracks = timeline != null ? (object[])timeline["video"] : new object[0];
        var main = videoTracks.Length > 0 ? (Dictionary<string, object>)videoTracks[0] : null;
        var mainClips = new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
        if (main != null) foreach (object item in (object[])main["clips"])
        {
            var clip = (Dictionary<string, object>)item;
            mainClips[(string)clip["shot_id"]] = clip;
        }
        double at = 0;
        foreach (object item in (object[])model["shots"])
        {
            var shot = (Dictionary<string, object>)item;
            double length;
            if (!Double.TryParse(((string)shot["duration_seconds"]).Trim(), NumberStyles.AllowDecimalPoint, Invariant, out length) || length <= 0) continue;
            Dictionary<string, object> clip;
            if (mainClips.TryGetValue((string)shot["id"], out clip))
            {
                double inPoint = Seconds(clip["in_us"]);
                AddMedia(plan, specDir, (string)clip["source"], at, inPoint, Math.Min(Seconds(clip["out_us"]) - inPoint, length),
                    Gain(main), !Flag(main, "hidden"), (bool)clip["audio"] && !Flag(main, "muted"));
            }
            else if (((string)shot["thumbnail"]).Length > 0 && !Flag(main, "hidden"))
            {
                string full = image((string)shot["thumbnail"]);
                if (full == null) plan.Missing.Add((string)shot["thumbnail"]);
                else plan.Video.Add(new ExportSegment { Source = full, Image = true, Start = at, Length = length });
            }
            at += length;
        }
        plan.Total = at;
        for (int i = 1; i < videoTracks.Length; i++)
        {
            var track = (Dictionary<string, object>)videoTracks[i];
            foreach (object item in (object[])track["clips"])
            {
                var clip = (Dictionary<string, object>)item;
                double inPoint = Seconds(clip["in_us"]);
                AddMedia(plan, specDir, (string)clip["source"], Seconds(clip["start_us"]), inPoint, Seconds(clip["out_us"]) - inPoint,
                    Gain(track), !Flag(track, "hidden"), (bool)clip["audio"] && !Flag(track, "muted"));
            }
        }
        if (timeline != null) foreach (object entry in (object[])timeline["audio"])
        {
            var track = (Dictionary<string, object>)entry;
            foreach (object item in (object[])track["clips"])
            {
                var clip = (Dictionary<string, object>)item;
                double inPoint = Seconds(clip["in_us"]);
                AddMedia(plan, specDir, (string)clip["source"], Seconds(clip["start_us"]), inPoint, Seconds(clip["out_us"]) - inPoint,
                    Gain(track), false, !Flag(track, "muted"));
            }
        }
        foreach (ExportSegment segment in plan.Video) plan.Total = Math.Max(plan.Total, segment.Start + segment.Length);
        foreach (ExportSegment segment in plan.Audio) plan.Total = Math.Max(plan.Total, segment.Start + segment.Length);
        return plan;
    }

    private static void AddMedia(ExportPlan plan, string specDir, string source, double start, double inPoint, double length,
        double gain, bool video, bool audio)
    {
        if ((!video && !audio) || length <= 0) return;
        string full = ResolveMedia(specDir, source);
        if (full == null) { plan.Missing.Add(source); return; }
        if (video) plan.Video.Add(new ExportSegment { Source = full, Start = start, In = inPoint, Length = length });
        if (audio) plan.Audio.Add(new ExportSegment { Source = full, Start = start, In = inPoint, Length = length, Gain = gain });
    }

    private static double Seconds(object us) { return Convert.ToInt64(us, Invariant) / 1000000.0; }
    private static bool Flag(Dictionary<string, object> track, string key) { object value; return track != null && track.TryGetValue(key, out value) && value is bool && (bool)value; }
    private static double Gain(Dictionary<string, object> track) { object value; return track != null && track.TryGetValue("gain", out value) ? Convert.ToDouble(value, Invariant) : 1; }
    private static string Num(double value) { return value.ToString("0.######", Invariant); }

    /* ffmpeg 的输入参数与滤镜图。画面先铺满时长的黑场，再按顺序把每段缩放留边后叠上去；声音各自对齐到起点后混合。
       混合后的时间戳不一定从 0 开始（WAV 不记时间戳，开头的静音会被吃掉），按样本数重排到 0 起。 */
    internal static string BuildGraph(ExportPlan plan, int width, int height, string fps, bool withVideo, List<string> inputs)
    {
        var graph = new StringBuilder();
        int index = 0;
        string size = width + ":" + height, total = Num(plan.Total);
        if (withVideo)
        {
            graph.Append("color=c=black:s=" + width + "x" + height + ":r=" + fps + ":d=" + total + "[base];");
            string last = "base";
            for (int i = 0; i < plan.Video.Count; i++, index++)
            {
                ExportSegment segment = plan.Video[i];
                if (segment.Image) inputs.AddRange(new[] { "-loop", "1", "-framerate", fps, "-t", Num(segment.Length), "-i", segment.Source });
                else inputs.AddRange(new[] { "-ss", Num(segment.In), "-t", Num(segment.Length), "-i", segment.Source });
                graph.Append("[" + index + ":v]scale=" + size + ":force_original_aspect_ratio=decrease,pad=" + size + ":(ow-iw)/2:(oh-ih)/2,setsar=1,fps=" + fps
                    + ",setpts=PTS-STARTPTS+" + Num(segment.Start) + "/TB[s" + i + "];[" + last + "][s" + i + "]overlay=eof_action=pass[o" + i + "];");
                last = "o" + i;
            }
            graph.Append("[" + last + "]null[vout];");
        }
        var mixed = new StringBuilder();
        for (int i = 0; i < plan.Audio.Count; i++, index++)
        {
            ExportSegment segment = plan.Audio[i];
            inputs.AddRange(new[] { "-ss", Num(segment.In), "-t", Num(segment.Length), "-i", segment.Source });
            graph.Append("[" + index + ":a]asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo,volume=" + Num(segment.Gain)
                + ",adelay=" + Math.Round(segment.Start * 1000) + ":all=1[a" + i + "];");
            mixed.Append("[a" + i + "]");
        }
        if (plan.Audio.Count > 0)
            graph.Append(mixed + "amix=inputs=" + plan.Audio.Count + ":normalize=0:dropout_transition=0,asetpts=N/SR/TB,apad,atrim=0:" + total + "[aout]");
        else
            graph.Append("anullsrc=r=48000:cl=stereo,atrim=0:" + total + "[aout]");
        return graph.ToString();
    }

    private static string FindTool(string name)
    {
        foreach (string folder in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            try
            {
                string full = Path.Combine(folder.Trim().Trim('"'), name);
                if (folder.Trim().Length > 0 && File.Exists(full)) return full;
            }
            catch (ArgumentException) { /* PATH 里的无效项 */ }
        }
        return null;
    }

    /* 视频文件不一定有声音轨；没有的不参与混音，否则 ffmpeg 会因为找不到音频流而失败。 */
    private static bool HasAudio(string ffprobe, string file)
    {
        try
        {
            var info = new ProcessStartInfo(ffprobe, "-v error -select_streams a -show_entries stream=index -of csv=p=0 " + Quote(file))
            { UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true, StandardOutputEncoding = Encoding.UTF8 };
            using (Process process = Process.Start(info))
            {
                string output = process.StandardOutput.ReadToEnd();
                process.WaitForExit();
                return output.Trim().Length > 0;
            }
        }
        catch (Exception) { return false; }
    }

    /* 按 Windows 命令行规则给参数加引号：引号前的反斜杠要成对。 */
    private static string Quote(string arg)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes).Append(c);
            slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    /* 页面发来格式、分辨率与帧率；选好位置后在后台合成，进度与结果都发回页面。 */
    private void StartVideoExport(Dictionary<string, object> message)
    {
        if (exportJob != null) { PostVideo("busy", null, null); return; }
        object format, width, height, fps;
        message.TryGetValue("format", out format); message.TryGetValue("width", out width);
        message.TryGetValue("height", out height); message.TryGetValue("fps", out fps);
        string kind = format as string, rate = fps as string;
        bool audioOnly = kind == "wav";
        int w = width is int ? (int)width : 0, h = height is int ? (int)height : 0;
        if (!(kind == "mp4" || kind == "mov" || audioOnly)
            || (!audioOnly && (w < 16 || h < 16 || w > 8192 || h > 8192 || w % 2 != 0 || h % 2 != 0 || rate == null || !Regex.IsMatch(rate, @"^[1-9][0-9]{0,4}(/[1-9][0-9]{0,4})?$"))))
        { PostVideo("invalid", null, null); return; }
        string ffmpeg = FindTool("ffmpeg.exe"), ffprobe = FindTool("ffprobe.exe");
        if (ffmpeg == null || ffprobe == null) { PostVideo("ffmpeg_missing", null, null); return; }
        string specDir = Path.GetDirectoryName(session.FilePath);
        var model = JsonTools.Serializer().DeserializeObject(session.InitialJson) as Dictionary<string, object>;
        ExportPlan plan = PlanExport(model, specDir, ResolveAsset);
        if (plan.Missing.Count > 0) { PostVideo("missing", null, String.Join("、", plan.Missing.ToArray())); return; }
        if (plan.Total <= 0) { PostVideo("empty", null, null); return; }
        string target = PickVideoTarget(kind);
        if (target == null) { PostVideo("cancelled", null, null); return; }
        plan.Audio.RemoveAll(segment => !HasAudio(ffprobe, segment.Source));

        var inputs = new List<string>();
        string graph = BuildGraph(plan, w, h, rate, !audioOnly, inputs);
        string temp = Path.Combine(Path.GetDirectoryName(target), ".kpl-export-" + Guid.NewGuid().ToString("N") + Path.GetExtension(target));
        string script = Path.Combine(Path.GetTempPath(), "kpl-graph-" + Guid.NewGuid().ToString("N") + ".txt");
        File.WriteAllText(script, graph, new UTF8Encoding(false));
        var args = new List<string> { "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1" };
        args.AddRange(inputs);
        args.AddRange(new[] { "-filter_complex_script", script });
        if (!audioOnly) args.AddRange(new[] { "-map", "[vout]" });
        args.AddRange(new[] { "-map", "[aout]", "-t", Num(plan.Total) });
        if (kind == "mp4") args.AddRange(new[] { "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart" });
        else if (kind == "mov") args.AddRange(new[] { "-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le" });
        else args.AddRange(new[] { "-c:a", "pcm_s16le" });
        args.Add(temp);

        var startInfo = new ProcessStartInfo(ffmpeg, String.Join(" ", args.ConvertAll(Quote).ToArray()))
        {
            UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
        };
        exportCancelled = false;
        try { exportJob = Process.Start(startInfo); }
        catch (Exception) { File.Delete(script); PostVideo("failed", null, "无法启动 ffmpeg。"); return; }
        Process job = exportJob;
        double total = plan.Total;
        PostVideo("started", Path.GetFileName(target), null);
        Task<string> errors = job.StandardError.ReadToEndAsync();
        Task.Run(() =>
        {
            string line;
            var tick = Stopwatch.StartNew();
            while ((line = job.StandardOutput.ReadLine()) != null)
            {
                if (!line.StartsWith("out_time_us=", StringComparison.Ordinal) || tick.ElapsedMilliseconds < 300) continue;
                long us;
                if (!Int64.TryParse(line.Substring(12), out us)) continue;
                tick.Restart();
                PostUi(new Dictionary<string, object> { { "type", "exportResult" }, { "kind", "video" }, { "status", "progress" },
                    { "percent", Math.Max(0, Math.Min(99, (int)(us / 1000000.0 / total * 100))) } });
            }
            job.WaitForExit();
            string error = errors.Result.Trim();
            string status = "failed", detail = error.Length > 300 ? error.Substring(error.Length - 300) : error;
            try
            {
                if (exportCancelled) status = "cancelled";
                else if (job.ExitCode == 0 && File.Exists(temp))
                {
                    if (File.Exists(target)) File.Delete(target);
                    File.Move(temp, target);
                    status = "done";
                }
            }
            catch (Exception ex) { detail = ex.Message; }
            finally
            {
                try { if (File.Exists(temp)) File.Delete(temp); } catch (Exception) { }
                try { File.Delete(script); } catch (Exception) { }
            }
            PostUi(new Dictionary<string, object> { { "type", "exportResult" }, { "kind", "video" }, { "status", status },
                { "path", Path.GetFileName(target) }, { "detail", status == "failed" ? detail : "" } });
            try { BeginInvoke((Action)(() => { exportJob = null; job.Dispose(); })); }
            catch (Exception) { /* 窗口已关闭 */ }
        });
    }

    private void CancelVideoExport()
    {
        if (exportJob == null) return;
        exportCancelled = true;
        try { exportJob.Kill(); } catch (Exception) { /* 已经结束 */ }
    }

    /* 默认放在 07_预演交付，按项目命名规则取下一个空着的版本号；用户可以改位置和名字。 */
    private string PickVideoTarget(string kind)
    {
        string specDir = Path.GetDirectoryName(session.FilePath);
        string folder = Path.GetFullPath(Path.Combine(specDir, "..", "07_预演交付"));
        if (!Directory.Exists(folder)) folder = specDir;
        int version = 1;
        while (version < 999 && File.Exists(Path.Combine(folder, "01_成片导出_v" + version.ToString("000") + "_draft." + kind))) version++;
        string filter = kind == "mp4" ? "MP4 视频 (*.mp4)|*.mp4" : kind == "mov" ? "MOV 视频 (*.mov)|*.mov" : "WAV 音频 (*.wav)|*.wav";
        using (var dialog = new SaveFileDialog
        {
            Title = "导出视频", Filter = filter, DefaultExt = kind, AddExtension = true, OverwritePrompt = true,
            InitialDirectory = folder, FileName = "01_成片导出_v" + version.ToString("000") + "_draft." + kind
        })
        {
            if (dialog.ShowDialog(this) != DialogResult.OK) return null;
            string target = Path.GetFullPath(dialog.FileName);
            // 不覆盖规格文件
            return String.Equals(target, session.FilePath, StringComparison.OrdinalIgnoreCase) ? null : target;
        }
    }

    private void PostVideo(string status, string path, string detail)
    {
        PostRaw(new Dictionary<string, object> { { "type", "exportResult" }, { "kind", "video" }, { "status", status },
            { "path", path ?? "" }, { "detail", detail ?? "" } });
    }
}
