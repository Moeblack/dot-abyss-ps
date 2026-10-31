using AbyssMod.Services;
using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;

int checks = 0;
void Check(bool condition, string message)
{
    checks++;
    if (!condition)
        throw new Exception(message);
}
LocalTextMatcher Matcher(LocalTemplateKind kind, params (string Source, string Target)[] entries) =>
    new(entries.Select(entry => new KeyValuePair<string, string>(entry.Source, entry.Target)), kind);
void Match(LocalTextMatcher matcher, string input, string expected)
{
    Check(matcher.TryTranslate(input, out string translated), "No match: " + input);
    Check(translated == expected, $"Wrong translation: {input}\nExpected: {expected}\nActual: {translated}");
}
void Miss(LocalTextMatcher matcher, string input) => Check(!matcher.TryTranslate(input, out _), "Unsafe match: " + input);

var tables = new Dictionary<string, Dictionary<string, string>>
{
    ["ability_descriptions"] = new(),
    ["m_ability_details"] = new() { ["能力{1}"] = "能力{1}", ["共通"] = "新版" },
    ["m_character_action_skills"] = new() { ["行動{[VALUE]}"] = "行動{[VALUE]}", ["共通"] = "舊版" },
};
var abilities = LocalTranslationTables.Abilities(name => tables.GetValueOrDefault(name));
Check(abilities.Count == 3 && abilities["共通"] == "新版", "Modern skill dictionaries were shadowed");
Check(LocalTranslationTables.WithoutAbilities(new() { ["能力{1}"] = "能力{1}", ["攻撃{1}"] = "攻擊{1}" }, abilities).Count == 1,
    "Literal ability values leaked into indexed templates");

var ability = Matcher(LocalTemplateKind.Ability,
    ("30秒に1回、自身に紋章：情熱を【{1}】付与", "每30秒1次，自身取得紋章：情熱【{1}】"),
    ("防御力が【{10％}】上昇", "防禦力提高【{10％}】"),
    ("自身の前方に【3HIT / 合計{[DAMAGE.PERCENT]}】ダメージ", "對自身前方造成【3HIT / 合計{[DAMAGE.PERCENT]}】傷害"),
    ("炎上【{[ABNORMAL.DURATION]秒} / 成功率:50%】を付与", "附加灼燒【{[ABNORMAL.DURATION]秒} / 成功率:50%】"),
    ("攻撃{[ATTACK]} / 防御{[DEFENCE]}", "防禦{[DEFENCE]} / 攻擊{[ATTACK]}"),
    ("攻撃{[ATTACK]} / 再度{[ATTACK]}", "攻擊{[ATTACK]} / 再次{[ATTACK]}"));
Match(ability, "30秒に1回、自身に紋章：情熱を【1】付与", "每30秒1次，自身取得紋章：情熱【1】");
Miss(ability, "30秒に1回、自身に紋章：情熱を【2】付与");
Miss(ability, "31秒に1回、自身に紋章：情熱を【1】付与");
Match(ability, "防御力が【10%】上昇", "防禦力提高【10%】");
Match(ability, "自身の前方に【3HIT / 合計31.4%】ダメージ", "對自身前方造成【3HIT / 合計31.4%】傷害");
Miss(ability, "自身の前方に【4HIT / 合計31.4%】ダメージ");
Match(ability, "炎上【10秒 / 成功率:50%】を付与", "附加灼燒【10秒 / 成功率:50%】");
Miss(ability, "炎上【10秒 / 成功率:60%】を付与");
Match(ability, "攻撃20% / 防御30%", "防禦30% / 攻擊20%");
Match(ability, "攻撃20% / 再度20%", "攻擊20% / 再次20%");
Miss(ability, "攻撃20% / 再度30%");
Match(ability, "攻撃<color=#ff0000>20%</color> / 防御30%", "防禦30% / 攻擊<color=#ff0000>20%</color>");
Match(ability, "炎上【<color=red>10秒</color> / 成功率:50%】を付与", "附加灼燒【<color=red>10秒</color> / 成功率:50%】");
Match(ability, "炎上【<color=red>10</color>秒 / 成功率:50%】を付与", "附加灼燒【<color=red>10</color>秒 / 成功率:50%】");
Miss(ability, "攻撃<sprite=1>20% / 防御30%");
Miss(ability, "攻撃<color=red>20%</size> / 防御30%");

