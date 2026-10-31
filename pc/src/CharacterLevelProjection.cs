namespace DotAbyss.Local.Pc;

internal readonly record struct CharacterLevel(long Id, int Level);

internal sealed class CharacterLevelProjection
{
    private readonly Dictionary<long, int> originalLevels = new();
    private (long Store, long User) owner;

    internal CharacterLevel[] Apply(long store, long user, IReadOnlyList<CharacterLevel> updates,
        IReadOnlyList<CharacterLevel> characters, bool unlocked, int synchronizedLevel)
    {
        if (store == 0 || user <= 0) throw new InvalidDataException("A player store is required for level projection.");
        if (unlocked && synchronizedLevel < 1) throw new InvalidDataException("The synchronization level is missing.");
        Validate(updates);
        Validate(characters);
        var live = characters.Select(value => value.Id).ToHashSet();
        var next = owner == (store, user) ? new Dictionary<long, int>(originalLevels) : new Dictionary<long, int>();
        foreach (var id in next.Keys.Where(id => !live.Contains(id)).ToArray()) next.Remove(id);
        foreach (var character in characters) next.TryAdd(character.Id, character.Level);
        foreach (var update in updates)
            if (live.Contains(update.Id)) next[update.Id] = update.Level;
        var result = characters.Select(character => new CharacterLevel(character.Id,
            unlocked ? synchronizedLevel : next[character.Id])).ToArray();
        originalLevels.Clear();
        foreach (var entry in next) originalLevels.Add(entry.Key, entry.Value);
        owner = (store, user);
        return result;
    }

    private static void Validate(IReadOnlyList<CharacterLevel> values)
    {
        var ids = new HashSet<long>();
        foreach (var value in values)
            if (value.Id <= 0 || value.Level <= 0 || !ids.Add(value.Id))
                throw new InvalidDataException("Player levels must have valid unique identities.");
    }
}
