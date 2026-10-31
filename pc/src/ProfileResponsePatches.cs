using Absf;
using Il2CppInterop.Runtime;
using Newtonsoft.Json;
using Project.Api;
using UnityEngine;

namespace DotAbyss.Local.Pc;

internal static class ProfileResponsePatches
{
    internal static void Install(Plugin.AddHook add)
    {
        add(typeof(JsonUtility), "FromJsonInternal", new[] { typeof(string), typeof(Il2CppSystem.Object), typeof(Il2CppSystem.Type) },
            typeof(ProfileResponsePatches), nameof(Parse));
        Verify();
    }

    internal static bool Parse(string __0, Il2CppSystem.Object? __1, Il2CppSystem.Type __2, ref Il2CppSystem.Object __result)
    {
        if (__1 is not null || __2?.Pointer != Il2CppType.Of<ProfileTopApiResponseEntity>().Pointer) return true;
        try
        {
            // Unity drops contents because this response inherits a recursive API wrapper.
            var settings = new JsonSerializerSettings
            {
                TypeNameHandling = TypeNameHandling.None,
                MetadataPropertyHandling = MetadataPropertyHandling.Ignore,
                MaxDepth = new Il2CppSystem.Nullable<int>(64)
            };
            __result = JsonConvert.DeserializeObject(__0, __2, settings)
                ?? throw new InvalidDataException("Profile response is empty.");
            return false;
        }
        catch (Exception error)
        {
            Plugin.Info("Profile response parsing failed (" + error.GetType().Name + ").");
            return true;
        }
    }

    private static void Verify()
    {
        foreach (var (following, followers) in new[] { (0, 0), (2, 3) })
        {
            var payload = "{\"contents\":{\"follow_count\":" + following + ",\"follower_count\":" + followers + "}}";
            var response = JsonUtility.FromJson<ProfileTopApiResponseEntity>(payload);
            if (response?.contents is not { } contents || contents.follow_count != following || contents.follower_count != followers)
                throw new InvalidDataException("The native profile response parser did not preserve its contents.");
        }
        Plugin.Info("Native profile response parsing verified: 2 checks passed.");
    }
}