var generic = Matcher(LocalTemplateKind.Indexed,
    ("攻撃間隔{1}秒", "攻擊間隔{1}秒"),
    ("10回攻撃ごとに15秒間自身の連撃率【{1}%】UP", "每攻擊10次，15秒內自身連擊率提高【{1}%】"),
    ("攻撃力が{0:P0}上昇する", "攻擊力提高{0:P0}"),
    ("所持{0:N0}、番号{1:D2}", "編號{1:D2}、持有{0:N0}"),
    ("使用{0:N0}、再確認{0:N0}", "使用{0:N0}、再次確認{0:N0}"),
    ("攻撃{0}\n防御{1}", "攻擊{0}\n防禦{1}"),
    ("確認", "確認內容"));
Match(generic, "攻撃間隔2秒", "攻擊間隔2秒");
Match(generic, "10回攻撃ごとに15秒間自身の連撃率【8%】UP", "每攻擊10次，15秒內自身連擊率提高【8%】");
Miss(generic, "11回攻撃ごとに15秒間自身の連撃率【8%】UP");
Match(generic, "攻撃力が25%上昇する", "攻擊力提高25%");
Match(generic, "攻撃力が２５％上昇する", "攻擊力提高２５％");
Match(generic, "所持1,200、番号03", "編號03、持有1,200");
Match(generic, "使用1,200、再確認1,200", "使用1,200、再次確認1,200");
Miss(generic, "使用1,200、再確認1,300");
Match(generic, "攻撃10\r\n防御20", "攻擊10\n防禦20");
Match(generic, "攻撃10\\n防御20", "攻擊10\n防禦20");
Match(generic, "<size=24><color=#fff>攻撃間隔2秒</color></size>", "<size=24><color=#fff>攻擊間隔2秒</color></size>");
Match(generic, "<b>確認</b>", "<b>確認內容</b>");
Miss(generic, "<link=account>確認</link>");
Miss(generic, "<sprite=0>確認");
Miss(generic, new string('9', 9000));

var strings = Matcher(LocalTemplateKind.Indexed,
    ("「{0}」をフォローしました。", "已追蹤「{0}」。"),
    ("ゲストアカウントの状態では{0}を行うことができません。", "訪客帳號無法進行{0}。"),
    ("選択した「{0}」を「{0}」として登録します。", "將選擇的「{0}」登記為「{0}」。"),
    ("装備の{0}と{1}を設定", "設定裝備{0}與{1}"),
    ("\n<color={0}>・お一人様{1}回限り</color>", "\n<color={0}>・每人限{1}次</color>"),
    ("{0}", "任意文字{0}"),
    ("確認した{0}{1}を使用", "使用確認的{0}{1}"),
    ("<b>{0}</b>", "<b>任意文字{0}</b>"));
Match(strings, "「Alice_千夏123」をフォローしました。", "已追蹤「Alice_千夏123」。");
Match(strings, "ゲストアカウントの状態ではデータ連携を行うことができません。", "訪客帳號無法進行データ連携。");
Match(strings, "選択した「Alice」を「Alice」として登録します。", "將選擇的「Alice」登記為「Alice」。");
Match(strings, "装備の劍と盾を設定", "設定裝備劍與盾");
Miss(strings, "装備の劍と盾と杖を設定");
Miss(strings, "選択した「Alice」を「Bob」として登録します。");
Miss(strings, "「Alice\nBob」をフォローしました。");
Miss(strings, "「<b>Alice</b>」をフォローしました。");
Miss(strings, "「" + new string('名', 129) + "」をフォローしました。");
Miss(strings, "123");
Miss(strings, "確認した123を使用");
Miss(strings, "<b>123</b>");
Match(strings, "\n<color=#4cf37b>・お一人様3回限り</color>", "\n<color=#4cf37b>・每人限3次</color>");
Miss(strings, "\n<color=red onclick=run>・お一人様3回限り</color>");
Miss(generic, "攻撃力が大量上昇する");

var ambiguity = Matcher(LocalTemplateKind.Indexed, ("攻撃{0}", "攻擊{0}"), ("攻撃{1}", "傷害{1}"));
Miss(ambiguity, "攻撃20");
var normalizedConflict = Matcher(LocalTemplateKind.Indexed, ("攻撃\r\n防御", "譯文甲"), ("攻撃\n防御", "譯文乙"));
Miss(normalizedConflict, "攻撃\n防御");
var changedIdentity = Matcher(LocalTemplateKind.Indexed, ("攻撃{0:P0}", "攻擊{0:N0}"));
Miss(changedIdentity, "攻撃25%");
var lostParameter = Matcher(LocalTemplateKind.Indexed, ("攻撃{0}防御{1}", "攻擊{0}"));
Miss(lostParameter, "攻撃20防御30");
var revisions = new LocalTextRevisions();
revisions.Record(1, "VIP席の設置", "VIP席設置");
Check(revisions.IsCurrent(1, "VIP席設置") && !revisions.IsCurrent(2, "VIP席設置"), "UI translations were not tracked per component");
revisions.Clear();
Check(!revisions.IsCurrent(1, "VIP席設置"), "Reload did not clear UI revisions");

