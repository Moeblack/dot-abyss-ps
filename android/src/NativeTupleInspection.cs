using System.Globalization;
using System.Reflection;
using System.Runtime.InteropServices;
using Il2CppInterop.Runtime;
using LoginTuple = Il2CppSystem.ValueTuple<long, string>;

namespace DotAbyss.Local;

internal sealed record LoginTupleShape(bool WrapperPresent, bool LongMatches, bool LongIsZero,
    bool StringPointerMatches, bool StringInspected, bool StringMatches, bool StringIsNull,
    int? StringLength, bool? StringParsesAsDateTime)
{
    public bool Matches => WrapperPresent && LongMatches && StringInspected && StringMatches;
}

internal static class NativeTupleInspection
{
    internal static nint StringPointer(LoginTuple tuple)
    {
        var field = typeof(LoginTuple).GetField("NativeFieldInfoPtr_Item2", BindingFlags.NonPublic | BindingFlags.Static)
            ?? throw new MissingFieldException(typeof(LoginTuple).FullName, "NativeFieldInfoPtr_Item2");
        var metadata = (nint)(field.GetValue(null) ?? throw new InvalidOperationException("Native Item2 metadata is unavailable."));
        var offset = checked((int)IL2CPP.il2cpp_field_get_offset(metadata));
        return Marshal.ReadIntPtr(tuple.Pointer, offset);
    }

    internal static LoginTupleShape Compare(LoginTuple? tuple, GameLoginData expected, nint expectedStringPointer)
    {
        if (tuple is null) return new(false, false, false, false, false, false, false, null, null);
        var number = tuple.Item1;
        var pointer = StringPointer(tuple);
        var pointerMatches = pointer == expectedStringPointer;
        if (pointer != 0 && !pointerMatches)
            return new(true, number == expected.UserId, number == 0, false, false, false, false, null, null);

        // A mismatched boxed header must not be dereferenced as a native string.
        var value = pointer == 0 ? null : tuple.Item2;
        var isDate = value is not null && DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture,
            DateTimeStyles.AllowWhiteSpaces | DateTimeStyles.AssumeUniversal, out _);
        return new(true, number == expected.UserId, number == 0, pointerMatches, true,
            value == expected.ServerTime, value is null, value?.Length, isDate);
    }
}
