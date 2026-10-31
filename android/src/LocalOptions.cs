using System.Net;
using System.Text.RegularExpressions;

namespace DotAbyss.Local;

internal sealed class LocalOptions
{
    public bool Enabled { get; set; }
    public bool ObserveOnly { get; set; }
    public bool AllowFarTrampolines { get; set; }
    public bool StandaloneNativeLoginProbe { get; set; }
    public string ServerBaseUrl { get; set; } = "http://127.0.0.1:18081";
    public string Profile { get; set; } = "default";
    public bool IgnoreVersionChecks { get; set; }
    public bool LogProtocolShapes { get; set; } = true;
    public bool CaptureRedactedResponses { get; set; }
    public int TimeoutSeconds { get; set; } = 8;

    internal Uri Validate()
    {
        if (!Uri.TryCreate(ServerBaseUrl, UriKind.Absolute, out var uri)
            || uri.Scheme != Uri.UriSchemeHttp || !string.IsNullOrEmpty(uri.UserInfo)
            || uri.AbsolutePath != "/" || !string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
            throw new InvalidOperationException("ServerBaseUrl must be an HTTP origin without credentials, path, query or fragment.");
        if (!(IPAddress.TryParse(uri.Host, out var address) && IPAddress.IsLoopback(address))
            && !string.Equals(uri.Host, "localhost", StringComparison.OrdinalIgnoreCase)
            && uri.Host != "10.0.2.2")
            throw new InvalidOperationException("Only loopback or Android emulator host 10.0.2.2 is supported. Use adb reverse.");
        if (TimeoutSeconds is < 1 or > 30)
            throw new InvalidOperationException("TimeoutSeconds must be between 1 and 30.");
        if (Profile is null || !Regex.IsMatch(Profile, @"\A[A-Za-z0-9_.-]{1,64}\z"))
            throw new InvalidOperationException("Profile must contain 1 to 64 ASCII letters, digits, underscores, dots or hyphens.");
        return uri;
    }
}
