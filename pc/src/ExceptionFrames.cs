using System.Diagnostics;
using System.Text.RegularExpressions;

namespace DotAbyss.Local.Pc;

internal static class ExceptionFrames
{
    internal static IEnumerable<string> Describe(Exception error, bool reflectionDetails = false)
    {
        for (var depth = 0; error is not null && depth < 16; depth++, error = error.InnerException!)
        {
            yield return $"Exception type[{depth}]: {error.GetType().FullName}";
            if (error is TypeInitializationException initialization)
                yield return "  initialized type: " + Bounded(initialization.TypeName ?? "<unknown>");
            if (reflectionDetails && error is TypeLoadException or MissingMethodException or MissingFieldException
                or ArgumentException or NotSupportedException or InvalidCastException or EntryPointNotFoundException or BadImageFormatException)
                yield return "  reflection detail: " + ReflectionMessage(error.Message);
            foreach (var frame in (new StackTrace(error, true).GetFrames() ?? Array.Empty<StackFrame>()).Take(32))
            {
                var method = frame.GetMethod();
                var name = method?.DeclaringType?.FullName + "." + method?.Name;
                var file = Path.GetFileName(frame.GetFileName());
                yield return string.IsNullOrEmpty(file) ? "  at " + name : $"  at {name} ({file}:{frame.GetFileLineNumber()})";
            }
        }
    }

    private static string Bounded(string value) => value.Replace('\r', ' ').Replace('\n', ' ')[..Math.Min(value.Length, 768)];

    private static string ReflectionMessage(string message)
    {
        if (Regex.IsMatch(message, "token|password|authorization|cookie|secret|credential|https?://", RegexOptions.IgnoreCase))
            return "<sensitive details omitted>";
        return Bounded(message);
    }

    internal static string NativeCancellationMessage(string message)
    {
        var firstLine = message.Split(new[] { '\r', '\n' }, 2)[0];
        if (Regex.IsMatch(firstLine, "token|password|authorization|cookie|secret|credential|https?://", RegexOptions.IgnoreCase))
            return "<sensitive details omitted>";
        return Bounded(firstLine);
    }
}
