using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace AbyssMod.Services;

public enum LocalTemplateKind
{
    Indexed,
    Ability,
}

public enum LocalTextMatchStatus
{
    Missing,
    Matched,
    Ambiguous,
    Deferred,
}

public sealed class LocalTextMatcher
{
    private const int MaximumTextLength = 8192;
    private const int MaximumCandidates = 256;
    private const int MaximumCacheEntries = 2048;
    private const string Number = @"[+\-＋－]?(?:[0-9０-９]{1,3}(?:[,，][0-9０-９]{3})+|[0-9０-９]+)(?:[.．][0-9０-９]+)?[%％]?";
    private const string OpenTags = @"(?:<[a-zA-Z][a-zA-Z0-9-]*(?:=[^<>]{1,120})?>){0,4}";
    private const string CloseTags = @"(?:</[a-zA-Z][a-zA-Z0-9-]*>){0,4}";
    private const string ColorValue = @"(?:#[0-9A-Fa-f]{8}|#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{4}|#[0-9A-Fa-f]{3}|black|blue|green|orange|purple|red|white|yellow)";
    private static readonly TimeSpan RegexTimeout = TimeSpan.FromMilliseconds(20);
    private static readonly Regex Tokens = new(@"\{(?:\[[^\]\r\n]{1,200}\][^{}\r\n]{0,16}|[+\-＋－]?[0-9０-９]+(?:[.．][0-9０-９]+)?[%％]?|[0-9]+:[^{}\r\n]{1,24})\}", RegexOptions.CultureInvariant, RegexTimeout);
    private static readonly Regex NumericValue = new("\\A" + Number + "\\z", RegexOptions.CultureInvariant, RegexTimeout);
    private static readonly Regex Color = new("\\A" + ColorValue + "\\z", RegexOptions.CultureInvariant, RegexTimeout);
    private static readonly HashSet<string> FormattingTags = new(StringComparer.OrdinalIgnoreCase)
    {
        "color", "size", "b", "i", "u", "s", "mark", "alpha", "voffset", "cspace", "mspace",
    };
    private readonly Dictionary<string, string> _exact = new(StringComparer.Ordinal);
    private readonly HashSet<string> _ambiguousExact = new(StringComparer.Ordinal);
    private readonly PrefixNode _prefixes = new();
    private readonly ConcurrentDictionary<string, MatchResult> _cache = new(StringComparer.Ordinal);

    public LocalTextMatcher(IEnumerable<KeyValuePair<string, string>> translations, LocalTemplateKind kind)
    {
        foreach (var pair in translations ?? Array.Empty<KeyValuePair<string, string>>())
        {
            if (string.IsNullOrEmpty(pair.Key) || string.IsNullOrEmpty(pair.Value))
                continue;
            string source = NormalizeLines(pair.Key);
            string target = NormalizeLines(pair.Value);
            if (source.Length > MaximumTextLength || target.Length > MaximumTextLength)
                continue;
            if (_exact.TryGetValue(source, out var previous) && previous != target)
                _ambiguousExact.Add(source);
            else
                _exact[source] = target;
            var template = Compile(source, target, kind);
            if (template == null)
                continue;
            var node = _prefixes;
            foreach (char value in template.Prefix)
            {
                if (!node.Children.TryGetValue(value, out var child))
                    node.Children[value] = child = new PrefixNode();
                node = child;
            }
            node.Templates.Add(template);
        }
    }

    public bool TryTranslate(string text, out string result) => Match(text, out result) == LocalTextMatchStatus.Matched;

    public LocalTextMatchStatus Match(string text, out string result)
    {
        result = null;
        if (string.IsNullOrEmpty(text) || text.Length > MaximumTextLength)
            return LocalTextMatchStatus.Missing;
        if (_cache.TryGetValue(text, out var cached))
        {
            result = cached.Text;
            return Status(cached);
        }
        string normalized = NormalizeLines(text);
        var match = Find(normalized);
        if (match.Cacheable)
        {
            if (_cache.Count >= MaximumCacheEntries)
                _cache.Clear();
            _cache.TryAdd(text, match);
        }
        result = match.Text;
        return Status(match);
    }

