from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from datetime import datetime

import msgpack

from .admin_master import apply_overrides, atomic_json, editable_field, fingerprint, patch_config, read_overrides
from .admin_store import AdminJournal, record_delta
from .admin_display import DisplayTranslations
from .admin_profiles import ProfileManager
from .gameplay import Gameplay, prepare_progress
from .gameplay_catalog import GAMEPLAY_TABLES, GameplayCatalog, JST
from .gameplay_rules import DEFAULT_RULES, RULE_BOUNDS, load_rules, validate_rules
from .masterdata import inspect_payload, unique_map
from .newgame import POLICY_VERSION, validate_saved_state
from .protocol import validate_data


class AdminError(ValueError):
    def __init__(self, message, status=400, code="invalid_operation"):
        super().__init__(message)
        self.status, self.code = status, code


TIMINGS = {"live": "同步后生效", "page": "刷新页面后生效", "next_battle": "下场战斗生效",
           "next_round": "下轮生效", "master": "重新加载主数据", "relogin": "重新登录后生效"}

TIME_SENSITIVE_ACTIONS = {
    "shop.vip", "subscription.set", "shop.purchase", "shop.login_extend", "currency.paid_edit",
    "character.edit", "character.delete", "character.ability", "character.parameter", "character.gift",
    "research.edit", "research.maximize", "story.complete", "equipment.edit", "equipment.enchantment",
    "equipment.appraisal_apply", "equipment.delete", "party.layout", "party.copy", "party.preset_load",
    "party.delete", "idle.party", "idle.assign", "profile.exp",
}


