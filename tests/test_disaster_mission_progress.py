import copy
import json

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_statistics import record
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import edit, prepare


def test_imported_disaster_highest_level_survives_patrol_and_missing_statistics(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    call(context, "api/training-center")
    edit(context, lambda state, _: state["records"]["t_disaster_statuses"]["up"][0].update(
        disaster_level=1, disaster_level_record_high=12))
    state, progress = map(json.loads, snapshot(context))
    progress["features"]["statistics"]["data"]["totals"].clear()
    progress["features"]["statistics"]["data"]["days"].clear()
    missions = Missions(state, progress, GameplayCatalog(context.tables, context.schema), NOW, context.identity["user_id"])
    mission = {"situation_type": 160000, "target_id": 0, "sub_target_id": 0, "mission_type": 1}
    before = copy.deepcopy([state, progress])
    assert missions.value(mission) == 12
    assert missions.value({**mission, "mission_type": 2}) == 0
    assert [state, progress] == before
    record(progress, NOW, 160000, 18, mode="max")
    assert missions.value(mission) == 18
    assert missions.value({**mission, "mission_type": 2}) == 18