    private static LocalTextMatchStatus Status(MatchResult result) => result.Found ? LocalTextMatchStatus.Matched
        : result.Ambiguous ? LocalTextMatchStatus.Ambiguous
        : result.Cacheable ? LocalTextMatchStatus.Missing : LocalTextMatchStatus.Deferred;

    public static string NormalizeLines(string text) => text.Replace("\\r\\n", "\n").Replace("\\n", "\n").Replace("\r\n", "\n").Replace('\r', '\n');

    private MatchResult Find(string text)
    {
        if (_ambiguousExact.Contains(text))
            return MatchResult.Conflict;
        if (_exact.TryGetValue(text, out var exact))
            return new MatchResult(exact);

        bool wrapped = TryUnwrap(text, out string prefix, out string inner, out string suffix);
        if (wrapped)
        {
            if (_ambiguousExact.Contains(inner))
                return MatchResult.Conflict;
            if (_exact.TryGetValue(inner, out exact))
                return new MatchResult(prefix + exact + suffix);
        }

        var clock = Stopwatch.StartNew();
        var direct = FindTemplate(text, clock);
        if (!direct.Cacheable || direct.Ambiguous || !wrapped)
            return direct;
        var nested = FindTemplate(inner, clock);
        if (!nested.Cacheable || nested.Ambiguous)
            return nested;
        if (!nested.Found)
            return direct;
        string decorated = prefix + nested.Text + suffix;
        if (direct.Found && direct.Text != decorated)
            return MatchResult.Conflict;
        return new MatchResult(decorated);
    }

    private MatchResult FindTemplate(string text, Stopwatch clock)
    {
        string result = null;
        int count = 0;
        var node = _prefixes;
        for (int offset = 0; ; offset++)
        {
            foreach (var template in node.Templates)
            {
                if (template.Suffix.Length > 0 && !text.EndsWith(template.Suffix, StringComparison.Ordinal))
                    continue;
                if (++count > MaximumCandidates || clock.ElapsedMilliseconds > 50)
                    return MatchResult.Exhausted;
                string translated;
                try
                {
                    translated = template.Translate(text);
                }
                catch (RegexMatchTimeoutException)
                {
                    return MatchResult.Exhausted;
                }
                if (translated == null)
                    continue;
                if (result != null && result != translated)
                    return MatchResult.Conflict;
                result = translated;
            }
            if (offset == text.Length || !node.Children.TryGetValue(text[offset], out var child))
                break;
            node = child;
        }
        return result == null ? MatchResult.Missing : new MatchResult(result);
    }

    private static CompiledTemplate Compile(string source, string target, LocalTemplateKind kind)
    {
        var sourceTokens = ReadTokens(source, kind);
        if (sourceTokens.Count == 0)
            return null;
        if (string.IsNullOrWhiteSpace(LiteralContext(source)))
            return null;
        for (int index = 1; index < sourceTokens.Count; index++)
        {
            if (sourceTokens[index - 1].Start + sourceTokens[index - 1].Length == sourceTokens[index].Start)
                return null;
        }
        var targetTokens = ReadTokens(target, kind);
        var identities = sourceTokens.Select(token => token.Identity).ToHashSet(StringComparer.Ordinal);
        if (!identities.SetEquals(targetTokens.Select(token => token.Identity)))
            return null;

        var pattern = new StringBuilder("\\A");
        int offset = 0;
        for (int index = 0; index < sourceTokens.Count; index++)
        {
            var token = sourceTokens[index];
            pattern.Append(Regex.Escape(source.Substring(offset, token.Start - offset)));
            pattern.Append("(?<v").Append(index).Append('>');
            if (token.IsColor)
            {
                pattern.Append(ColorValue);
            }
            else
            {
                pattern.Append(OpenTags).Append(@"\{?").Append(Number)
                    .Append(CloseTags).Append(Regex.Escape(token.Suffix)).Append(CloseTags).Append(@"\}?");
                if (token.AllowText)
                    pattern.Append(@"|[^\p{Cc}<>{}]{1,128}?");
            }
            pattern.Append(')');
            offset = token.Start + token.Length;
        }
        pattern.Append(Regex.Escape(source.Substring(offset))).Append("\\z");
        string prefix = source.Substring(0, sourceTokens[0].Start);
        string suffix = source.Substring(offset);
        return new CompiledTemplate(pattern.ToString(), prefix, suffix, sourceTokens, targetTokens, target);
    }