class AdminService:
    def __init__(self, store, local_state):
        self.store, self.local = store, local_state
        self.journal = AdminJournal(store)
        self.display = DisplayTranslations()
        self.profile_manager = ProfileManager(store, local_state)
        self.rules_path = store.path.parent / "gameplay-rules.json"
        self.overrides_path = store.path.parent / "master-overrides.json"
        self.lock = threading.RLock()
        self.tickets = {}

    def require_local(self):
        if self.local is None:
            raise AdminError("服务未配置游戏主数据和存档结构", 503, "not_configured")
        return self.local._active()

    def config_version(self):
        return fingerprint({"rules": load_rules(self.rules_path), "overrides": read_overrides(self.overrides_path)})

    def load(self, db, profile_id):
        row = db.execute("SELECT g.*,p.name FROM game_states g JOIN profiles p ON p.id=g.profile_id WHERE g.profile_id=?", (profile_id,)).fetchone()
        if row is None:
            raise AdminError("该存档尚未初始化，请先在本地游戏中登录", 404, "profile_not_initialized")
        active = self.require_local()
        if row["schema_version"] != 1 or row["master_revision"] != active["revision"]:
            raise AdminError("存档与当前主数据版本不匹配", 409, "version_mismatch")
        saved = db.execute("SELECT * FROM gameplay_progress WHERE profile_id=?", (profile_id,)).fetchone()
        if saved and saved["master_revision"] != row["master_revision"]:
            raise AdminError("玩法进度与存档版本不匹配", 409, "version_mismatch")
        state, progress = json.loads(row["contents"]), json.loads(saved["contents"]) if saved else {}
        social = {"following": [row[0] for row in db.execute("SELECT followed_id FROM follows WHERE follower_id=? ORDER BY followed_id", (profile_id,))],
                  "followers": [row[0] for row in db.execute("SELECT follower_id FROM follows WHERE followed_id=? ORDER BY follower_id", (profile_id,))]}
        version = fingerprint({"state": state, "progress": progress, "social": social, "master": active["sha256"], "config": self.config_version()})
        return {"profile_id": profile_id, "name": row["name"], "master_revision": row["master_revision"],
                "version": version, "state": state, "progress": progress, "social": social, "sync": self.journal.status(db, profile_id)}

    def snapshot(self, profile_id, display=True):
        with self.store.connection() as db:
            db.execute("BEGIN")
            result = self.load(db, profile_id)
        if display:
            _, catalog = self.local._master_data(result["master_revision"])
            result["record_display"] = self.display.native_records(result["state"]["records"], catalog)
        return result

    def profiles(self):
        return self.profile_manager.list_profiles()

    def status(self):
        return {"service": "dotabyss-console", "version": 1, "enabled": self.local is not None,
                "master": self.store.master(), "timings": TIMINGS,
                "capabilities": {"profile_management": True, "translated_display": True, "launcher": True}}

    def schema(self):
        from .admin_actions import action_specs
        record = self.require_local()
        _, catalog = self.local._master_data(record["revision"])
        actions = action_specs(catalog)
        for operation, label in (("follow", "关注本地玩家"), ("unfollow", "取消关注"), ("remove_follower", "移除本地粉丝")):
            actions.append({"id": "social." + operation, "group": "social", "label": label, "description": "修改本地存档之间的关系，下次打开好友页生效。",
                            "fields": [{"name": "target_id", "label": "目标本地玩家 ID", "type": "integer", "min": 1, "required": True}], "timing": "page"})
        groups = list(dict.fromkeys(item["group"] for item in actions))
        labels = {"currency": "货币与体力", "inventory": "背包材料", "characters": "角色养成", "character": "角色养成",
                  "affection": "好感与羁绊", "appearance": "服装外观", "equipment": "装备魔晶石", "party": "编队预设",
                  "building": "设施研究", "research": "设施研究", "story": "剧情教程", "missions": "任务奖励", "mission": "任务与邮件", "mail": "邮件",
                  "gacha": "卡池与抽卡", "shop": "商城权益", "tavern": "酒馆", "idle": "探索队",
                  "battle": "常驻战斗", "events": "活动复刻", "event": "活动复刻", "profile": "玩家资料", "social": "本地好友"}
        return {"groups": [{"id": key, "label": labels.get(key, key)} for key in groups], "actions": actions, "timings": TIMINGS}

    @staticmethod
    def assert_version(expected, current):
        if not isinstance(expected, str) or not secrets.compare_digest(expected, current):
            raise AdminError("数据已变化，请刷新并重新预览", 409, "version_conflict")

    @staticmethod
    def active_battle(progress):
        return progress.get("features", {}).get("battle", {}).get("data", {}).get("active")

    def validate_social(self, db, profile_id, social, revision):
        if not isinstance(social, dict) or set(social) != {"following", "followers"}:
            raise AdminError("本地好友关系格式无效")
        limit = load_rules(self.rules_path)["friend_limit"]
        for values in social.values():
            if (not isinstance(values, list) or len(values) > 10000 or any(type(value) is not int or value <= 0 or value == profile_id for value in values)
                    or len(values) != len(set(values))):
                raise AdminError("本地好友列表包含无效或重复的玩家")
            for target in values:
                peer = db.execute("SELECT master_revision FROM game_states WHERE profile_id=?", (target,)).fetchone()
                if peer is None or peer[0] != revision:
                    raise AdminError("好友快照引用了不存在或版本不同的玩家")
        if len(social["following"]) > limit:
            raise AdminError("好友快照超过当前关注上限")
        for target in social["followers"]:
            exists = db.execute("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?", (target, profile_id)).fetchone()
            if not exists and db.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (target,)).fetchone()[0] >= limit:
                raise AdminError("恢复关系会超过另一位玩家的关注上限")

    def ticket(self, item):
        now = time.time()
        self.tickets = {key: value for key, value in self.tickets.items() if value["expires_at"] > now}
        while len(self.tickets) >= 24:
            self.tickets.pop(next(iter(self.tickets)))
        token = secrets.token_urlsafe(32)
        item["expires_at"] = int(now) + 300
        self.tickets[token] = item
        return {"ticket": token, "summary": item["summary"], "timing": item["timing"],
                "changes": item.get("changes", []), "warnings": item.get("warnings", []), "expires_at": item["expires_at"]}

    def get_ticket(self, token, kind, profile_id=None):
        if not isinstance(token, str) or not 16 <= len(token) <= 128:
            raise AdminError("预览凭据无效")
        item = self.tickets.get(token)
        if item is None or item["expires_at"] <= time.time():
            raise AdminError("预览已过期，请重新预览", 409, "preview_expired")
        if item["kind"] != kind or item.get("profile_id") != profile_id:
            raise AdminError("预览与当前操作不匹配", 409, "preview_mismatch")
        return item

    def preview(self, profile_id, operation, args, expected_version):
        from .admin_actions import apply_action
        if not isinstance(args, dict) or not isinstance(operation, str):
            raise AdminError("操作和参数格式无效")
        with self.lock:
            before = self.snapshot(profile_id, display=False)
            self.assert_version(expected_version, before["version"])
            if self.active_battle(before["progress"]):
                raise AdminError("请先完成或退出当前战斗，再修改存档", 409, "battle_active")
            after = copy.deepcopy(before)
            if operation in ("social.follow", "social.unfollow", "social.remove_follower"):
                target = args.get("target_id")
                if set(args) != {"target_id"} or type(target) is not int or target <= 0 or target == profile_id:
                    raise AdminError("请选择另一个有效的本地玩家 ID")
                with self.store.connection() as db:
                    peer = db.execute("SELECT master_revision FROM game_states WHERE profile_id=?", (target,)).fetchone()
                if peer is None or peer[0] != before["master_revision"]:
                    raise AdminError("目标玩家未初始化或主数据版本不同")
                values = after["social"]["followers" if operation == "social.remove_follower" else "following"]
                if operation == "social.follow":
                    if target not in values:
                        if len(values) >= load_rules(self.rules_path)["friend_limit"]:
                            raise AdminError("已达到本地好友上限")
                        values.append(target)
                        values.sort()
                elif target in values:
                    values.remove(target)
                result = {"summary": "修改本地好友关系", "timing": "page"}
            elif operation in ("save.restore", "save.import"):
                source = args.get("snapshot")
                if operation == "save.restore":
                    history_id = args.get("history_id")
                    side = args.get("which", "before")
                    if type(history_id) is not int or side not in ("before", "after"):
                        raise AdminError("备份参数无效")
                    with self.store.connection() as db:
                        row = db.execute("SELECT * FROM admin_changes WHERE id=? AND profile_id=?", (history_id, profile_id)).fetchone()
                    if not row:
                        raise AdminError("找不到该存档的备份", 404)
                    source = json.loads(row[side + "_data"])
                if not isinstance(source, dict) or source.get("master_revision") != before["master_revision"]:
                    raise AdminError("只能恢复同版本存档")
                if source.get("profile_id") != profile_id:
                    raise AdminError("备份属于其他存档；不允许直接覆盖身份")
                after["state"], after["progress"] = copy.deepcopy(source["state"]), copy.deepcopy(source["progress"])
                if "social" in source:
                    after["social"] = copy.deepcopy(source["social"])
                    with self.store.connection() as db:
                        self.validate_social(db, profile_id, after["social"], before["master_revision"])
                if self.active_battle(after["progress"]):
                    raise AdminError("不允许恢复正在战斗的快照")
                from .admin_validation import validate_import
                _, catalog = self.local._master_data(before["master_revision"])
                validate_import(Gameplay(after["state"], after["progress"], catalog, profile_id, rules=load_rules(self.rules_path)))
                result = {"summary": "恢复存档快照", "timing": "page", "warnings": ["恢复后刷新页面；如当前菜单仍保留旧状态，请重新登录。"]}
            else:
                _, catalog = self.local._master_data(before["master_revision"])
                game = Gameplay(after["state"], after["progress"], catalog, profile_id,
                                rules=load_rules(self.rules_path))
                result = apply_action(game, operation, args)
            validate_saved_state(after["state"])
            owners = {row["user_id"] for value in after["state"]["records"].values() for row in value["up"] if "user_id" in row}
            if owners != {profile_id}:
                raise AdminError("存档归属校验失败")
            validate_data(after["state"])
            validate_data(after["progress"])
            prepare_progress(copy.deepcopy(after["progress"]), datetime.now(JST))
            self.store._state_json(after["state"])
            self.store._state_json(after["progress"])
            delta = record_delta(before["state"]["records"], after["state"]["records"])
            changes = []
            from .gameplay_records import identity_field
            for table, values in delta.items():
                field = identity_field(table)
                old = {row[field]: row for row in before["state"]["records"].get(table, {}).get("up", [])}
                added = sum(row[field] not in old for row in values["up"])
                examples = [{"id": row[field], "before": old.get(row[field]), "after": row} for row in values["up"][:8]]
                examples.extend({"id": key, "before": old[key], "after": None} for key in values["down"][:8])
                changes.append({"table": table, "added": added, "updated": len(values["up"]) - added,
                                "removed": len(values["down"]), "examples": examples})
            if after["progress"] != before["progress"]:
                changes.append({"table": "玩法进度与奖励记录", "added": 0, "updated": 1, "removed": 0})
            if after["social"] != before["social"]:
                changes.append({"table": "本地好友关系", "before": before["social"], "after": after["social"]})
            return self.ticket({"kind": "profile", "profile_id": profile_id, "before": before, "after": after,
                                "operation": operation, "args": copy.deepcopy(args), "delta": delta, "changes": changes,
                                "summary": result.get("summary", operation), "timing": result.get("timing", "page"),
                                "warnings": result.get("warnings", []) + (["时间相关收益与期限按确认时刻结算。"] if operation in TIME_SENSITIVE_ACTIONS else [])})

    def apply(self, profile_id, token):
        if not isinstance(token, str) or not 16 <= len(token) <= 128:
            raise AdminError("预览凭据无效")
        with self.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT id,profile_id,summary,timing,after_data FROM admin_changes WHERE ticket=?", (token,)).fetchone()
            if prior:
                if prior["profile_id"] != profile_id:
                    raise AdminError("预览与存档不匹配", 409)
                return {"version": self.load(db, profile_id)["version"], "revision": prior["id"], "status": "saved",
                        "summary": prior["summary"], "timing": prior["timing"]}
            item = self.get_ticket(token, "profile", profile_id)
            current = self.load(db, profile_id)
            self.assert_version(item["before"]["version"], current["version"])
            if self.active_battle(current["progress"]):
                raise AdminError("当前正在战斗，请结束后重新预览", 409, "battle_active")
            after = item["after"]
            if item["operation"] in TIME_SENSITIVE_ACTIONS:
                from .admin_actions import apply_action
                after = copy.deepcopy(current)
                _, catalog = self.local._master_data(current["master_revision"])
                game = Gameplay(after["state"], after["progress"], catalog, profile_id, rules=load_rules(self.rules_path))
                apply_action(game, item["operation"], item["args"])
                validate_saved_state(after["state"])
                validate_data(after["progress"])
                item = {**item, "before": current, "after": after,
                        "delta": record_delta(current["state"]["records"], after["state"]["records"])}
            if item["before"]["social"] != after["social"]:
                old, new = item["before"]["social"], after["social"]
                self.validate_social(db, profile_id, new, current["master_revision"])
                for target in set(new["following"]) - set(old["following"]):
                    db.execute("INSERT OR IGNORE INTO follows(follower_id,followed_id) VALUES(?,?)", (profile_id, target))
                for target in set(old["following"]) - set(new["following"]):
                    db.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (profile_id, target))
                for target in set(old["followers"]) - set(new["followers"]):
                    db.execute("DELETE FROM follows WHERE follower_id=? AND followed_id=?", (target, profile_id))
                for target in set(new["followers"]) - set(old["followers"]):
                    db.execute("INSERT OR IGNORE INTO follows(follower_id,followed_id) VALUES(?,?)", (target, profile_id))
            db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (self.store._state_json(after["state"]), profile_id))
            db.execute("INSERT INTO gameplay_progress VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET contents=excluded.contents",
                       (profile_id, current["master_revision"], self.store._state_json(after["progress"])))
            revision = self.journal.append(db, profile_id, token, item["operation"], item["summary"], item["timing"],
                                           item["before"], after, item["delta"])
            version = self.load(db, profile_id)["version"]
            return {"version": version, "revision": revision, "status": "saved", "summary": item["summary"], "timing": item["timing"]}

    def backup(self, profile_id, expected_version):
        with self.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = self.load(db, profile_id)
            self.assert_version(expected_version, snapshot["version"])
            revision = self.journal.append(db, profile_id, secrets.token_urlsafe(32), "save.backup", "手动备份", "relogin", snapshot, snapshot, {}, False)
            return {"id": revision, "status": "backup", "summary": "备份已保存"}

    def rules(self):
        values = load_rules(self.rules_path)
        specs = []
        for name, default in DEFAULT_RULES.items():
            if name == "format_version":
                continue
            spec = {"name": name, "label": name, "default": default,
                    "type": "integer" if type(default) is int else "json"}
            if name in RULE_BOUNDS:
                spec.update(min=RULE_BOUNDS[name][0], max=RULE_BOUNDS[name][1])
            specs.append(spec)
        return {"values": values, "version": self.config_version(), "specs": specs}

    def preview_rules(self, changes, expected_version):
        with self.lock:
            self.assert_version(expected_version, self.config_version())
            if not isinstance(changes, dict) or "format_version" in changes:
                raise AdminError("规则变更格式无效")
            before = load_rules(self.rules_path)
            after = validate_rules({**before, **changes})
            prepared = self.prepare_rule_profiles(after)
            timing = "master" if any(key in changes for key in ("event_open_all", "event_open_ids")) else "page"
            return self.ticket({"kind": "rules", "before": before, "after": after, "version": expected_version,
                                "profiles": prepared,
                                "changes": [{"table": f"存档 {item['before']['name']}", "added": 0,
                                             "updated": sum(len(change["up"]) for change in item["delta"].values()),
                                             "removed": sum(len(change["down"]) for change in item["delta"].values())} for item in prepared],
                                "summary": f"修改 {sum(before[key] != after[key] for key in after)} 项本地规则", "timing": timing,
                                "warnings": ["已有战斗、酒馆轮次和深渊远征保留其生成时的规则；初始补给只影响新存档。", *self.skipped_profile_warnings(prepared)]})

    @staticmethod
    def skipped_profile_warnings(profiles):
        return [f"旧格式存档 {item['before']['name']} 保持原样，迁移后才能应用新规则。"
                for item in profiles if item.get("skipped")]

    def prepare_rule_profiles(self, rules, catalog=None):
        from .gameplay_library import sync_novels
        from .gameplay_effects import settle_before_effect_change
        from .gameplay_idle import reconcile
        from .gameplay_nether_configuration import freeze_configuration
        prepared = []
        profiles = [profile for profile in self.profiles()["profiles"] if profile["has_state"]]
        if not profiles:
            return prepared
        record = self.require_local()
        _, old_catalog = self.local._master_data(record["revision"])
        catalog = old_catalog if catalog is None else catalog
        old_rules = load_rules(self.rules_path)
        for profile in profiles:
            before = self.snapshot(profile["id"], display=False)
            if self.active_battle(before["progress"]):
                raise AdminError(f"存档 {profile['name']} 正在战斗，请结束后再发布规则", 409, "battle_active")
            after = copy.deepcopy(before)
            if after["state"].get("policy_version") != POLICY_VERSION:
                prepared.append({"before": before, "after": after, "delta": {}, "skipped": True})
                continue
            old_game = Gameplay(after["state"], after["progress"], old_catalog, profile["id"], rules=old_rules)
            old_game.prepare()
            freeze_configuration(old_game)
            settle_before_effect_change(old_game)
            game = Gameplay(after["state"], after["progress"], catalog, profile["id"], now=old_game.now, rules=rules)
            game.prepare()
            reconcile(game, settle_pending=False)
            sync_novels(game)
            validate_saved_state(after["state"])
            self.store._state_json(after["state"])
            self.store._state_json(after["progress"])
            prepared.append({"before": before, "after": after, "delta": record_delta(before["state"]["records"], after["state"]["records"])})
        return prepared

    def master_data(self):
        record = self.require_local()
        if self.local.event_master:
            self.local.event_master.current()
            return record, self.local.event_master.base_tables
        _, payload = self.local.masters.read(record["revision"])
        return record, msgpack.unpackb(payload, raw=False, strict_map_key=True, object_pairs_hook=unique_map)

    def master_tables(self):
        _, tables = self.master_data()
        return {"tables": [{"name": name, "count": len(rows), "editable": name in self.local.schema["tables"]}
                           for name, rows in sorted(tables.items())]}

    def master_table(self, name, offset=0, limit=100, query=""):
        record, tables = self.master_data()
        if name not in tables:
            raise AdminError("主表不存在", 404)
        config = read_overrides(self.overrides_path)
        effective, _ = apply_overrides(tables, self.local.schema, record, config)
        fields = self.local.schema["tables"].get(name)
        if fields is None:
            fields = [{"name": f"column_{i}", "type": "string"} for i in range(len(tables[name][0]) if tables[name] else 0)]
        result = []
        lookup = self.display.lookup(name)
        for index, values in enumerate(effective[name]):
            row = dict(zip((field["name"] for field in fields), values))
            display = self.display.row(name, row, lookup)
            if not query or query.casefold() in json.dumps([row, display], ensure_ascii=False).casefold():
                result.append({"index": index, "values": row, "display": display})
        return {"rows": result[offset:offset + limit], "total": len(result), "offset": offset,
                "fields": [{**field, "editable": name in self.local.schema["tables"] and editable_field(field, self.local.schema)} for field in fields],
                "version": fingerprint({"config": self.config_version(), "master": record["sha256"]})}

    def catalog(self, name, offset=0, limit=100, query=""):
        result = self.master_table(name, offset, limit, query)
        return {**result, "rows": [{**row["values"], "_display": row["display"]} for row in result["rows"]]}

    def preview_master(self, table, index, changes, expected_version):
        with self.lock:
            with self.store.connection() as db:
                self.require_idle_profiles(db)
            record, tables = self.master_data()
            version = fingerprint({"config": self.config_version(), "master": record["sha256"]})
            self.assert_version(expected_version, version)
            before = read_overrides(self.overrides_path)
            after = patch_config(before, tables, self.local.schema, record, table, index, changes)
            effective, _ = apply_overrides(tables, self.local.schema, record, after)
            inspect_payload(msgpack.packb(effective, use_bin_type=True), self.local.schema)
            catalog = GameplayCatalog({key: value for key, value in effective.items() if key in GAMEPLAY_TABLES}, self.local.schema)
            prepared = self.prepare_rule_profiles(load_rules(self.rules_path), catalog)
            warnings = ["需要客户端重新加载主数据；已有战斗或远征不会自动重建。"]
            warnings.extend(self.skipped_profile_warnings(prepared))
            if any(name.endswith("_at") for name in changes) and load_rules(self.rules_path)["event_open_all"]:
                warnings.append("当前启用了活动自动复刻，关联活动日期可能被复刻规则覆盖；手动设置档期前请调整活动开放规则。")
            return self.ticket({"kind": "master", "before": before, "after": after, "version": self.config_version(),
                                "profiles": prepared,
                                "master": record["sha256"], "summary": f"修改 {table} 第 {index + 1} 行", "timing": "master",
                                "changes": [{"table": table, "index": index, "before": dict(zip((field["name"] for field in self.local.schema["tables"][table]), tables[table][index])), "after": dict(zip((field["name"] for field in self.local.schema["tables"][table]), effective[table][index]))}],
                                "warnings": warnings})

    def require_idle_profiles(self, db):
        for row in db.execute("SELECT profile_id,contents FROM gameplay_progress"):
            progress = json.loads(row["contents"])
            if self.active_battle(progress):
                raise AdminError(f"存档 {row['profile_id']} 正在战斗，请结束后再发布主数据", 409, "battle_active")
            run = progress.get("features", {}).get("nether", {}).get("data", {}).get("run")
            if run and not run.get("settled"):
                raise AdminError(f"存档 {row['profile_id']} 正在深渊远征，请结束远征后再发布主数据", 409, "expedition_active")

    def apply_config(self, kind, token):
        if not isinstance(token, str) or not 16 <= len(token) <= 128:
            raise AdminError("预览凭据无效")
        with self.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT id,operation,summary,timing FROM admin_changes WHERE ticket=? AND profile_id IS NULL", (token,)).fetchone()
            if prior:
                if prior["operation"] != kind + ".update":
                    raise AdminError("预览类型不匹配", 409)
                return {"revision": prior["id"], "status": "saved", "summary": prior["summary"], "timing": prior["timing"]}
            item = self.get_ticket(token, kind)
            self.assert_version(item["version"], self.config_version())
            if kind in ("rules", "master"):
                stored = {row[0] for row in db.execute("SELECT profile_id FROM game_states")}
                if stored != {item["before"]["profile_id"] for item in item["profiles"]}:
                    raise AdminError("存档列表已变化，请重新预览规则", 409, "version_conflict")
                for prepared in item["profiles"]:
                    self.assert_version(prepared["before"]["version"], self.load(db, prepared["before"]["profile_id"])["version"])
            if kind == "master" and self.require_local()["sha256"] != item["master"]:
                raise AdminError("主数据已变化，请重新预览", 409)
            if kind == "master":
                self.require_idle_profiles(db)
                record, tables = self.master_data()
                effective, _ = apply_overrides(tables, self.local.schema, record, item["after"])
                catalog = GameplayCatalog({key: value for key, value in effective.items() if key in GAMEPLAY_TABLES}, self.local.schema)
                prepared_profiles = self.prepare_rule_profiles(load_rules(self.rules_path), catalog)
            else:
                prepared_profiles = self.prepare_rule_profiles(item["after"])
            path = self.rules_path if kind == "rules" else self.overrides_path
            original = path.read_bytes() if path.exists() else None
            try:
                atomic_json(path, item["after"])
                if self.local and self.local.event_master:
                    self.local.event_master.current()
                revision = self.journal.append(db, None, token, kind + ".update", item["summary"], item["timing"], item["before"], item["after"], {})
                for prepared in prepared_profiles:
                    if prepared.get("skipped"):
                        continue
                    before, after = prepared["before"], prepared["after"]
                    profile_id = before["profile_id"]
                    db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (self.store._state_json(after["state"]), profile_id))
                    db.execute("INSERT INTO gameplay_progress VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET contents=excluded.contents",
                               (profile_id, before["master_revision"], self.store._state_json(after["progress"])))
                    if prepared["delta"] or before["progress"] != after["progress"]:
                        self.journal.append(db, profile_id, token + ":" + str(profile_id), kind + ".refresh", "同步规则派生状态", "page", before, after, prepared["delta"])
                db.commit()
            except BaseException:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_json(path, json.loads(original))
                raise
            return {"revision": revision, "status": "saved", "summary": item["summary"], "timing": item["timing"], "version": self.config_version()}
