import unittest

from diff import diff_new_pals

ZERO_GUID = "00000000-0000-0000-0000-000000000000"
PLAYER_GUID = "11111111-1111-1111-1111-111111111111"


def snapshot(entries):
    return {"properties": {"worldSaveData": {"CharacterSaveParameterMap": entries}}}


def entry(instance_id, raw_data):
    return {"key": {"InstanceId": instance_id}, "value": {"RawData": {"value": raw_data}}}


class TestDiffNewPals(unittest.TestCase):
    def test_new_wild_capture(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "Quivern", "Level": 20, "LastJumpedLocation": {"x": 1, "y": 2, "z": 3},
            "OwnerPlayerUId": PLAYER_GUID, "Talent_HP": 80, "Talent_Shot": 70, "Talent_Defense": 60,
        })])
        events = diff_new_pals(old, new)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["acquisition_type"], "wild_capture")
        self.assertEqual(events[0]["character_id"], "Quivern")
        self.assertEqual(events[0]["level"], 20)
        self.assertEqual(events[0]["talent_hp"], 80)

    def test_new_hatched(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "SakuraSaurus", "OwnerPlayerUId": PLAYER_GUID,
        })])
        events = diff_new_pals(old, new)
        self.assertEqual(events[0]["acquisition_type"], "hatched")

    def test_new_purchased(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "CactusDoll", "Level": 44, "OwnerPlayerUId": PLAYER_GUID,
        })])
        events = diff_new_pals(old, new)
        self.assertEqual(events[0]["acquisition_type"], "purchased")

    def test_no_event_for_unchanged_pal(self):
        pal = {"CharacterID": "Lamball", "Level": 5, "OwnerPlayerUId": PLAYER_GUID}
        old = snapshot([entry("a", pal)])
        new = snapshot([entry("a", pal)])
        self.assertEqual(diff_new_pals(old, new), [])

    def test_unowned_to_owned_transition_counts_as_new(self):
        old = snapshot([entry("a", {"CharacterID": "Lamball", "Level": 5, "OwnerPlayerUId": ZERO_GUID})])
        new = snapshot([entry("a", {"CharacterID": "Lamball", "Level": 5, "OwnerPlayerUId": PLAYER_GUID})])
        events = diff_new_pals(old, new)
        self.assertEqual(len(events), 1)

    def test_palbox_reclaim_not_counted_as_new(self):
        # Moving a pal to the Palbox and back can null OwnerPlayerUId in
        # between snapshots, but OldOwnerPlayerUIds already lists the player
        # reclaiming it -- this is not a fresh wild capture.
        old = snapshot([entry("a", {
            "CharacterID": "BOSS_QueenBee", "Level": 42, "LastJumpedLocation": {"x": 1, "y": 2, "z": 3},
            "OwnerPlayerUId": None, "OldOwnerPlayerUIds": [PLAYER_GUID],
        })])
        new = snapshot([entry("a", {
            "CharacterID": "BOSS_QueenBee", "Level": 42, "LastJumpedLocation": {"x": 1, "y": 2, "z": 3},
            "OwnerPlayerUId": PLAYER_GUID, "OldOwnerPlayerUIds": [PLAYER_GUID],
        })])
        self.assertEqual(diff_new_pals(old, new), [])

    def test_players_excluded(self):
        old = snapshot([])
        new = snapshot([entry("a", {"IsPlayer": True, "OwnerPlayerUId": PLAYER_GUID})])
        self.assertEqual(diff_new_pals(old, new), [])

    def test_recruitable_npc_excluded(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "GrassBoss", "UniqueNPCID": "some-id",
            "LastJumpedLocation": {"x": 0, "y": 0, "z": 0}, "OwnerPlayerUId": PLAYER_GUID,
        })])
        self.assertEqual(diff_new_pals(old, new), [])

    def test_rare_and_awakening_flags(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "Chillet", "Level": 10, "LastJumpedLocation": {"x": 0, "y": 0, "z": 0},
            "OwnerPlayerUId": PLAYER_GUID, "IsRarePal": True, "bIsAwakening": True,
        })])
        events = diff_new_pals(old, new)
        self.assertTrue(events[0]["is_rare_pal"])
        self.assertTrue(events[0]["is_awakening"])

    def test_new_unowned_wild_spawn_is_not_an_event(self):
        # A pal that spawns into the world (brand-new InstanceId) but hasn't
        # been caught by anyone yet -- OwnerPlayerUId null/zero -- must not
        # be reported as an "acquisition". Only a genuinely new InstanceId
        # that is ALSO currently owned counts.
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "Chillet", "Level": 10, "LastJumpedLocation": {"x": 0, "y": 0, "z": 0},
            "OwnerPlayerUId": ZERO_GUID,
        })])
        self.assertEqual(diff_new_pals(old, new), [])

    def test_new_unowned_wild_spawn_with_null_owner_is_not_an_event(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "Chillet", "Level": 10, "LastJumpedLocation": {"x": 0, "y": 0, "z": 0},
        })])
        self.assertEqual(diff_new_pals(old, new), [])

    def test_missing_talents_default_to_zero(self):
        old = snapshot([])
        new = snapshot([entry("a", {
            "CharacterID": "Lamball", "Level": 1, "LastJumpedLocation": {"x": 0, "y": 0, "z": 0},
            "OwnerPlayerUId": PLAYER_GUID,
        })])
        events = diff_new_pals(old, new)
        self.assertEqual(events[0]["talent_hp"], 0)
        self.assertEqual(events[0]["talent_shot"], 0)
        self.assertEqual(events[0]["talent_defense"], 0)


if __name__ == "__main__":
    unittest.main()
