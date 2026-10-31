using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace DotAbyss.Observer.Pc;

internal sealed record SanitizedPayload(byte[] Json, byte[] Schema, string[] Assets);
internal sealed class CaptureLimitException : Exception;

internal sealed class CaptureSanitizer
{
    private const string Redacted = "[redacted]";
    private readonly CaptureOptions options;
    private readonly SortedDictionary<string, SortedSet<string>> schema = new(StringComparer.Ordinal);
    private readonly SortedSet<string> assets = new(StringComparer.Ordinal);
    private int nodes;
    private bool schemaTruncated;
    private static readonly HashSet<string> BusinessTokens = new(StringComparer.Ordinal)
    {
        "tgachatokens", "mgachatokens", "gachatokens", "gachatoken", "gachatokenid", "gachatokencount",
        "tokencount", "tokenamount", "tokenquantity", "tokenid"
    };
    private static readonly string[] SensitiveParts =
    {
        "token", "session", "cookie", "password", "passwd", "secret", "credential", "authorization", "authentication",
        "receipt", "signature", "privatekey", "accesskey", "apikey", "clientsecret", "verificationcode", "bearer", "hmac", "uuid", "oauth"
    };
    private static readonly HashSet<string> SensitiveNames = new(StringComparer.Ordinal)
    {
        "auth", "pwd", "otp", "jwt", "sign", "uuid", "deviceid", "device", "email", "username", "playername", "nickname",
        "account", "accountid", "userid", "playerid", "openid", "dmmid", "raw", "debug", "stack", "stacktrace", "message", "error",
        "headers", "requestheaders", "responseheaders", "sig", "signedrequest", "assertion", "nonce", "challenge", "codeverifier", "codechallenge", "csrf", "xsrf"
    };
    private static readonly Regex SafeKey = new(@"\A(?:[A-Za-z_][A-Za-z0-9_.-]{0,95}|[0-9]{1,19})\z",
        RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(20));
    private static readonly Regex CredentialText = new(@"(?:\b(?:bearer|basic)\s+\S+|(?:token|password|session|secret|signature|authorization|api[_-]?key)\s*[:=]|-----BEGIN|\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b)",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(20));
    private static readonly Regex Opaque = new(@"\A[A-Za-z0-9_+=/-]{32,}\z", RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(20));

    internal CaptureSanitizer(CaptureOptions options) => this.options = options;

    internal SanitizedPayload Sanitize(string body)
    {
        CheckBody(body);
        using var document = JsonDocument.Parse(body, new JsonDocumentOptions { MaxDepth = options.MaxDepth });
        using var output = new MemoryStream();
        using (var writer = new Utf8JsonWriter(output)) Write(document.RootElement, writer, "$", "", 0);
        if (output.Length > options.MaxBodyBytes) throw new CaptureLimitException();
        var shape = JsonSerializer.SerializeToUtf8Bytes(new { format_version = 1, truncated = schemaTruncated,
            fields = schema.ToDictionary(pair => pair.Key, pair => pair.Value.ToArray(), StringComparer.Ordinal) });
        return new SanitizedPayload(output.ToArray(), shape, assets.ToArray());
    }

    private void CheckBody(string body)
    {
        if (body.Length > options.MaxBodyChars || Encoding.UTF8.GetByteCount(body) > options.MaxBodyBytes)
            throw new CaptureLimitException();
    }