    private static List<Token> ReadTokens(string text, LocalTemplateKind kind)
    {
        var result = new List<Token>();
        int fixedLetters = LiteralContext(text).Count(char.IsLetter);
        foreach (Match match in Tokens.Matches(text))
        {
            string content = match.Value.Substring(1, match.Length - 2);
            string identity;
            string suffix = "";
            string fixedValue = null;
            bool allowText = false;
            bool isColor = false;
            if (content[0] == '[')
            {
                int close = content.IndexOf(']');
                suffix = content.Substring(close + 1);
                identity = "named:" + content;
            }
            else if (kind == LocalTemplateKind.Ability && NumericValue.IsMatch(content))
            {
                fixedValue = NormalizeNumber(content);
                identity = "fixed:" + fixedValue;
            }
            else if (IsIndexed(content))
            {
                identity = "index:" + content;
                bool insideTag = text.LastIndexOf('<', match.Index) > text.LastIndexOf('>', match.Index);
                isColor = insideTag && text.Substring(0, match.Index).EndsWith("<color=", StringComparison.OrdinalIgnoreCase)
                    && text.Substring(match.Index + match.Length).StartsWith(">", StringComparison.Ordinal);
                allowText = content.IndexOf(':') < 0 && !insideTag && fixedLetters >= 4;
            }
            else
            {
                continue;
            }
            result.Add(new Token(match.Index, match.Length, identity, suffix, fixedValue, allowText, isColor, "", ""));
        }
        for (int index = 0; index < result.Count; index++)
        {
            var token = result[index];
            string before = index == 0 ? "" : LiteralContext(text.Substring(result[index - 1].Start + result[index - 1].Length,
                token.Start - result[index - 1].Start - result[index - 1].Length));
            string after = index + 1 == result.Count ? "" : LiteralContext(text.Substring(token.Start + token.Length,
                result[index + 1].Start - token.Start - token.Length));
            bool unseparated = index > 0 && string.IsNullOrWhiteSpace(before)
                || index + 1 < result.Count && string.IsNullOrWhiteSpace(after);
            result[index] = token with { AllowText = token.AllowText && !unseparated, Before = before, After = after };
        }
        return result;
    }

    private static string LiteralContext(string text) =>
        Regex.Replace(Tokens.Replace(text, ""), @"<[^<>]*>", "", RegexOptions.CultureInvariant, RegexTimeout);

    private static bool IsIndexed(string value)
    {
        int colon = value.IndexOf(':');
        string index = colon < 0 ? value : value.Substring(0, colon);
        return index.Length > 0 && index.All(character => character >= '0' && character <= '9');
    }

    private static string NormalizeNumber(string value)
    {
        var normalized = new StringBuilder(value.Length);
        foreach (char character in value)
        {
            if (character == ',' || character == '，')
                continue;
            normalized.Append(character >= '０' && character <= '９' ? (char)(character - '０' + '0') : character switch
            {
                '％' => '%', '．' => '.', '＋' => '+', '－' => '-', _ => character,
            });
        }
        return normalized.ToString();
    }

    private static bool TryValue(string value, Token token, out string identity)
    {
        identity = null;
        if (token.IsColor)
        {
            identity = value;
            return Color.IsMatch(value);
        }
        var plain = new StringBuilder();
        var tags = new Stack<string>();
        for (int index = 0; index < value.Length;)
        {
            if (value[index] != '<')
            {
                plain.Append(value[index++]);
                continue;
            }
            if (!TryTag(value, index, out string tag, out bool closing, out int length))
                return false;
            if (closing)
            {
                if (tags.Count == 0 || !string.Equals(tags.Pop(), tag, StringComparison.OrdinalIgnoreCase))
                    return false;
            }
            else
            {
                tags.Push(tag);
            }
            index += length;
        }
        if (tags.Count != 0)
            return false;
        string number = plain.ToString();
        if (number.StartsWith("{", StringComparison.Ordinal) && number.EndsWith("}", StringComparison.Ordinal))
            number = number.Substring(1, number.Length - 2);
        if (!number.EndsWith(token.Suffix, StringComparison.Ordinal))
            return false;
        number = number.Substring(0, number.Length - token.Suffix.Length);
        if (!NumericValue.IsMatch(number))
        {
            if (!token.AllowText || value.Length > 128 || value.Any(character => char.IsControl(character) || character is '<' or '>' or '{' or '}'))
                return false;
            if (token.Before.Length > 0 && value.Contains(token.Before, StringComparison.Ordinal)
                || token.After.Length > 0 && value.Contains(token.After, StringComparison.Ordinal))
                return false;
            identity = "text:" + value;
            return true;
        }
        identity = NormalizeNumber(number);
        return token.FixedValue == null || identity == token.FixedValue;
    }

