import struct
import unittest

from gvas import parse_gvas


def fstring(s: str) -> bytes:
    if s == "":
        return struct.pack("<i", 0)
    raw = s.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def int_property(name: str, value: int) -> bytes:
    # name, type, size(i64), has_guid(u8), payload(i32)
    return (
        fstring(name) + fstring("IntProperty") + struct.pack("<q", 4)
        + struct.pack("<B", 0) + struct.pack("<i", value)
    )


def str_property(name: str, value: str) -> bytes:
    payload = fstring(value)
    return (
        fstring(name) + fstring("StrProperty") + struct.pack("<q", len(payload))
        + struct.pack("<B", 0) + payload
    )


def build_gvas(properties_bytes: bytes) -> bytes:
    header = (
        b"GVAS"
        + struct.pack("<iii", 1, 2, 3)  # save_game_file_version, ue4, ue5
        + struct.pack("<HHH", 5, 1, 0)  # engine major/minor/patch
        + struct.pack("<I", 0)          # engine changelist
        + fstring("release")            # engine branch
        + struct.pack("<i", 0)          # custom_version_format
        + struct.pack("<I", 0)          # num_custom_versions
        + fstring("SaveGameClass")
    )
    terminator = fstring("None")
    return header + properties_bytes + terminator


class TestParseGvas(unittest.TestCase):
    def test_header_fields(self):
        data = build_gvas(b"")
        result = parse_gvas(data)
        self.assertEqual(result["header"]["save_game_file_version"], 1)
        self.assertEqual(result["header"]["engine_version_major"], 5)
        self.assertEqual(result["header"]["save_game_class_name"], "SaveGameClass")
        self.assertEqual(result["properties"], {})
        self.assertEqual(result["trailing_bytes"], 0)

    def test_int_and_str_properties(self):
        props = int_property("Level", 42) + str_property("Name", "hi")
        data = build_gvas(props)
        result = parse_gvas(data)
        self.assertEqual(result["properties"]["Level"], 42)
        self.assertEqual(result["properties"]["Name"], "hi")

    def test_custom_version_data_dropped(self):
        cvd = (
            fstring("CustomVersionData") + fstring("IntProperty") + struct.pack("<q", 4)
            + struct.pack("<B", 0) + struct.pack("<i", 1)
        )
        data = build_gvas(cvd)
        result = parse_gvas(data)
        self.assertNotIn("CustomVersionData", result["properties"])

    def test_unknown_struct_falls_back_to_raw(self):
        # StructProperty with a made-up struct name -> not in COMPACT_STRUCTS,
        # so it's read as a nested property list; an empty one just yields {}.
        struct_payload = fstring("None")  # empty property list body
        prop = (
            fstring("Weird") + fstring("StructProperty") + struct.pack("<q", len(struct_payload))
            + fstring("MadeUpStruct") + (b"\x00" * 16)  # struct name + guid
            + struct.pack("<B", 0) + struct_payload
        )
        data = build_gvas(prop)
        result = parse_gvas(data)
        self.assertEqual(result["properties"]["Weird"], {"_struct_type": "MadeUpStruct"})

    def test_bad_magic_raises(self):
        from binary_reader import ParseError
        with self.assertRaises(ParseError):
            parse_gvas(b"NOPE" + b"\x00" * 20)


if __name__ == "__main__":
    unittest.main()
