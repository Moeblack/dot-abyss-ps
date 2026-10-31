from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import msgpack

from dotabyss_ps.gameplay_catalog import JST, available

DOMAINS = {
    "角色与养成": "character interaction level-synchro",
    "装备与道具": "equipment item",
    "编队": "party",
    "主线、关卡与剧情": "main-story chapter-story chapter-quest chapter-view quest dungeon novel library",
    "活动与复刻": "event event-mission side-story",
    "战斗模式与训练": "disaster nether nether-mission training-center union-request battle-session",
    "挂机探索": "idle-exploration",
    "酒馆": "tavern",
    "研究与建设任务": "research plan-mission",
    "任务、通行证与邮件": "mission special-mission mission-pass present visitor-mission",
    "卡池": "gacha",
    "商店、兑换与权益": "exchange shop payment payment-subscription charge-center vip serial license",
    "好友与个人资料": "friend profile",
    "主页与引导": "home mypage user feature-lock feature-tip tutorial",
    "平台与基础服务": "dmm ios inherit login uuid maintenance version terms withdraw push-notification health-check",
    "开发与调试": "debug {ApiName}",
}
EVENT_LABELS = {1: "采矿", 2: "凶化厄灾", 3: "剧情", 4: "寻宝棋盘", 5: "狩猎", 6: "箱池", 7: "委托", 8: "养成", 9: "竞速"}
EVENT_ROUTE_GROUPS = {1: "miner", 2: "disaster", 3: "story", 4: "treasure-board", 5: "hunt", 6: "box-gacha", 7: "commission", 8: "training", 9: "speedrun"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def matches(template, route):
    pattern = re.escape(template.strip("/"))
    pattern = re.sub(r"\\\{\d+\\\}", "[^/]+", pattern)
    return re.fullmatch(pattern, route.strip("/")) is not None


def domain(route):
    parts = route.split("/")
    group = parts[1] if parts[0] == "api" and len(parts) > 1 else parts[0]
    return next((name for name, groups in DOMAINS.items() if group in groups.split()), "未分类"), group


def build(project, root, capture):
    contracts = load(root / "api-contracts.json")
    captured = Counter()
    for path in (capture / "records").glob("*.json"):
        record = load(path)
        captured[(record["method"], record["route"].strip("/"))] += 1
    support = {row["route"].strip("/"): row for row in load(project / "artifacts/pc-research/sampled-interface-coverage.json")["routes"]}
    support["api/health-check"] = {"status": "local_complete", "scope": "Local health response."}
    routes, unresolved = {}, []
    for request in contracts["requests"]:
        if not request["verified"]:
            unresolved.append({"request_type": request["request_type"], "reason": request["unresolved_reason"]})
            continue
        key = request["method"], request["route"]
        if key not in routes:
            label, group = domain(request["route"])
            sampled = sum(n for (method, route), n in captured.items() if method == key[0] and matches(key[1], route))
            routes[key] = {"method": key[0], "route": key[1], "domain": label, "group": group, "request_types": [], "samples": sampled,
                           "route_kind": request["route_kind"], "server_support": support.get(key[1], {"status": "not_in_implemented_inventory"})}
        routes[key]["request_types"].append(request["request_type"])
    missing = [{"method": method, "route": route} for method, route in captured
               if not any(method == key[0] and matches(key[1], route) for key in routes)]
    if missing:
        raise ValueError("Captured requests have no matching native method/route: " + str(missing))
    grouped = defaultdict(list)
    for row in routes.values():
        grouped[row["domain"]].append(row)
    groups = [{"domain": name, "routes": sorted(rows, key=lambda r: (r["route"], r["method"])),
               "route_count": len(rows), "sampled_routes": sum(r["samples"] > 0 for r in rows),
               "server_support_counts": dict(Counter(r["server_support"]["status"] for r in rows))}
              for name, rows in grouped.items()]

    content = load(project / "artifacts/pc-content/index.json")
    schema = load(project / "artifacts/master-schema-pc-1.15.0.json")
    blob = project / "data-pc-1.15.0/master/blobs" / (content["source_master_sha256"] + ".msgpack")
    if hashlib.sha256(blob.read_bytes()).hexdigest() != content["source_master_sha256"]:
        raise ValueError("Master fingerprint mismatch")
    data = msgpack.unpackb(blob.read_bytes(), raw=False)
    events = [dict(zip([f["name"] for f in schema["tables"]["m_events"]], row)) for row in data["m_events"]]
    enum = next(e for e in load(root / "api-type-index.json")["enums"] if e["type"] == "Project.Master.EventType")
    names = {e["value"]: e["name"] for e in enum["values"]}
    now = datetime.now(JST)
    event_tables = {}
    for name, fields in schema["tables"].items():
        columns = [f["name"] for f in fields]
        if "m_event_id" in columns and name in data:
            event_tables[name] = Counter(row[columns.index("m_event_id")] for row in data[name])
    for event in events:
        event.update(type_name=names.get(event["type"], "unknown"), type_label=EVENT_LABELS.get(event["type"], "未知"),
                     within_play_window=available(event, now, "open_at", "end_at"), within_display_window=available(event, now, "open_at", "close_at"),
                     direct_related_tables={name: counts[event["id"]] for name, counts in event_tables.items() if counts[event["id"]]})
    event_types = [{"id": key, "name": name, "label": EVENT_LABELS.get(key), "config_count": sum(e["type"] == key for e in events),
                    "routes": [r for r in routes.values() if r["route"].startswith("api/event/" + EVENT_ROUTE_GROUPS[key] + "/")]}
                   for key, name in names.items()]
    api_flow = load(root / "api-flow-functions/index.json")
    battle = load(root / "battle-functions-typed/index.json")
    warning_files = {name: [path.name for path in (root / name).glob("*.c")
                            if "output may be wrong" in path.read_text(encoding="utf-8")]
                     for name in ("api-flow-functions", "battle-functions-typed")}
    report = {"checked_at": now.isoformat(), "master_sha256": content["source_master_sha256"], "master_tables": len(data),
              "bound_master_tables": len(schema["tables"]), "unbound_master_tables": sorted(set(data) - schema["tables"].keys()),
              "request_type_count": contracts["request_types"], "bound_request_types": contracts["verified"], "unique_method_routes": len(routes),
              "captured_routes": len(captured), "capture_contract_mismatches": missing, "domains": groups, "unresolved_routes": unresolved,
              "event_types": event_types, "events": events, "event_date_scope": "JST time windows from this pinned master snapshot, not an online availability check or proof of local gameplay support.",
              "api_flow_functions": {"total": len(api_flow), "pseudocode": sum("error" not in r for r in api_flow), "failures": [r for r in api_flow if "error" in r]},
              "battle_functions": {"total": len(battle), "pseudocode": sum("error" not in r for r in battle)},
              "decompiler_warning_files": warning_files,
              "server_scope": "Source implementation inventory; this analysis did not change gameplay handlers or verify their live reload."}
    save(root / "feature-report.json", report)
    return report


def markdown(report):
    lines = ["首次全局分析已完成。本报告覆盖客户端静态接口、接口调用函数、战斗计算入口和当前主数据中的活动配置。它不等于完整服务端实现。", "",
             f"已检查 {report['request_type_count']} 个请求类型，确认 {report['bound_request_types']} 个类型的路由及方法，对应 {report['unique_method_routes']} 个方法与路径组合。采样的 {report['captured_routes']} 条接口均与静态映射匹配。", "",
             "另一个请求是 `WafCaptchaDebugApiRequestEntity`：地址和 HTTP 方法由调用者传入，不应登记为固定业务接口。固定映射中也包含开发模板 `{ApiName}` 和调试路径，不能把数量理解成同等数量的可玩功能。", "",
             "接口调用与异步处理：641 个函数中 640 个生成伪代码；`UserApiDataStore.RequestAsync` 保留汇编，其实际异步主体 `MoveNext` 已生成伪代码。响应候选类型目前按命名关联，序列化键、必要字段和调用点仍需结合代码验证。", "",
             "**功能范围**", "", "| 功能域 | 方法／路径组合 | 已采样 | 本轮实现清单中的状态 |", "|---|---:|---:|---|"]
    for group in report["domains"]:
        statuses = ", ".join(f"{k}={v}" for k, v in group["server_support_counts"].items())
        lines.append(f"| {group['domain']} | {group['route_count']} | {group['sampled_routes']} | {statuses} |")
    lines += ["", "`local_complete` 表示已完成声明范围内的本地功能；`partial` 是局部实现；`fixture` 是占位；`blocked` 是明确拒绝执行；`not_in_implemented_inventory` 表示未列入当前实现清单。以上均不是本轮实机验证结果。", "",
              "**活动类型**", "", "当前主表包含 30 套活动配置，分属以下 9 类。类型存在和配置齐全不代表玩法已实现。", "",
              "| 类型 | 客户端枚举 | 配置数 | 对应方法／路径组合 |", "|---|---|---:|---:|"]
    for event in report["event_types"]:
        lines.append(f"| {event['label']} | `{event['name']}` | {event['config_count']} | {len(event['routes'])} |")
    lines += ["", f"检查时间：{report['checked_at']}。下表时间采用主数据的日本时间；仅按快照中的时间窗口判断，不代表已查询官服状态。", "",
              "| 活动 | ID | 类型 | 玩法结束 | 页面关闭 | 时间窗口 |", "|---|---:|---|---|---|---|"]
    for event in report["events"]:
        if event["within_display_window"]:
            status = "玩法开放期" if event["within_play_window"] else "结束后的保留期"
            name = event["name"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {name} | {event['id']} | {event['type_label']} | {event['end_at']} | {event['close_at']} | {status} |")
    lines += ["", "完整 30 套活动的开放、结束、关闭时间，以及直接关联的事件表行数、兑换分类、任务分组和卡池编号，见 `feature-report.json`。", "",
              "**战斗分析**", "", "已导出 44 个战斗相关函数并核对部分原生调用签名。常规伤害主干位于 `UnitDamageCalculator.Calculate`，其客户端计算包含：", "",
              "- `max(1, Attack) / sqrt(max(1, Defence))`；", "- 技能倍率、暴击倍率、弱点／属性修正、伤害修正和关卡修正；", "- 伤害修正和关卡修正分别取不小于 0 的值；", "- 属性抗性项为 `clamp(1000 - resistance, 0, 1000) / 1000`；", "- 随机浮动参数为 `0.95` 到 `1.05`，最终向下取整写入伤害值。", "",
              "这仅描述该常规伤害计算器。固定伤害、当前／最大生命比例伤害、护盾和魔晶石另有计算器，不能统一套用同一个公式。泛型调用、状态来源和特殊关卡修正仍需逐项核对，不能把所有伪代码直接当作可运行源码。", "",
              "`stage_detail` 与 `party_detail` 有独立 JSON 解析入口。探索和厄灾使用不同的关卡详情类型；探索详情继续构造楼层、敌人、资源和掉落模型。服务端仍需提供正确的实例数据，并实现扣费、战斗会话、首次通关和奖励结算。", "",
              f"反编译器警告已单独登记：接口流程 {len(report['decompiler_warning_files']['api-flow-functions'])} 个文件、战斗部分 {len(report['decompiler_warning_files']['battle-functions-typed'])} 个文件。受影响的变量分配或类型推断需回到汇编核对。", "",
              f"主数据共 {report['master_tables']} 张表，其中 {report['bound_master_tables']} 张已有字段绑定。未绑定表、未实现玩法、未覆盖运行分支均保留在机器可读清单中。", "",
              "**结果文件**", "", "- `api-contracts.json`：每个请求的字段、路由、方法、原生地址和解析证据。", "- `api-flow-functions/`：接口调用与异步状态机的伪代码、汇编、调用方和被调用方。", "- `battle-functions-typed/`：战斗函数及调用链；`battle-layouts.json` 保存相关字段偏移。", "- `feature-report.json`：全功能和活动配置清单。", "- `applied-native-prototypes.json`：已应用与跳过的原生类型声明。", "",
              "分析基于隔离副本，不修改官方客户端、玩家存档或服务端玩法代码。新增版本先核对输入指纹，再重新导出元数据、运行 getter 绑定和对比报告；新玩法仍需实现业务规则。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    root = args.analysis or args.project / "artifacts/ida/pc-1.15.0"
    capture = args.capture or args.project / "captures/20261031-025842598-a3a7a9e5c32249308ff906a6c0089b4f"
    report = build(args.project, root, capture)
    (root / "analysis-report.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("request_type_count", "bound_request_types", "unique_method_routes", "captured_routes", "master_tables", "bound_master_tables")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
