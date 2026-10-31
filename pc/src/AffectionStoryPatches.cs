using Absf;
using HarmonyLib;
using Project.Interaction.AdventurerDetail;
using Project.Master.NoaMessagePack;
using Project.User;

namespace DotAbyss.Local.Pc;

internal static class AffectionStoryPatches
{
    private static int failures;

    internal static void Install(Harmony harmony)
    {
        Install(harmony, typeof(StoryListViewController), "__c__DisplayClass9_0");
        Install(harmony, typeof(R18StoryListViewController), "__c__DisplayClass11_0");
    }

    private static void Install(Harmony harmony, Type controller, string closureName)
    {
        var owner = AccessTools.Inner(controller, closureName)
            ?? throw new MissingMemberException(controller.FullName, closureName);
        var method = AccessTools.Method(owner, "_UpdateListView_b__0", new[] { typeof(MNovelCharacters) });
        if (method is null || method.ReturnType != typeof(StoryListThumbnailModel))
            throw new MissingMethodException(owner.FullName, "_UpdateListView_b__0(MNovelCharacters)");
        harmony.Patch(method, postfix: new HarmonyMethod(typeof(AffectionStoryPatches), nameof(Created)));
        Plugin.Info("Hook installed: " + owner.FullName + "." + method.Name);
    }

    internal static void Created(MNovelCharacters __0, StoryListThumbnailModel __result)
    {
        if (!Plugin.Active || __0 is null || __result is null || !Engine.IsBoot) return;
        try
        {
            if (__result.IsExistStory) return;
            var user = Engine.Get<UserData>();
            var userId = user?.UserStatus?.UserId ?? 0;
            if (user is not null && userId > 0 && PreviouslyUnlocked(user, __0, userId))
                __result._IsExistStory_k__BackingField = true;
        }
        catch (Exception error)
        {
            if (Interlocked.Increment(ref failures) <= 3)
                Plugin.Info("Character story unlock synchronization unavailable (" + error.GetType().Name + ").");
        }
    }

    private static bool PreviouslyUnlocked(UserData user, MNovelCharacters definition, long userId)
    {
        var stories = user.NovelCharacterDataStore?._dataList;
        var characters = user.CharaDataStore?._dataList;
        if (stories is null || characters is null) return false;
        var unlocked = false;
        for (var index = 0; index < stories.Count; index++)
        {
            var story = stories[index];
            if (story is not null && story.NovelCharacterId == definition.id && story.Status == 1 && story.UserId == userId)
            {
                unlocked = true;
                break;
            }
        }
        if (!unlocked) return false;
        for (var index = 0; index < characters.Count; index++)
            if (characters[index]?.MCharaId == definition.m_character_id) return true;
        return false;
    }
}
