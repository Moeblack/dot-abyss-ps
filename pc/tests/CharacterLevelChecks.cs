using DotAbyss.Local.Pc;

internal static class CharacterLevelChecks
{
    internal static void Run()
    {
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException(name);
            count++;
        }
        void Reject(Action action, string name)
        {
            try { action(); }
            catch (InvalidDataException) { count++; return; }
            throw new InvalidOperationException(name);
        }
        var projection = new CharacterLevelProjection();
        var original = new[] { new CharacterLevel(1, 30), new CharacterLevel(2, 1) };
        var empty = Array.Empty<CharacterLevel>();
        var result = projection.Apply(100, 7, original, original, false, 30);
        Check(result.SequenceEqual(original), "Locked research preserves trained levels");
        result = projection.Apply(100, 7, empty, result, true, 32);
        Check(result.Select(value => value.Level).SequenceEqual(new[] { 32, 32 }), "Research applies to every owned character");
        Check(original.Select(value => value.Level).SequenceEqual(new[] { 30, 1 }), "Projection does not mutate original records");
        result = projection.Apply(100, 7, new[] { new CharacterLevel(2, 20) }, result, true, 34);
        Check(result.All(value => value.Level == 34), "Training edit and synchronization update can share a batch");
        result = projection.Apply(100, 7, empty, result.Reverse().ToArray(), true, 31);
        Check(result.Select(value => value.Id).SequenceEqual(new long[] { 2, 1 }) && result.All(value => value.Level == 31),
            "Reordered characters keep identity and accept a reduced synchronization level");
        result = projection.Apply(100, 7, empty, result, false, 0);
        Check(result.SequenceEqual(new[] { new CharacterLevel(2, 20), new CharacterLevel(1, 30) }),
            "Removing research restores the latest trained level by identity");
        result = projection.Apply(100, 7, new[] { new CharacterLevel(1, 11), new CharacterLevel(2, 12) }, result, true, 40);
        result = projection.Apply(100, 7, empty, result, false, 0);
        Check(result.SequenceEqual(new[] { new CharacterLevel(2, 12), new CharacterLevel(1, 11) }),
            "A full login or restored snapshot replaces cached training levels");
        result = projection.Apply(100, 7, empty, new[] { result[1] }, true, 41);
        Check(result.Length == 1 && result[0].Id == 1, "Deleted characters leave the projection");
        result = projection.Apply(100, 7, empty, new[] { result[0], new CharacterLevel(2, 7) }, false, 0);
        Check(result[1].Level == 7, "Reused deleted identity does not inherit stale training");
        result = projection.Apply(100, 8, empty, new[] { new CharacterLevel(1, 8) }, false, 0);
        Check(result[0].Level == 8, "Changing player clears the previous player's training");
        result = projection.Apply(101, 8, empty, new[] { new CharacterLevel(1, 9) }, false, 0);
        Check(result[0].Level == 9, "Replacing the native store starts a fresh projection");
        result = projection.Apply(101, 8, new[] { new CharacterLevel(99, 100) }, result, true, 32);
        Check(result.Length == 1 && result[0].Id == 1, "Non-player models are never added or projected");
        Reject(() => projection.Apply(101, 8, new[] { new CharacterLevel(1, 5) }, result, true, 0),
            "An unlocked projection cannot use a missing synchronization level");
        result = projection.Apply(101, 8, empty, result, false, 0);
        Check(result[0].Level == 9, "Failed synchronization does not overwrite trained levels");
        Reject(() => projection.Apply(101, 8, empty, new[] { new CharacterLevel(1, 2), new CharacterLevel(1, 3) }, false, 0),
            "Duplicate player identities are rejected");
        Check(projection.Apply(101, 8, empty, empty, false, 0).Length == 0, "An empty player store clears cached entries");
        Console.WriteLine($"Character level projection: {count} checks passed.");
    }
}
