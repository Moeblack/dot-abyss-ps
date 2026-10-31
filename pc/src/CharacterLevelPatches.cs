using Absf;
using Project.Api;
using Project.User;

namespace DotAbyss.Local.Pc;

internal static class CharacterLevelPatches
{
    private static readonly CharacterLevelProjection projection = new();
    private static Exception? failure;

    internal static void Install(Plugin.AddHook add) => add(typeof(UserData), "Apply", new[] { typeof(RecordEntity) },
        typeof(CharacterLevelPatches), nameof(Applied), true);

    internal static void Applied(UserData __instance, RecordEntity __0)
    {
        if (!Plugin.Active || __instance is null || __0 is null) return;
        try
        {
            if (!Engine.IsBoot || Engine.Get<UserData>()?.Pointer != __instance.Pointer) return;
            var userId = __instance.UserStatus?.UserId ?? 0;
            var characters = __instance.CharaDataStore?._dataList;
            if (userId <= 0 || characters is null) return;
            var current = new List<CharacterLevel>();
            var updates = new List<CharacterLevel>();
            for (var index = 0; index < characters.Count; index++)
            {
                var character = characters[index];
                current.Add(new CharacterLevel(character.Id, character.ParameterData.Lv));
            }
            var incoming = __0.t_characters?.up;
            if (incoming is not null)
                foreach (var character in incoming)
                    updates.Add(new CharacterLevel(character.id, character.level));
            var skills = __instance.BuildingDataStore?.GetBuildingSkillData();
            var unlocked = false;
            if (skills is not null)
                for (var index = 0; index < skills.Count; index++)
                    unlocked |= skills[index].SkillType == 11001 && skills[index].SkillLevel >= 1;
            var synchronizedLevel = unlocked ? __instance.LevelSynchroDataStore?.GetLevelSynchroData()?.Level ?? 0 : 0;
            var levels = projection.Apply(__instance.Pointer.ToInt64(), userId, updates, current, unlocked, synchronizedLevel);
            for (var index = 0; index < levels.Length; index++) characters[index].ParameterData.Lv = levels[index].Level;
            failure = null;
        }
        catch (Exception error)
        {
            failure = error;
            Plugin.Info("Player level synchronization failed (" + error.GetType().Name + ").");
        }
    }

    internal static void VerifyApplied()
    {
        if (failure is not null) throw new InvalidOperationException("Player level synchronization failed.", failure);
    }
}