    private void Write(JsonElement value, Utf8JsonWriter writer, string path, string key, int depth)
    {
        if (++nodes > options.MaxNodes || depth > options.MaxDepth) throw new CaptureLimitException();
        AddShape(path, value.ValueKind.ToString());
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                var seen = new HashSet<string>(StringComparer.Ordinal);
                foreach (var property in value.EnumerateObject())
                {
                    if (!SafeKey.IsMatch(property.Name) || !seen.Add(property.Name)) throw new JsonException();
                    var normalized = NormalizeKey(property.Name);
                    writer.WritePropertyName(property.Name);
                    var childPath = path + "." + property.Name;
                    if (Sensitive(normalized, property.Value))
                    {
                        writer.WriteStringValue(Redacted);
                        AddShape(childPath, property.Value.ValueKind.ToString());
                        AddShape(childPath, "Redacted");
                    }
                    else Write(property.Value, writer, childPath, normalized, depth + 1);
                }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in value.EnumerateArray()) Write(item, writer, path + "[]", key, depth + 1);
                writer.WriteEndArray();
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(SanitizeString(value.GetString()!, path, key, depth));
                break;
            default:
                value.WriteTo(writer);
                break;
        }
        if (writer.BytesCommitted + writer.BytesPending > options.MaxBodyBytes) throw new CaptureLimitException();
    }

    private string SanitizeString(string value, string path, string key, int depth)
    {
        var trimmed = value.Trim();
        var structuredSession = key == "sessiondetail" && (trimmed.StartsWith('{') || trimmed.StartsWith('[') || trimmed == "null");
        if (value.Length > options.MaxStringChars && !structuredSession) return "[omitted:string_limit]";
        if (value.Any(c => char.IsControl(c) && c is not '\t' and not '\r' and not '\n')) return Redacted;
        if (structuredSession || trimmed.StartsWith('{') || trimmed.StartsWith('[') || trimmed.StartsWith('"'))
        {
            try
            {
                using var nested = JsonDocument.Parse(trimmed, new JsonDocumentOptions { MaxDepth = options.MaxDepth });
                using var output = new MemoryStream();
                using (var writer = new Utf8JsonWriter(output)) Write(nested.RootElement, writer, path + "{json}", key, depth + 1);
                return Encoding.UTF8.GetString(output.ToArray());
            }
            catch (JsonException) { return Redacted; }
        }
        if (Uri.TryCreate(trimmed, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
        {
            var reference = CapturePolicy.SafeResourceReference(trimmed);
            if (reference != Redacted && assets.Count < 256) assets.Add(reference);
            return reference;
        }
        if (trimmed.StartsWith('/') || key.EndsWith("url", StringComparison.Ordinal) || key.EndsWith("link", StringComparison.Ordinal))
        {
            var reference = CapturePolicy.SafeResourceReference(trimmed);
            if (reference != Redacted && assets.Count < 256) assets.Add(reference);
            return reference;
        }
        if (trimmed.Contains("://", StringComparison.Ordinal) || trimmed.Contains("%3a%2f", StringComparison.OrdinalIgnoreCase)
            || trimmed.Contains("%7b", StringComparison.OrdinalIgnoreCase) || trimmed.Contains("%5b", StringComparison.OrdinalIgnoreCase)
            || CredentialText.IsMatch(trimmed)) return Redacted;
        var decoded = trimmed;
        for (var pass = 0; pass < 2; pass++)
        {
            var next = Uri.UnescapeDataString(decoded);
            if (next == decoded) break;
            if (next.Contains("://", StringComparison.Ordinal) || next.StartsWith('{') || next.StartsWith('[')
                || next.Contains("%7b", StringComparison.OrdinalIgnoreCase) || next.Contains("%5b", StringComparison.OrdinalIgnoreCase)
                || CredentialText.IsMatch(next)) return Redacted;
            decoded = next;
        }
        if (Guid.TryParse(trimmed, out _)) return Redacted;
        if (Opaque.IsMatch(trimmed) && key is not ("sha256" or "sha1" or "md5" or "hash" or "checksum")) return Redacted;
        return value;
    }

    private static string NormalizeKey(string key) => new(key.Normalize(NormalizationForm.FormKC)
        .Where(char.IsLetterOrDigit).Select(char.ToLowerInvariant).ToArray());

    private bool Sensitive(string key, JsonElement value)
    {
        if (key == "sessiondetail")
        {
            if (value.ValueKind is JsonValueKind.Object or JsonValueKind.Array or JsonValueKind.Null) return false;
            if (value.ValueKind == JsonValueKind.String)
            {
                try
                {
                    using var nested = JsonDocument.Parse(value.GetString()!, new JsonDocumentOptions { MaxDepth = options.MaxDepth });
                    if (nested.RootElement.ValueKind is JsonValueKind.Object or JsonValueKind.Array or JsonValueKind.Null) return false;
                }
                catch (JsonException) { }
            }
        }
        if (BusinessTokens.Contains(key) && (value.ValueKind is JsonValueKind.Number or JsonValueKind.Null
            || (key.Contains("gacha", StringComparison.Ordinal) && value.ValueKind is JsonValueKind.Array or JsonValueKind.Object))) return false;
        return SensitiveNames.Contains(key) || SensitiveParts.Any(key.Contains);
    }

    private void AddShape(string path, string kind)
    {
        if (path.Length > 512 || (!schema.ContainsKey(path) && schema.Count >= options.MaxSchemaFields))
        {
            schemaTruncated = true;
            return;
        }
        if (!schema.TryGetValue(path, out var kinds)) schema[path] = kinds = new SortedSet<string>(StringComparer.Ordinal);
        kinds.Add(kind);
    }
}
