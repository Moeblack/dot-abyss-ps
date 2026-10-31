using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Globalization;
using System.Diagnostics;

namespace DotAbyss.Local;

internal static partial class ProtocolLog
{
    internal static string ExceptionFrames(Exception exception)
    {
        try
        {
            var failures = new List<string>();
            for (Exception? current = exception; current is not null && failures.Count < 4; current = current.InnerException)
            {
                var frames = new StackTrace(current, false).GetFrames().Take(12).Select(frame =>
                {
                    var method = frame.GetMethod();
                    return $"{method?.DeclaringType?.FullName ?? "<dynamic>"}.{method?.Name ?? "<unknown>"}@IL:{frame.GetILOffset()}";
                });
                failures.Add($"{current.GetType().Name}[{string.Join(" -> ", frames)}]");
            }
            return string.Join("; inner=", failures);
        }
        catch { return exception.GetType().Name; }
    }

    internal static string StringShape(string? value)
    {
        if (value is null) return "null";
        var date = DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture,
            DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal, out _);
        return $"string(length={value.Length}, parses_as_datetime={date})";
    }

    internal static string SafeUrl(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri)) return "<invalid-url>";
        var path = IdentifierSegment().Replace(uri.AbsolutePath, "/<id>");
        return $"{uri.Scheme}://{uri.Authority}{path}{(uri.Query.Length == 0 ? "" : "?<redacted>")}";
    }

    internal static string Shape(string? body)
    {
        if (string.IsNullOrEmpty(body)) return "empty";
        if (body.Length > 65536) return $"body(length={body.Length}, omitted)";
        try
        {
            using var document = JsonDocument.Parse(body, new JsonDocumentOptions { MaxDepth = 32 });
            return Describe(document.RootElement, 0);
        }
        catch (JsonException)
        {
            return $"non-json(length={body.Length}, omitted)";
        }
    }

    private static string Describe(JsonElement element, int depth)
    {
        if (depth >= 3) return element.ValueKind.ToString();
        return element.ValueKind switch
        {
            JsonValueKind.Object => "{" + string.Join(",", element.EnumerateObject().Take(32)
                .Select(property => $"{SafeKey(property.Name)}:{Describe(property.Value, depth + 1)}")) + "}",
            JsonValueKind.Array => $"array[{element.GetArrayLength()}]",
            _ => element.ValueKind.ToString()
        };
    }

    private static string SafeKey(string key) => SchemaKey().IsMatch(key) ? key : "<key>";

    internal static string? RedactResponse(string? body)
    {
        if (string.IsNullOrWhiteSpace(body) || body.Length > 2 * 1024 * 1024) return null;
        try
        {
            var node = JsonNode.Parse(body, documentOptions: new JsonDocumentOptions { MaxDepth = 64 });
            if (node is JsonValue rootValue && rootValue.TryGetValue<string>(out var rootText)
                && Uri.TryCreate(rootText, UriKind.Absolute, out var rootUri) && rootUri.Scheme is "http" or "https")
                return JsonSerializer.Serialize(SafeUrl(rootText));
            RedactNode(node);
            return node?.ToJsonString(new JsonSerializerOptions { WriteIndented = true });
        }
        catch (JsonException) { return null; }
    }

    private static void RedactNode(JsonNode? node)
    {
        if (node is JsonObject obj)
        {
            foreach (var key in obj.Select(pair => pair.Key).ToArray())
            {
                if (SensitiveKey().IsMatch(key)) obj[key] = "<redacted>";
                else RedactNode(obj[key]);
            }
        }
        else if (node is JsonArray array)
        {
            foreach (var child in array) RedactNode(child);
        }
        else if (node is JsonValue value && value.TryGetValue<string>(out var text)
                 && Uri.TryCreate(text, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
        {
            value.ReplaceWith(SafeUrl(text));
        }
    }

    [GeneratedRegex(@"/[0-9a-fA-F-]{12,}(?=/|$)|/[0-9]{6,}(?=/|$)|/[A-Za-z0-9_%+=-]{32,}(?=/|$)")]
    private static partial Regex IdentifierSegment();

    [GeneratedRegex(@"\A[A-Za-z_][A-Za-z0-9_]{0,63}\z")]
    private static partial Regex SchemaKey();

    [GeneratedRegex("token|session|authorization|cookie|password|secret|credential|uuid|device|user.?id|account|email|player.?name|user.?name|open.?id", RegexOptions.IgnoreCase)]
    private static partial Regex SensitiveKey();
}
