"""Low-level little-endian binary reader shared by decompress.py's palsav
container header and gvas.py's tagged-property parser.
"""

import struct


class ParseError(Exception):
    pass


class BinaryReader:
    _s_u8 = struct.Struct("<B")
    _s_i8 = struct.Struct("<b")
    _s_u16 = struct.Struct("<H")
    _s_i16 = struct.Struct("<h")
    _s_u32 = struct.Struct("<I")
    _s_i32 = struct.Struct("<i")
    _s_u64 = struct.Struct("<Q")
    _s_i64 = struct.Struct("<q")
    _s_f32 = struct.Struct("<f")
    _s_f64 = struct.Struct("<d")
    _s_guid_head = struct.Struct("<IHH")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def read(self, n: int) -> bytes:
        pos = self.pos
        end = pos + n
        if n < 0 or end > len(self.data):
            raise ParseError(f"read past end of buffer (pos={pos}, n={n}, len={len(self.data)})")
        self.pos = end
        return self.data[pos:end]

    def seek(self, pos: int):
        if pos < 0 or pos > len(self.data):
            raise ParseError(f"seek out of range: {pos}")
        self.pos = pos

    def u8(self):
        return self._s_u8.unpack(self.read(1))[0]

    def i8(self):
        return self._s_i8.unpack(self.read(1))[0]

    def u16(self):
        return self._s_u16.unpack(self.read(2))[0]

    def i16(self):
        return self._s_i16.unpack(self.read(2))[0]

    def u32(self):
        return self._s_u32.unpack(self.read(4))[0]

    def i32(self):
        return self._s_i32.unpack(self.read(4))[0]

    def u64(self):
        return self._s_u64.unpack(self.read(8))[0]

    def i64(self):
        return self._s_i64.unpack(self.read(8))[0]

    def f32(self):
        return self._s_f32.unpack(self.read(4))[0]

    def f64(self):
        return self._s_f64.unpack(self.read(8))[0]

    def bool32(self):
        return self.i32() != 0

    def guid_bytes(self):
        raw = self.read(16)
        a, b, c = self._s_guid_head.unpack(raw[:8])
        tail = raw[8:].hex()
        return f"{a:08x}-{b:04x}-{c:04x}-{tail[0:4]}-{tail[4:16]}"

    def fstring(self):
        length = self.i32()
        if length == 0:
            return ""
        if length > 0:
            raw = self.read(length)
            return raw[:-1].decode("utf-8", errors="replace")
        char_count = -length
        raw = self.read(char_count * 2)
        return raw[:-2].decode("utf-16-le", errors="replace")