    private static bool TryUnwrap(string text, out string prefix, out string inner, out string suffix)
    {
        int start = 0;
        int end = text.Length;
        for (int depth = 0; depth < 4 && start < end; depth++)
        {
            if (!TryTag(text, start, out string name, out bool closing, out int length) || closing)
                break;
            string close = "</" + name + ">";
            int closeStart = end - close.Length;
            if (closeStart < start + length || !string.Equals(text.Substring(closeStart, close.Length), close, StringComparison.OrdinalIgnoreCase))
                break;
            start += length;
            end = closeStart;
        }
        prefix = text.Substring(0, start);
        inner = text.Substring(start, end - start);
        suffix = text.Substring(end);
        return start != 0;
    }

    private static bool TryTag(string text, int offset, out string name, out bool closing, out int length)
    {
        name = null;
        closing = false;
        length = 0;
        if (offset >= text.Length || text[offset] != '<')
            return false;
        int end = text.IndexOf('>', offset + 1);
        if (end < 0 || end - offset > 160)
            return false;
        string body = text.Substring(offset + 1, end - offset - 1);
        closing = body.StartsWith("/", StringComparison.Ordinal);
        if (closing)
            body = body.Substring(1);
        int equals = body.IndexOf('=');
        name = equals < 0 ? body : body.Substring(0, equals);
        if (!FormattingTags.Contains(name) || (closing && equals >= 0) || body.IndexOf('<') >= 0)
            return false;
        length = end - offset + 1;
        return true;
    }

    private sealed class PrefixNode
    {
        public readonly Dictionary<char, PrefixNode> Children = new();
        public readonly List<CompiledTemplate> Templates = new();
    }

    private sealed class CompiledTemplate
    {
        private readonly Lazy<Regex> _expression;
        private readonly List<Token> _sourceTokens;
        private readonly List<Token> _targetTokens;
        private readonly string _target;
        public readonly string Prefix;
        public readonly string Suffix;

        public CompiledTemplate(string pattern, string prefix, string suffix, List<Token> sourceTokens, List<Token> targetTokens, string target)
        {
            _expression = new Lazy<Regex>(() => new Regex(pattern, RegexOptions.CultureInvariant, RegexTimeout));
            Prefix = prefix;
            Suffix = suffix;
            _sourceTokens = sourceTokens;
            _targetTokens = targetTokens;
            _target = target;
        }

        public string Translate(string text)
        {
            var match = _expression.Value.Match(text);
            if (!match.Success)
                return null;
            var values = new Dictionary<string, (string Identity, string Display)>(StringComparer.Ordinal);
            for (int index = 0; index < _sourceTokens.Count; index++)
            {
                var token = _sourceTokens[index];
                string display = match.Groups["v" + index].Value;
                if (!TryValue(display, token, out var identity))
                    return null;
                if (values.TryGetValue(token.Identity, out var previous) && previous.Identity != identity)
                    return null;
                values.TryAdd(token.Identity, (identity, display));
            }
            var translated = new StringBuilder();
            int offset = 0;
            foreach (var token in _targetTokens)
            {
                translated.Append(_target, offset, token.Start - offset);
                translated.Append(values[token.Identity].Display);
                offset = token.Start + token.Length;
            }
            translated.Append(_target, offset, _target.Length - offset);
            return translated.ToString();
        }
    }

    private sealed record Token(int Start, int Length, string Identity, string Suffix, string FixedValue, bool AllowText, bool IsColor, string Before, string After);
    private readonly record struct MatchResult(string Text, bool Found = true, bool Cacheable = true, bool Ambiguous = false)
    {
        public static MatchResult Missing => new(null, false);
        public static MatchResult Conflict => new(null, false, true, true);
        public static MatchResult Exhausted => new(null, false, false);
    }
}
