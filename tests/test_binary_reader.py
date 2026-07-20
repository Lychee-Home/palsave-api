import struct
import unittest

from binary_reader import BinaryReader, ParseError


class TestBinaryReader(unittest.TestCase):
    def test_primitive_reads(self):
        data = struct.pack("<BbHhIiQqff", 1, -1, 2, -2, 3, -3, 4, -4, 1.5, 2.5)
        # replaced below with individual structs since pack widths for f/d differ; keep simple:
        r = BinaryReader(struct.pack("<B", 200))
        self.assertEqual(r.u8(), 200)
        r = BinaryReader(struct.pack("<b", -5))
        self.assertEqual(r.i8(), -5)
        r = BinaryReader(struct.pack("<H", 60000))
        self.assertEqual(r.u16(), 60000)
        r = BinaryReader(struct.pack("<h", -6000))
        self.assertEqual(r.i16(), -6000)
        r = BinaryReader(struct.pack("<I", 4000000000))
        self.assertEqual(r.u32(), 4000000000)
        r = BinaryReader(struct.pack("<i", -400000))
        self.assertEqual(r.i32(), -400000)
        r = BinaryReader(struct.pack("<Q", 18000000000000000000))
        self.assertEqual(r.u64(), 18000000000000000000)
        r = BinaryReader(struct.pack("<q", -900000000000))
        self.assertEqual(r.i64(), -900000000000)
        r = BinaryReader(struct.pack("<f", 1.5))
        self.assertAlmostEqual(r.f32(), 1.5)
        r = BinaryReader(struct.pack("<d", 2.5))
        self.assertAlmostEqual(r.f64(), 2.5)

    def test_bool32(self):
        r = BinaryReader(struct.pack("<i", 1) + struct.pack("<i", 0))
        self.assertTrue(r.bool32())
        self.assertFalse(r.bool32())

    def test_fstring_empty(self):
        r = BinaryReader(struct.pack("<i", 0))
        self.assertEqual(r.fstring(), "")

    def test_fstring_ascii(self):
        raw = b"hello\x00"
        r = BinaryReader(struct.pack("<i", len(raw)) + raw)
        self.assertEqual(r.fstring(), "hello")

    def test_fstring_utf16(self):
        raw = "hi".encode("utf-16-le") + b"\x00\x00"
        char_count = len(raw) // 2
        r = BinaryReader(struct.pack("<i", -char_count) + raw)
        self.assertEqual(r.fstring(), "hi")

    def test_guid_bytes_format(self):
        raw = bytes.fromhex("efbeadde") + bytes.fromhex("efbe") + bytes.fromhex("adde") + bytes.fromhex("0011223344556677")
        r = BinaryReader(raw)
        self.assertEqual(r.guid_bytes(), "deadbeef-beef-dead-0011-223344556677")

    def test_read_past_end_raises(self):
        r = BinaryReader(b"\x01\x02")
        with self.assertRaises(ParseError):
            r.read(3)

    def test_seek_out_of_range_raises(self):
        r = BinaryReader(b"\x01\x02")
        with self.assertRaises(ParseError):
            r.seek(3)

    def test_remaining(self):
        r = BinaryReader(b"\x01\x02\x03")
        r.read(1)
        self.assertEqual(r.remaining(), 2)


if __name__ == "__main__":
    unittest.main()
