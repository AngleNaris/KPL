using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

// Synthetic cross-process probe; it uses the same mutex naming as DocumentSession.
internal static class MutexProbe
{
    private static int Main(string[] args)
    {
        string path = Path.GetFullPath(args[0]).ToUpperInvariant();
        string digest;
        using (var sha = SHA256.Create())
            digest = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(path))).Replace("-", "").ToLowerInvariant();
        using (var mutex = new Mutex(false, "Local\\KPLDocument-" + digest))
        {
            if (!mutex.WaitOne(TimeSpan.FromSeconds(5))) return 1;
            try { File.WriteAllText(args[1], "ready"); Thread.Sleep(7000); }
            finally { mutex.ReleaseMutex(); }
        }
        return 0;
    }
}
