using DotAbyss.Local.Pc;

var checks = 0;
void Check(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
    checks++;
}

PaymentItem[] Display(IEnumerable<PaymentItem>? items) => PaymentResultProjection.ForDisplay(items, item => item.ContentType);

var premium = new[] { new PaymentItem(20, 0, 2400), new PaymentItem(180, 202611, 10000) };
Check(Display(premium).SequenceEqual(premium.Take(1)), "Premium pass receipt shows its paid currency without treating access as an item");
Check(premium.Length == 2 && premium[1].Amount == 10000, "Display projection leaves the granted entitlement unchanged");

foreach (var type in new[] { 185, 186 })
{
    var rewards = new[] { new PaymentItem(type, 10045, 1), new PaymentItem(10, 0, 120), new PaymentItem(30, 80004, 5) };
    Check(Display(rewards).SequenceEqual(rewards.Skip(1)), "Login benefit receipt preserves the actual daily rewards: " + type);
}

var normalTypes = new[] { 10, 20, 30, 33, 31, 40, 41, 50, 51, 53, 52, 60, 70, 80, 90, 110, 111, 140, 141, 170, 181, 54, 56, 160, 165, 166, 190, 45 };
var normalRewards = normalTypes.Select((type, index) => new PaymentItem(type, 9007199254740993L + index, index + 1)).ToArray();
Check(Display(normalRewards).SequenceEqual(normalRewards), "All native receipt types preserve their order, identifiers and amounts");
Check(Display(new[] { new PaymentItem(181, 202611, 100) }).Single().Amount == 100, "Pass points remain visible");
Check(Display(new[] { new PaymentItem(999, 12, 34) }).Single().ContentType == 999, "Unknown item types remain visible to diagnostics instead of being silently dropped");
Check(Display(new[] { premium[0], premium[0], premium[1] }).Length == 2, "Distinct reward entries are not merged or deduplicated");
Check(Display(new[] { premium[1], new PaymentItem(185, 1, 1), new PaymentItem(186, 2, 1) }).Length == 0, "Entitlement-only purchases produce an empty item receipt");
Check(Display(Array.Empty<PaymentItem>()).Length == 0, "Empty purchases remain empty");
Check(Display(null).Length == 0, "Missing optional item arrays remain empty");

var calls = 0;
IEnumerable<PaymentItem> SingleRead()
{
    if (++calls > 1) throw new InvalidOperationException("Rewards enumerated more than once");
    foreach (var item in premium) yield return item;
}
Check(Display(SingleRead()).SequenceEqual(premium.Take(1)) && calls == 1, "Native reward arrays are enumerated once");

Console.WriteLine($"Payment display checks passed: {checks}");

internal sealed record PaymentItem(int ContentType, long ContentId, int Amount);
