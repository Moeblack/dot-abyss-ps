using System.Reflection.Emit;

namespace Il2CppInterop.HarmonySupport;

internal static class ValueTypeArgument
{
    internal static bool UseAddress(bool is64Bit, bool isWindowsX64, bool isInstance, bool isByRef, int nativeSize)
        => !is64Bit || (isWindowsX64 && !isInstance && !isByRef && nativeSize is 1 or 2 or 4 or 8);

    internal static void Emit(ILGenerator il, int index, bool is64Bit, bool isWindowsX64, bool isInstance, bool isByRef, int nativeSize)
    {
        var opcode = UseAddress(is64Bit, isWindowsX64, isInstance, isByRef, nativeSize)
            ? index <= byte.MaxValue ? OpCodes.Ldarga_S : OpCodes.Ldarga
            : OpCodes.Ldarg;
        il.Emit(opcode, index);
    }
}
