using System;
using System.Windows.Forms;

internal static class EngineIntegration
{
    private static int outcome = 1;
    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        var window = new EditorWindow(new DocumentSession(args[0])) { ShowInTaskbar = false, Opacity = 0 };
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var timer = new Timer { Interval = 100 };
        timer.Tick += delegate {
            if (window.Ready) { Console.WriteLine("PASS: actual prototype reached its trusted page-ready bridge"); outcome = 0; timer.Stop(); Application.ExitThread(); }
            else if (clock.ElapsedMilliseconds > 12000) { Console.Error.WriteLine("Prototype did not reach page-ready bridge"); timer.Stop(); Application.ExitThread(); }
        };
        window.Shown += delegate { timer.Start(); };
        Application.Run(window);
        return outcome;
    }
}
