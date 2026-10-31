using AbyssMod;
using AbyssMod.Patches;
using AbyssMod.Services;
using TMPro;
using UnityEngine;

public static class Program
{
    private static int _checks;
    public static void Main()
    {
        var ui = new Dictionary<string, string>
        {
            ["VIP席の設置"] = "VIP席設置",
            ["VIP席設置"] = "VIP包廂設置",
            ["名前を入力"] = "輸入名字",
        };
        var general = new Dictionary<string, string>
        {
            ["探索力上昇"] = "探索力提高",
            ["攻撃間隔{1}秒"] = "攻擊間隔{1}秒",
        };
        Plugin.Trans = new FakeTranslations { Texts = general, Ui = ui };
        TemplateTextMatcher.Rebuild(general, ui);
        AbilityTextMatcher.Rebuild(new() { ["防御力が【{10%}】上昇"] = "防禦力提高【{10%}】" });

        var staticText = new TextMeshProUGUI("探索力上昇");
        GeneralTextPatch.OnUiTextEnable(staticText);
        Equal("探索力提高", staticText.text, "OnEnable did not use the general dictionary");
        var vip = new TextMeshProUGUI("VIP席設置");
        GeneralTextPatch.OnUiTextEnable(vip);
        Equal("VIP包廂設置", vip.text, "A valid exact key was mistaken for another entry's translation");
        var chained = new TextMeshProUGUI("VIP席の設置");
        GeneralTextPatch.OnUiTextEnable(chained);
        GeneralTextPatch.OnUiTextEnable(chained);
        Equal("VIP席設置", chained.text, "The same component was translated twice");

        var polled = new TextMeshProUGUI("攻撃間隔2秒");
        var decorated = new TextMeshProUGUI("<color=red>攻撃間隔2秒</color>");
        var ability = new TextMeshProUGUI("防御力が【10%】上昇");
        ability.gameObject.name = "SkillDescription";
        var input = new TMP_InputField();
        var nickname = new TextMeshProUGUI("VIP席設置") { Input = input };
        var placeholder = new TextMeshProUGUI("名前を入力") { Input = input };
        input.textComponent = nickname;
        UnityEngine.Object.Visible = new TMP_Text[] { staticText, vip, chained, polled, decorated, ability, nickname, placeholder };
        GeneralTextPatch.RefreshVisibleText();
        GeneralTextPatch.RefreshVisibleText();
        Equal("VIP席設置", chained.text, "Polling translated an already translated component twice");
        Equal("攻擊間隔2秒", polled.text, "Kana-free polling skipped an indexed template");
        Equal("<color=red>攻擊間隔2秒</color>", decorated.text, "Polling lost rich text");
        Equal("防禦力提高【10%】", ability.text, "The skill component bypassed its matcher");
        Equal("VIP席設置", nickname.text, "Polling translated user input");
        Equal("輸入名字", placeholder.text, "Input placeholder was unnecessarily excluded");
        GeneralTextPatch.OnUiTextEnable(nickname);
        Equal("VIP席設置", nickname.text, "OnEnable translated user input");
        string value = "VIP席設置";
        GeneralTextPatch.OnSetText(ref value, nickname);
        Equal("VIP席設置", value, "The setter translated user input");
        Equal("VIP席設置", UiTextTranslator.Translate(nickname, value), "Direct UI lookup translated user input");
        value = "探索力上昇";
        GeneralTextPatch.OnSetText(ref value, polled);
        Equal("探索力提高", value, "A reused component did not translate its new text");
        GeneralTextPatch.ResetTranslations();
        Equal("VIP包廂設置", UiTextTranslator.Translate(null, "VIP席設置"), "Exact lookup was not available without a component");
        var destroyed = new TextMeshProUGUI("VIP席の設置");
        GeneralTextPatch.OnUiTextEnable(destroyed);
        GeneralTextPatch.OnUiTextDestroy(destroyed);
        GeneralTextPatch.OnUiTextEnable(destroyed);
        Equal("VIP包廂設置", destroyed.text, "A recycled instance identifier retained an old translation");
        Console.WriteLine($"UI pipeline checks passed: {_checks}");
    }

