using Project.Api;
using Project.Title;
using TermsVersions = Il2CppSystem.Collections.Generic.List<Project.Api.STermsVersionsEntity>;

namespace DotAbyss.Local.Pc;

internal static class TermsPatches
{
    private static int emptyCatalogReported;

    internal static void Install(Plugin.AddHook add)
        => add(typeof(TopScene), "CheckTermsVersion", new[] { typeof(TermsFetchResponseEntity), typeof(TermsFetchAgreementsResponseEntity) },
            typeof(TermsPatches), nameof(CheckTermsVersion));

    internal static bool CheckTermsVersion(TermsFetchResponseEntity? __0, TermsFetchAgreementsResponseEntity? __1, ref TermsVersions __result)
    {
        try
        {
            if (__0?.s_terms_versions is not { Length: 0 } || __1?.t_terms_agreements is null) return true;
            __result = new TermsVersions();
            if (Interlocked.Exchange(ref emptyCatalogReported, 1) == 0)
                Plugin.Info("The local terms catalog is empty; no terms versions require agreement.");
        }
        catch (Exception error) { Plugin.Stop(error); }
        return false;
    }
}