if (args.Length > 0)
{
    string root = Path.GetFullPath(args[0]);
    var real = new Dictionary<string, string>();
    foreach (string table in new[] { "m_ability_details", "m_character_action_skills", "m_enemy_skills" })
    {
        string path = Path.Combine(root, table, "zh_Hant.json");
        if (File.Exists(path))
            foreach (var pair in JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(path)))
                real[pair.Key] = pair.Value;
    }
    var watch = Stopwatch.StartNew();
    var corpus = new LocalTextMatcher(real, LocalTemplateKind.Ability);
    int matched = 0;
    foreach (var pair in real.Where(pair => pair.Key.Length > 0 && pair.Value.Length > 0))
    {
        Check(corpus.TryTranslate(pair.Key, out var translated), "Real exact key was lost: " + pair.Key);
        Check(translated == LocalTextMatcher.NormalizeLines(pair.Value), "Real exact value changed");
        matched++;
    }
    Miss(corpus, "30秒に1回、自身に紋章：情熱を【999999】付与");
    Console.WriteLine($"Real dictionary exact checks: {matched}; {watch.ElapsedMilliseconds} ms");
}
if (args.Length > 1)
{
    string overlays = Path.GetFullPath(args[1]);
    int formatted = 0;
    int ambiguous = 0;
    foreach (string table in new[] { "m_ability_details", "m_character_action_skills", "m_enemy_skills", "m_character_abilities", "ui_texts" })
    {
        string file = Path.Combine(overlays, table, "zh_Hant.json");
        if (!File.Exists(file))
            continue;
        var additions = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(file));
        string upstreamFile = Path.Combine(Path.GetFullPath(args[0]), table, "zh_Hant.json");
        var merged = File.Exists(upstreamFile)
            ? JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(upstreamFile))
            : new Dictionary<string, string>();
        foreach (var pair in additions)
        {
            if (!merged.TryGetValue(pair.Key, out var existing) || string.IsNullOrEmpty(existing) || existing == pair.Key)
                merged[pair.Key] = pair.Value;
        }
        var kind = table is "m_ability_details" or "m_character_action_skills" or "m_enemy_skills" ? LocalTemplateKind.Ability : LocalTemplateKind.Indexed;
        var corpus = new LocalTextMatcher(merged, kind);
        foreach (var pair in additions)
        {
            string expected = merged[pair.Key];
            Match(corpus, pair.Key, LocalTextMatcher.NormalizeLines(expected));
            string input = Render(pair.Key, kind);
            if (input == LocalTextMatcher.NormalizeLines(pair.Key))
                continue;
            string formattedExpected = Render(expected, kind);
            Match(Matcher(kind, (pair.Key, expected)), input, formattedExpected);
            var status = corpus.Match(input, out var output);
            if (status == LocalTextMatchStatus.Ambiguous)
                ambiguous++;
            else
            {
                Check(status == LocalTextMatchStatus.Matched, $"Supplement formatted lookup failed ({status}): {input}");
                Check(output == formattedExpected, "Supplement formatted lookup selected the wrong entry: " + input);
            }
            formatted++;
        }
    }
    Console.WriteLine($"Supplement formatted checks: {formatted}; ambiguous combined lookups rejected: {ambiguous}");
}
Console.WriteLine($"Translation runtime checks passed: {checks}");

static string Render(string template, LocalTemplateKind kind)
{
    string normalized = LocalTextMatcher.NormalizeLines(template);
    return Regex.Replace(normalized, @"\{([^{}]+)\}", match =>
    {
        string value = match.Groups[1].Value;
        if (value.StartsWith('['))
        {
            int closing = value.IndexOf(']');
            string suffix = value.Substring(closing + 1);
            string number = value.Contains("DURATION", StringComparison.Ordinal) || suffix == "秒" ? "7" : "31.4%";
            return number + suffix;
        }
        if (kind == LocalTemplateKind.Ability && Regex.IsMatch(value, @"^[+\-]?[0-9]+(?:\.[0-9]+)?[%％]?$"))
            return value;
        if (Regex.IsMatch(value, @"^[0-9]+(?::[^{}]+)?$"))
            return normalized.Substring(0, match.Index).EndsWith("<color=", StringComparison.OrdinalIgnoreCase) ? "#4cf37b"
                : value.Contains(":P", StringComparison.OrdinalIgnoreCase) ? "23%"
                : value.Contains(":N", StringComparison.OrdinalIgnoreCase) ? "1,234"
                : value.Contains(":D", StringComparison.OrdinalIgnoreCase) ? "07" : "17";
        return match.Value;
    });
}
