using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

/* 字幕解析：ASS 卡拉 OK 逐字、LRC 逐句与 <mm:ss.xx>、SRT、GB18030 与常见错误。 */
internal static class SubtitleTests
{
    private static int assertions;
    private static void Check(bool condition, string message) { assertions++; if (!condition) throw new Exception(message); }

    private static string Write(string dir, string name, string text, Encoding encoding)
    {
        string path = Path.Combine(dir, name);
        File.WriteAllText(path, text, encoding);
        return path;
    }

    private static string Ass(string dialogue)
    {
        return "[Script Info]\nTitle: 合成\n\n[Events]\nFormat: Layer, Start, End, Style, Name, Effect, Text\n" + dialogue + "\n";
    }

    private static int Main(string[] args)
    {
        string root = Path.GetFullPath(args[0]);
        try
        {
            // ASS：{\k} 标签给出逐段时长，文字按标签切分
            var ass = Subtitle.Read(Write(root, "合成.ass", Ass(
                "Dialogue: 0,0:00:42.02,0:00:44.48,Default,,0,{\\k50}长风{\\k45}卷起了{\\k61}漫天狂沙"), new UTF8Encoding(false)));
            Check(ass.Count == 1 && ass[0].Start == 42020 && ass[0].End == 44480, "ass cue range");
            Check(ass[0].Text == "长风卷起了漫天狂沙", "ass text: " + ass[0].Text);
            Check(ass[0].Words.Count == 3 && ass[0].Words[0].Text == "长风" && ass[0].Words[2].Text == "漫天狂沙", "ass karaoke split");
            Check(ass[0].Words[0].Start == 42020 && ass[0].Words.Last().End == 44480, "ass karaoke range");
            Check(ass[0].Words.SequenceEqual(ass[0].Words.OrderBy(w => w.Start)), "ass karaoke order");

            // 只有标签没有逐段文字：整句按字数与时长比例切分
            var plainAss = Subtitle.Read(Write(root, "纯标签.ass", Ass(
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,{\\kf50}一二三四五六七八"), new UTF8Encoding(false)));
            Check(plainAss[0].Words.Count == 1 && plainAss[0].Words[0].Text == "一二三四五六七八", "ass plain karaoke");

            // 样式标签、换行与空格
            var styled = Subtitle.Read(Write(root, "样式.ass", Ass(
                "Dialogue: 0,0:00:01.00,0:00:02.00,Default,歌手,0,{\\pos(960,1040)\\fad(200,200)\\c&HFFFFFF&}第一句\\N第二句"), new UTF8Encoding(false)));
            Check(styled[0].Text == "第一句 第二句", "ass style tags: " + styled[0].Text);

            // LRC：逐句时间、offset、一行多时间戳
            var lrc = Subtitle.Read(Write(root, "歌.lrc", "[ti: 合成]\n[offset:500]\n[00:01.00][00:10.00]第一句\n[00:03.25]第二句\n", new UTF8Encoding(false)));
            Check(lrc.Count == 3, "lrc cue count " + lrc.Count);
            Check(lrc[0].Start == 1500 && lrc[0].Text == "第一句", "lrc offset");
            Check(lrc[1].Start == 3750 && lrc[1].Text == "第二句", "lrc second line");
            Check(lrc[2].Start == 10500 && lrc[2].Text == "第一句", "lrc multi-stamp");

            // LRC 逐字：<mm:ss.xx> 标在每段之前
            var timed = Subtitle.Read(Write(root, "逐字.lrc", "[00:01.00]<00:01.00>长<00:01.40>风<00:01.80>起\n", new UTF8Encoding(false)));
            Check(timed.Count == 1 && timed[0].Words.Count == 3, "lrc word count " + timed[0].Words.Count);
            Check(timed[0].Words[0].Text == "长" && timed[0].Words[1].Start == 1400, "lrc word timing");

            // SRT：编号、时间行、<i> 标签
            var srt = Subtitle.Read(Write(root, "字幕.srt", "1\n00:00:01,000 --> 00:00:02,500\n<i>第一句</i>\n\n2\n00:00:03,000 --> 00:00:04,000\n第二句\n换行\n", new UTF8Encoding(false)));
            Check(srt.Count == 2 && srt[0].Text == "第一句" && srt[0].End == 2500, "srt parse");
            Check(srt[1].Text == "第二句\n换行", "srt multiline");

            // GB18030 编码的 SRT（无 BOM）
            var gbk = Subtitle.Read(Write(root, "国标.srt", "1\n00:00:01,000 --> 00:00:02,000\n中文歌词\n", Encoding.GetEncoding(54936)));
            Check(gbk[0].Text == "中文歌词", "gb18030 text");

            // 错误：空字幕、结束早于开始、不支持的格式
            bool refused = false;
            try { Subtitle.Read(Write(root, "空.srt", "没有任何时间\n", new UTF8Encoding(false))); } catch (InvalidDataException) { refused = true; }
            Check(refused, "empty subtitle accepted");
            refused = false;
            try { Subtitle.Read(Write(root, "倒序.srt", "1\n00:00:05,000 --> 00:00:02,000\n倒序\n", new UTF8Encoding(false))); } catch (InvalidDataException) { refused = true; }
            Check(refused, "reversed cue accepted");
            refused = false;
            try { Subtitle.Read(Write(root, "表格.vtt", "WEBVTT\n\n00:01.000 --> 00:02.000\nx\n", new UTF8Encoding(false))); } catch (InvalidDataException) { refused = true; }
            Check(refused, "unsupported extension accepted");

            Console.WriteLine("PASS: " + assertions + " subtitle assertions");
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
