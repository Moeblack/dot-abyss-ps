using System.Security.Cryptography;

namespace DotAbyss.Local.Pc;

internal static class ClientIdentity
{
    internal const string ApplicationVersion = "1.15.0";
    internal const string MetadataHash = "d38f026a769e522b136830785ad2789f834c47127be764f89ab294b4d91f056d";
    internal const string NativeHash = "d32ffc1e7940265e3e3952339f6cc53c190d13923edd8e275705b046a0e1b4bd";
    internal const string PreparedSettingsHash = "e32a51da7fd1607dbf9d13bf24592ec2ebab5cc426b7886dbe769a6bc32f53e9";
    internal const string MasterHash = "ee9f68157917ee9601c539f950aaf98ae9b446a54dac66707bc297746efac523";
    internal const string CatalogVersionHash = "a9ac4a676495720b605313d6f48dfb41";

    internal static void VerifyFiles(string dataPath)
    {
        Check(Path.Combine(dataPath, "il2cpp_data", "Metadata", "global-metadata.dat"), MetadataHash);
        Check(Path.Combine(Path.GetDirectoryName(dataPath)!, "GameAssembly.dll"), NativeHash);
        VerifySettings(dataPath);
    }

    internal static void VerifySettings(string dataPath) => Check(Path.Combine(dataPath, "globalgamemanagers"), PreparedSettingsHash);

    private static void Check(string path, string expected)
    {
        using var input = File.OpenRead(path);
        using var digest = SHA256.Create();
        var actual = Convert.ToHexString(digest.ComputeHash(input)).ToLowerInvariant();
        if (actual != expected) throw new InvalidDataException("Client fingerprint does not match the supported PC build.");
    }
}
