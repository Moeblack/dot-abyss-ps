using System.Security.Cryptography;
using UnityEngine;

namespace DotAbyss.Observer.Pc;

internal static class ClientIdentity
{
    internal const string ApplicationVersion = "1.15.0";
    internal const string ProductName = "ドットアビスX";
    internal const string LocalPluginId = "local.dotabyss.pc";
    internal const string MetadataHash = "d38f026a769e522b136830785ad2789f834c47127be764f89ab294b4d91f056d";
    internal const string NativeHash = "d32ffc1e7940265e3e3952339f6cc53c190d13923edd8e275705b046a0e1b4bd";
    internal const string UnityPlayerHash = "d935627d3ac843293f1c51ee9d85538191f0cb98def4271c1a29302b52a021d4";

    internal static string Verify()
    {
        var dataPath = Path.GetFullPath(Application.dataPath);
        var root = Path.GetDirectoryName(dataPath) ?? throw new InvalidDataException();
        if (Application.version != ApplicationVersion || Application.productName != ProductName
            || File.Exists(Path.Combine(root, "local-client.json")))
            throw new InvalidDataException();
        Check(Path.Combine(root, "GameAssembly.dll"), NativeHash);
        Check(Path.Combine(root, "UnityPlayer.dll"), UnityPlayerHash);
        Check(Path.Combine(root, "ドットアビスX.exe"), "7a3303bb9c358d9ec1b9ba9b48b04edf1ef2b2571c6aa140694b026e1652bd6d");
        Check(Path.Combine(dataPath, "globalgamemanagers"), "97738fcede2ae1628a978f00b2b41e098217c0214db91c329e274a88cf6cc30b");
        Check(Path.Combine(dataPath, "il2cpp_data", "Metadata", "global-metadata.dat"), MetadataHash);
        return root;
    }

    internal static bool IsWithin(string candidate, string root)
    {
        var full = Path.GetFullPath(candidate).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var directory = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return full.Equals(directory, StringComparison.OrdinalIgnoreCase)
            || full.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    private static void Check(string path, string expected)
    {
        using var file = File.OpenRead(path);
        using var hash = SHA256.Create();
        if (!Convert.ToHexString(hash.ComputeHash(file)).Equals(expected, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException();
    }
}