    private static void Equal(string expected, string actual, string message)
    {
        _checks++;
        if (expected != actual)
            throw new Exception(message + ": " + actual);
    }
}

namespace HarmonyLib
{
    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Method, AllowMultiple = true)]
    public sealed class HarmonyPatch : Attribute
    {
        public HarmonyPatch() { }
        public HarmonyPatch(Type type, string method) { }
    }
    [AttributeUsage(AttributeTargets.Method)] public sealed class HarmonyPrefix : Attribute { }
    [AttributeUsage(AttributeTargets.Method)] public sealed class HarmonyPostfix : Attribute { }
}

namespace UnityEngine
{
    public class Object
    {
        public static TMPro.TMP_Text[] Visible = Array.Empty<TMPro.TMP_Text>();
        public static T[] FindObjectsOfType<T>(bool includeInactive) => Visible.Cast<T>().ToArray();
    }
    public sealed class Transform
    {
        public string name = "Text";
        public Transform parent;
    }
    public sealed class GameObject
    {
        public string name = "Text";
        public Transform transform = new();
    }
}

namespace TMPro
{
    public class TMP_Text : UnityEngine.Object
    {
        private static int _nextId;
        private readonly int _id = ++_nextId;
        public string text;
        public TMP_InputField Input;
        public GameObject gameObject = new();
        public Transform transform => gameObject.transform;
        public TMP_Text(string value) => text = value;
        public int GetInstanceID() => _id;
        public T GetComponentInParent<T>(bool includeInactive) where T : class => Input as T;
    }
    public sealed class TMP_InputField { public TMP_Text textComponent; }
    public sealed class TextMeshProUGUI : TMP_Text
    {
        public TextMeshProUGUI(string value) : base(value) { }
        public void OnEnable() { }
        public void OnDestroy() { }
    }
    public sealed class TextMeshPro : TMP_Text
    {
        public TextMeshPro(string value) : base(value) { }
        public void OnEnable() { }
        public void OnDestroy() { }
    }
}

namespace Project
{
    public static class SkillTextFormatter
    {
        public static void CreateActionSkill() { }
        public static void CreateChainSkill() { }
        public static void CreateAbility() { }
    }
}

namespace AbyssMod
{
    public static class Config
    {
        public static readonly Setting Translation = new(true);
        public static readonly Setting CollectText = new(false);
        public static readonly Setting ClassifyText = new(false);
    }
    public sealed record Setting(bool Value);
    public static class Plugin { public static FakeTranslations Trans; }
    public sealed class FakeTranslations
    {
        public Dictionary<string, string> Names = new();
        public Dictionary<string, string> Texts = new();
        public Dictionary<string, string> Titles = new();
        public Dictionary<string, string> Descriptions = new();
        public Dictionary<string, string> Ui = new();
        public Dictionary<string, string> GetTable(string name) => name == TranslationPaths.UiTexts ? Ui : null;
        public Task EnsureStaticTranslationsLoadedAsync() => Task.CompletedTask;
    }
    public static class Logger { public static void Warn(string message) => throw new Exception(message); }
}

namespace AbyssMod.Services
{
    public static class TranslationPaths
    {
        public const string AbilityDescriptions = "ability_descriptions";
        public const string UiTexts = "ui_texts";
    }
    public static class UiContextHelper { public static string ResolveCategory(TMP_Text text, string value) => null; }
    public static class TextClassifier
    {
        public const string Name = "name";
        public static bool IsActionSkillDescription(string value) => false;
        public static string Classify(string value) => "ui_misc";
    }
    public static class MachineTranslator { public static string Handle(string category, string value) => value; }
    public static class TextCollector { public static void Record(string category, string value) { } }
    public static class TmpTextHelper { public static void TrySetTextDirect(TMP_Text text, string value) => text.text = value; }
}
