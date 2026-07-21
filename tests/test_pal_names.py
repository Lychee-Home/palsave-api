import unittest

from pal_names import PAL_NAMES, pal_name


class TestPalNames(unittest.TestCase):
    def test_1_0_dragon_pals_are_mapped(self):
        self.assertEqual(pal_name("LotusDragon"), "Ophydia")
        self.assertEqual(pal_name("BOSS_LotusDragon"), "Pure Bloom Dragon Ophydia")
        self.assertEqual(pal_name("GhostDragon_Fire"), "Eidrolon Ignis")
        self.assertEqual(pal_name("BOSS_GhostDragon_Fire"), "Wings of Distrust Eidrolon Ignis")

    def test_1_0_base_species_are_mapped(self):
        self.assertEqual(pal_name("KabukiMan"), "Renjishi")
        self.assertEqual(pal_name("ElecSnail"), "Snock")
        self.assertEqual(pal_name("ElecSnail_Ground"), "Snock Lux")

    def test_1_0_boss_titles_are_mapped(self):
        self.assertEqual(pal_name("BOSS_ElecSnail"), "Sluggish Blue Bolt Snock")
        self.assertEqual(pal_name("BOSS_VolcanoDragon"), "Scorched Wanderer Moldron")

    def test_1_0_predator_titles_follow_rampaging_convention(self):
        self.assertEqual(pal_name("PREDATOR_VolcanoDragon"), "Rampaging Moldron")
        self.assertEqual(pal_name("PREDATOR_MummyPal"), "Rampaging Gildra")

    def test_1_0_gym_and_raid_variants_are_mapped(self):
        self.assertEqual(pal_name("GYM_WorldTreeDragon"), "Zenara & Astralym")
        self.assertEqual(pal_name("RAID_YakushimaBoss002_Head"), "Moon Lord")

    def test_pals_with_no_known_english_name_fall_back_to_character_id(self):
        # paldb.cc itself has no real English name for these yet.
        self.assertNotIn("Sekhmet", PAL_NAMES)
        self.assertEqual(pal_name("BOSS_KingWhale"), "BOSS_KingWhale")


if __name__ == "__main__":
    unittest.main()
