namespace DotAbyss.Local.Pc;

internal sealed class OperationIdentities
{
    private sealed record Entry(long Task, object Owner, string Fingerprint, string Identity, Func<bool> Completed);
    private readonly Dictionary<long, Entry> entries = new();
    private readonly LinkedList<long> order = new();
    private readonly object gate = new();
    private readonly int capacity;

    internal OperationIdentities(int capacity = 256)
    {
        if (capacity < 1) throw new ArgumentOutOfRangeException(nameof(capacity));
        this.capacity = capacity;
    }

    internal string Get(long request, long task, object owner, string fingerprint, Func<bool> completed)
    {
        if (request == 0 || task == 0) throw new ArgumentException("Native request identity is unavailable.");
        ArgumentNullException.ThrowIfNull(owner);
        lock (gate)
        {
            var identity = Guid.NewGuid().ToString("N");
            if (entries.TryGetValue(request, out var previous) && previous.Fingerprint == fingerprint)
            {
                var finished = false;
                if (previous.Task != task)
                {
                    try { finished = previous.Completed(); }
                    catch { finished = false; }
                }
                if (!finished) identity = previous.Identity;
            }
            entries[request] = new Entry(task, owner, fingerprint, identity, completed);
            order.Remove(request);
            order.AddLast(request);
            while (entries.Count > capacity)
            {
                entries.Remove(order.First!.Value);
                order.RemoveFirst();
            }
            return identity;
        }
    }
}
