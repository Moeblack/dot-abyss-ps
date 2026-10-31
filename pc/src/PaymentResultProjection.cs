namespace DotAbyss.Local.Pc;

internal static class PaymentResultProjection
{
    internal static T[] ForDisplay<T>(IEnumerable<T>? items, Func<T, int> contentType)
    {
        // Entitlements are already granted, but the native receipt has no provider for them.
        return items?.Where(item => contentType(item) is not (180 or 185 or 186)).ToArray() ?? Array.Empty<T>();
    }
}
