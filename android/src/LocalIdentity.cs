using Il2CppAbsf;
using Il2CppAbsf.Api;
using GameCancellationToken = Il2CppSystem.Threading.CancellationToken;

namespace DotAbyss.Local;

internal static class LocalIdentity
{
    internal static GameLoginData Install(GameCancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        var session = LocalMod.RequireSession();
        var login = LocalMod.Session!.AuthenticateGameLogin();
        cancellation.ThrowIfCancellationRequested();
        var manager = Engine.Get<ApiManager>()
            ?? throw new InvalidOperationException("The native API manager is not initialized.");
        Il2CppProject.Login._rawUuidCache = login.Uuid;
        Il2CppProject.Login._isCachedRawUuid = true;
        Il2CppProject.Login.SaveLocalUuidIfChanged(login.Uuid);
        Il2CppProject.Login.SetEncryptUuidToken(login.Uuid);
        manager.SetSession(session.Token);
        manager.SetHeader("api-token", session.ApiToken);
        return login;
    }
}
