"""Identify newly-acquired pals between two Palworld save snapshots and
classify how each was acquired (wild capture / hatched / purchased),
excluding recruitable human NPCs. This is the structural half of palsave's
recap.py::diff_new_pals -- the notability-tier opinion (which catches are
"worth posting") is a Discord-recap-specific judgment call and lives in the
swee consumer, not here.
"""

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def index_characters(snapshot: dict) -> dict:
    """Map InstanceId -> that character's decoded RawData fields."""
    csm = snapshot["properties"]["worldSaveData"]["CharacterSaveParameterMap"]
    index = {}
    for entry in csm:
        instance_id = entry["key"]["InstanceId"]
        value = entry["value"].get("RawData", {}).get("value", {})
        index[instance_id] = value
    return index


def is_unowned(pal: dict) -> bool:
    owner = pal.get("OwnerPlayerUId")
    return owner is None or owner == ZERO_GUID


def classify_acquisition(pal: dict) -> str:
    """wild_capture | purchased | hatched, based on Level/Exp and
    LastJumpedLocation presence (see palsave-api CLAUDE.md testing notes /
    palsave project memory owned_time_field_semantics.md for how this
    3-way rule was verified against real save diffs).
    """
    has_level = pal.get("Level") is not None
    has_location = pal.get("LastJumpedLocation") is not None
    if has_location:
        return "wild_capture"
    if has_level:
        return "purchased"
    return "hatched"


def diff_new_pals(old_snapshot: dict, new_snapshot: dict) -> list:
    old_index = index_characters(old_snapshot)
    new_index = index_characters(new_snapshot)

    events = []
    for instance_id, pal in new_index.items():
        if pal.get("IsPlayer"):
            continue
        if "UniqueNPCID" in pal:
            # Recruitable human NPCs (traders, negotiators, base bosses)
            # also produce a new InstanceId with LastJumpedLocation set,
            # which would otherwise misclassify them as a wild pal capture.
            continue

        old_pal = old_index.get(instance_id)
        if old_pal is None:
            is_new_acquisition = True
        elif is_unowned(old_pal) and not is_unowned(pal):
            # Defensive path: a wild pal that was already tracked (unowned)
            # gets caught without getting a new InstanceId.
            is_new_acquisition = True
        else:
            is_new_acquisition = False

        if not is_new_acquisition:
            continue

        events.append({
            "character_id": pal.get("CharacterID"),
            "level": pal.get("Level"),
            "talent_hp": pal.get("Talent_HP") or 0,
            "talent_shot": pal.get("Talent_Shot") or 0,
            "talent_defense": pal.get("Talent_Defense") or 0,
            "acquisition_type": classify_acquisition(pal),
            "owner_player_uid": pal.get("OwnerPlayerUId"),
            "is_rare_pal": bool(pal.get("IsRarePal")),
            "is_awakening": bool(pal.get("bIsAwakening")),
        })
    return events
