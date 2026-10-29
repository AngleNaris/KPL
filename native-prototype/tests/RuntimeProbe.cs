using System;
using System.IO;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;

internal static class RuntimeProbe
{
    private static int outcome = 1;
    private static string stage = "starting";
    [STAThread]
    private static int Main()
    {
        var form = new Form { ShowInTaskbar = false, WindowState = FormWindowState.Normal, Opacity = 0 };
        CoreWebView2Controller controller = null;
        var timeout = new Timer { Interval = 15000 };
        timeout.Tick += delegate { Console.Error.WriteLine("WebView2 probe timed out at: " + stage); timeout.Stop(); form.Close(); };
        form.Shown += async delegate {
            timeout.Start();
            try
            {
                string profile = Path.Combine(Path.GetTempPath(), "kpl-probe-" + Guid.NewGuid().ToString("N"));
                stage = "CreateAsync";
                var environment = await CoreWebView2Environment.CreateAsync(null, profile, null);
                Console.WriteLine("Runtime: " + environment.BrowserVersionString);
                stage = "CreateCoreWebView2ControllerAsync";
                controller = await environment.CreateCoreWebView2ControllerAsync(form.Handle);
                stage = "local page message";
                var view = controller.CoreWebView2;
                view.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { Console.WriteLine("Navigation: " + e.Uri); };
                view.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e) { Console.WriteLine("Navigation completed: " + e.IsSuccess + " / " + e.WebErrorStatus); };
                view.ProcessFailed += delegate(object sender, CoreWebView2ProcessFailedEventArgs e) { Console.WriteLine("Process failed: " + e.ProcessFailedKind); };
                view.WebMessageReceived += delegate(object sender, CoreWebView2WebMessageReceivedEventArgs e) {
                    if (e.Source == "about:blank" && e.TryGetWebMessageAsString() == "probe-ready")
                    { Console.WriteLine("PASS: WebView2 runtime created and executed local test content"); outcome = 0; }
                    timeout.Stop(); form.Close();
                };
                view.NavigateToString("<!doctype html><meta charset=utf-8><script>window.chrome.webview.postMessage('probe-ready')</script>");
            }
            catch (Exception ex) { Console.Error.WriteLine("WebView2 probe failed: " + ex.Message); timeout.Stop(); form.Close(); }
        };
        form.FormClosed += delegate { if (controller != null) controller.Close(); };
        Application.Run(form);
        return outcome;
    }
}
