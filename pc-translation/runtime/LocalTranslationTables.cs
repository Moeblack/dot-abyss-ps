using System;
using System.Collections.Concurrent;
using System.Collections.Generic;

namespace AbyssMod.Services;

public static class LocalTranslationTables
{
    public static Dictionary<string, string> Abilities(Func<string, Dictionary<string, string>> getTable)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (string name in new[] { "ability_descriptions", "m_enemy_skills", "m_character_action_skills", "m_ability_details" })
        {
            var table = getTable(name);
            if (table == null)
                continue;
            foreach (var pair in table)
            {
                if (!string.IsNullOrEmpty(pair.Key) && !string.IsNullOrEmpty(pair.Value))
                    result[pair.Key] = pair.Value;
            }
        }
        return result;
    }

    public static Dictionary<string, string> WithoutAbilities(Dictionary<string, string> source, Dictionary<string, string> abilities)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        if (source == null)
            return result;
        foreach (var pair in source)
        {
            if (abilities == null || !abilities.ContainsKey(pair.Key))
                result[pair.Key] = pair.Value;
        }
        return result;
    }
}

public sealed class LocalTextRevisions
{
    private readonly ConcurrentDictionary<int, string> _outputs = new();

    public bool IsCurrent(int identifier, string text) => _outputs.TryGetValue(identifier, out string output) && output == text;

    public void Record(int identifier, string original, string translated)
    {
        if (original == translated)
        {
            _outputs.TryRemove(identifier, out _);
            return;
        }
        if (_outputs.Count >= 4096)
            _outputs.Clear();
        _outputs[identifier] = translated;
    }

    public void Clear() => _outputs.Clear();

    public void Forget(int identifier) => _outputs.TryRemove(identifier, out _);
}
