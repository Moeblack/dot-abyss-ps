using UnityEngine;
using UnityEngine.SceneManagement;

namespace DotAbyss.Local.Pc;

public sealed class LocalObserver : MonoBehaviour
{
    private string previousScene = "";
    public LocalObserver(IntPtr pointer) : base(pointer) { }

    public void Update()
    {
        if (!Plugin.Active) return;
        var scene = SceneManager.GetActiveScene().name;
        if (scene == previousScene) return;
        previousScene = scene;
        Plugin.Info($"Scene changed: {scene}");
        Plugin.Report("scene_changed", new { scene, native_home_scene = scene == "Home" });
    }
}
