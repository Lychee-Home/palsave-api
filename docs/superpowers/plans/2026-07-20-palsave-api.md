# palsave-api Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone `palsave-api` service: decompress/parse Palworld saves, diff snapshots
for newly-acquired pals, watch the server's backup rotation, and serve the resulting events over a
loopback-only HTTP API.

**Architecture:** Flat, single-purpose modules at the repo root (matching the `palsave` sandbox's flat
layout, not `swee`'s package layout): `binary_reader.py` (shared low-level reader) →
`decompress.py` + `gvas.py` (pure parsing) → `diff.py` (pure structural diff) → `state.py` (persisted
event log) → `watcher.py` (polling loop, runs in a background thread) → `api.py` (FastAPI app,
reads the same persisted state) → `main.py` (entrypoint wiring config + watcher thread + uvicorn).

**Tech Stack:** Python (stdlib `zlib`/`ctypes`/`struct` for decompression/parsing, no third-party
parsing deps), FastAPI + Uvicorn for the HTTP layer, `python-dotenv` for config, `unittest` for tests
(matches `swee`'s convention — no pytest).

## Global Constraints

- Linux-only target (per spec's "Out of scope"); `decompress.py`'s Oodle (`"PlM"`) path requires a
  locally-built `ooz/bin/libooz.so` that this plan does **not** build (needs `cmake`/a C++ toolchain
  on the deployment host, not this dev environment) — tests exercise only the `zlib` (`"PlZ"`) code
  paths, leaving the missing-library `ParseError` message as the only Oodle-path coverage.
- No auth, binds to `127.0.0.1` only.
- No CLI entry points — this is API-only, unlike `palsave`'s script-style `main.py`/`recap.py`/`watcher.py`.
- Test runner: `python -m unittest discover tests -v`, mirroring `swee/tests`.
- Config via `.env` (`python-dotenv`), following `swee/swee/config.py`'s pattern of reading
  `os.environ` at module import time.
- Poll interval fixed at 60s, no env override (per spec).
- Fields ported from `palsave`'s `recap.py`/`main.py` keep their original names (`CharacterID`,
  `OwnerPlayerUId`, `Talent_HP`, etc.) since those are literal Gvas property names, not our naming
  choice.

---

## Task 1: Project scaffolding — requirements, config, test harness

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `tests/__init__.py`
- Create: `config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.BACKUP_DIR: Path`, `config.PORT: int`, `config.ARCHIVE_DIR: Path`,
  `config.STATE_PATH: Path` — read by `watcher.py`, `api.py`, `main.py` in later tasks.

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi
uvicorn[standard]
python-dotenv
httpx
```

- [ ] **Step 2: Create `.env.example`**

```
PALSAVE_API_BACKUP_DIR=/home/steam/Steam/steamapps/common/PalServer/Pal/Saved/SaveGames/.../Backup
PALSAVE_API_PORT=8787
```

- [ ] **Step 3: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: installs without error.

- [ ] **Step 4: Create empty test package marker**

Create `tests/__init__.py` with empty content (zero bytes, matches `swee/tests/__init__.py`).

- [ ] **Step 5: Write the failing test for config**

```python
# tests/test_config.py
import importlib
import os
import unittest
from pathlib import Path


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._old_backup_dir = os.environ.get("PALSAVE_API_BACKUP_DIR")
        self._old_port = os.environ.get("PALSAVE_API_PORT")

    def tearDown(self):
        if self._old_backup_dir is None:
            os.environ.pop("PALSAVE_API_BACKUP_DIR", None)
        else:
            os.environ["PALSAVE_API_BACKUP_DIR"] = self._old_backup_dir
        if self._old_port is None:
            os.environ.pop("PALSAVE_API_PORT", None)
        else:
            os.environ["PALSAVE_API_PORT"] = self._old_port

    def test_reads_backup_dir_and_default_port(self):
        os.environ["PALSAVE_API_BACKUP_DIR"] = "/tmp/backups"
        os.environ.pop("PALSAVE_API_PORT", None)
        import config
        importlib.reload(config)
        self.assertEqual(config.BACKUP_DIR, Path("/tmp/backups"))
        self.assertEqual(config.PORT, 8787)
        self.assertEqual(config.ARCHIVE_DIR, Path("snapshots"))
        self.assertEqual(config.STATE_PATH, Path("state.json"))

    def test_port_override(self):
        os.environ["PALSAVE_API_BACKUP_DIR"] = "/tmp/backups"
        os.environ["PALSAVE_API_PORT"] = "9999"
        import config
        importlib.reload(config)
        self.assertEqual(config.PORT, 9999)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m unittest tests.test_config -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 7: Write `config.py`**

```python
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKUP_DIR = Path(os.environ["PALSAVE_API_BACKUP_DIR"])
PORT = int(os.environ.get("PALSAVE_API_PORT", "8787"))

ARCHIVE_DIR = Path("snapshots")
STATE_PATH = Path("state.json")
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m unittest tests.test_config -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add requirements.txt .env.example tests/__init__.py config.py tests/test_config.py
git commit -m "Add project scaffolding and env-based config"
```

---

## Task 2: `binary_reader.py` — shared low-level binary reader

**Files:**
- Create: `binary_reader.py`
- Test: `tests/test_binary_reader.py`

**Interfaces:**
- Produces: `binary_reader.ParseError(Exception)`, `binary_reader.BinaryReader(data: bytes)` with
  methods `read(n)`, `seek(pos)`, `remaining()`, `u8/i8/u16/i16/u32/i32/u64/i64/f32/f64()`,
  `bool32()`, `guid_bytes() -> str`, `fstring() -> str`. Consumed by `decompress.py` and `gvas.py`
  (Tasks 3, 4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_binary_reader.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_binary_reader -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'binary_reader'`

- [ ] **Step 3: Write `binary_reader.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_binary_reader -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add binary_reader.py tests/test_binary_reader.py
git commit -m "Add shared BinaryReader ported from palsave/main.py"
```

---

## Task 3: `decompress.py` — palsav container decompression

**Files:**
- Create: `decompress.py`
- Test: `tests/test_decompress.py`

**Interfaces:**
- Consumes: `binary_reader.BinaryReader`, `binary_reader.ParseError` (Task 2).
- Produces: `decompress.decompress_sav(data: bytes) -> bytes` — consumed by `watcher.py` (Task 7).
  `decompress.PALSAV_MAGIC: bytes`, `decompress.OOZ_DLL_PATH: Path` (internal, referenced by error
  messages/docs only).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_decompress.py
import struct
import unittest
import zlib

from binary_reader import ParseError
from decompress import decompress_sav


def build_palsav(raw: bytes, save_type: int) -> bytes:
    if save_type == 0x30:
        body = raw
        compressed_len = len(body)
    elif save_type == 0x31:
        body = zlib.compress(raw)
        compressed_len = len(body)
    elif save_type == 0x32:
        once = zlib.compress(raw)
        body = zlib.compress(once)
        compressed_len = len(once)
    else:
        raise ValueError(save_type)
    header = struct.pack("<II", len(raw), compressed_len) + b"PlZ" + struct.pack("<B", save_type)
    return header + body


class TestDecompressSav(unittest.TestCase):
    def test_uncompressed(self):
        raw = b"GVAS" + b"\x00" * 20
        self.assertEqual(decompress_sav(build_palsav(raw, 0x30)), raw)

    def test_single_zlib(self):
        raw = b"GVAS" + b"hello world" * 10
        self.assertEqual(decompress_sav(build_palsav(raw, 0x31)), raw)

    def test_double_zlib(self):
        raw = b"GVAS" + b"hello world" * 10
        self.assertEqual(decompress_sav(build_palsav(raw, 0x32)), raw)

    def test_cnk_wrapper(self):
        raw = b"GVAS" + b"payload"
        inner = build_palsav(raw, 0x31)
        outer = struct.pack("<II", len(inner), len(inner)) + b"CNK" + struct.pack("<B", 0) + inner
        self.assertEqual(decompress_sav(outer), raw)

    def test_bad_magic_raises(self):
        bad = struct.pack("<II", 4, 4) + b"XXX" + struct.pack("<B", 0x30) + b"data"
        with self.assertRaises(ParseError):
            decompress_sav(bad)

    def test_unknown_save_type_raises(self):
        raw = b"data"
        header = struct.pack("<II", len(raw), len(raw)) + b"PlZ" + struct.pack("<B", 0x99)
        with self.assertRaises(ParseError):
            decompress_sav(header + raw)

    def test_uncompressed_length_mismatch_raises(self):
        raw = b"short"
        header = struct.pack("<II", 999, len(raw)) + b"PlZ" + struct.pack("<B", 0x30)
        with self.assertRaises(ParseError):
            decompress_sav(header + raw)

    def test_oodle_without_library_raises_helpful_error(self):
        body = b"whatever-compressed-bytes"
        header = struct.pack("<II", 100, len(body)) + b"PlM" + struct.pack("<B", 0)
        with self.assertRaises(ParseError) as ctx:
            decompress_sav(header + body)
        self.assertIn("libooz.so", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_decompress -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'decompress'`

- [ ] **Step 3: Write `decompress.py`**

```python
"""Decompress a Palworld .sav file's palsav container into its raw Gvas
(Unreal SaveGame) payload.

A small custom header (uncompressed/compressed length, "PlZ"/"PlM" magic,
compression type) wraps either one or two rounds of zlib ("PlZ", used before
Palworld's 2026 "Summer Update") or Oodle Kraken ("PlM", used from that
update onward).
"""

import ctypes
import zlib
from pathlib import Path

from binary_reader import BinaryReader, ParseError

PALSAV_MAGIC = b"PlZ"

# zao/ooz only publishes a prebuilt Windows binary; on Linux (this service's
# only supported platform) libooz.so must be built from source -- see
# CLAUDE.md for the build command.
OOZ_DLL_PATH = Path(__file__).with_name("ooz") / "bin" / "libooz.so"

_ooz_lib = None


def _get_ooz_lib():
    """Load libooz (github.com/zao/ooz) -- a clean-room, open-source
    reimplementation of Oodle Kraken, not Epic/RAD's proprietary codec.
    """
    global _ooz_lib
    if _ooz_lib is None:
        if not OOZ_DLL_PATH.exists():
            raise ParseError(
                f"Oodle-compressed ('PlM') save detected but {OOZ_DLL_PATH} is missing -- build it: "
                "git clone --recurse-submodules https://github.com/zao/ooz.git && "
                "cmake -B build -DOOZ_BUILD_EXE=OFF -DOOZ_BUILD_BUN=OFF "
                "-DOOZ_BUILD_VALIDATE=OFF -S ooz && cmake --build build, then copy the "
                f"resulting libooz.so to {OOZ_DLL_PATH}"
            )
        lib = ctypes.CDLL(str(OOZ_DLL_PATH))
        lib.Ooz_Decompress.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
        ]
        lib.Ooz_Decompress.restype = ctypes.c_int
        _ooz_lib = lib
    return _ooz_lib


def ooz_decompress(compressed: bytes, uncompressed_len: int) -> bytes:
    lib = _get_ooz_lib()
    out_buf = ctypes.create_string_buffer(uncompressed_len + 64)
    result = lib.Ooz_Decompress(
        compressed, len(compressed), out_buf, uncompressed_len,
        0, 0, 0, None, 0, None, None, None, 0, 0,
    )
    if result != uncompressed_len:
        raise ParseError(f"Ooz_Decompress returned {result}, expected {uncompressed_len}")
    return out_buf.raw[:uncompressed_len]


def decompress_sav(data: bytes) -> bytes:
    r = BinaryReader(data)
    uncompressed_len = r.u32()
    compressed_len = r.u32()
    magic = r.read(3)
    save_type = r.u8()

    # Newer saves wrap the same header again behind a "CNK" chunk marker.
    if magic == b"CNK":
        uncompressed_len = r.u32()
        compressed_len = r.u32()
        magic = r.read(3)
        save_type = r.u8()

    if magic == b"PlM":
        body = r.read(r.remaining())
        if compressed_len != len(body):
            raise ParseError(f"incorrect compressed length: {compressed_len}")
        raw = ooz_decompress(body, uncompressed_len)
        if raw[:4] != b"GVAS":
            raise ParseError("Oodle-decompressed data does not start with Gvas magic")
        return raw

    if magic != PALSAV_MAGIC:
        raise ParseError(f"not a Palworld save (bad magic {magic!r})")
    if save_type not in (0x30, 0x31, 0x32):
        raise ParseError(f"unknown save compression type: {save_type:#x}")

    body = r.read(r.remaining())

    if save_type == 0x30:
        raw = body
    elif save_type == 0x31:
        raw = zlib.decompress(body)
    else:  # 0x32: zlib compressed twice
        once = zlib.decompress(body)
        if len(once) != compressed_len:
            raise ParseError("compressed length mismatch on inner zlib layer")
        raw = zlib.decompress(once)

    if len(raw) != uncompressed_len:
        raise ParseError("uncompressed length mismatch")
    return raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_decompress -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add decompress.py tests/test_decompress.py
git commit -m "Add palsav container decompression ported from palsave/main.py"
```

---

## Task 4: `gvas.py` — Gvas tagged-property parser

**Files:**
- Create: `gvas.py`
- Test: `tests/test_gvas.py`

**Interfaces:**
- Consumes: `binary_reader.BinaryReader`, `binary_reader.ParseError` (Task 2).
- Produces: `gvas.parse_gvas(data: bytes) -> dict` — consumed by `watcher.py` (Task 7). Returns
  `{"header": {...}, "properties": {...}, "trailing_bytes": int}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gvas.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_gvas -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gvas'`

- [ ] **Step 3: Write `gvas.py`**

```python
"""Gvas (Unreal Engine SaveGame) tagged-property parser.

Unreal's "tagged property" binary serialization format, walked generically
using each property's declared byte size as a resync point so an
unrecognized/game-specific struct never desyncs the rest of the file.
"""

import base64
import re

from binary_reader import BinaryReader, ParseError

# Builtin engine structs that use compact binary serialization instead of
# a tagged property list. Anything not in here is assumed to be a
# game-defined struct serialized as a nested tagged property list.
COMPACT_STRUCTS = {
    "Guid",
    "DateTime",
    "Timespan",
    "Vector",
    "Vector2D",
    "Rotator",
    "Quat",
    "LinearColor",
    "Color",
    "IntPoint",
}

SIMPLE_READERS = {
    "IntProperty": lambda r, size: r.i32(),
    "Int8Property": lambda r, size: r.i8(),
    "Int16Property": lambda r, size: r.i16(),
    "Int64Property": lambda r, size: r.i64(),
    "UInt16Property": lambda r, size: r.u16(),
    "UInt32Property": lambda r, size: r.u32(),
    "UInt64Property": lambda r, size: r.u64(),
    "FloatProperty": lambda r, size: r.f32(),
    "DoubleProperty": lambda r, size: r.f64(),
    "StrProperty": lambda r, size: r.fstring(),
    "NameProperty": lambda r, size: r.fstring(),
    "ObjectProperty": lambda r, size: r.fstring(),
    "SoftObjectProperty": lambda r, size: r.fstring(),
    "SoftClassProperty": lambda r, size: r.fstring(),
}

KNOWN_PROPERTY_TYPES = {
    "Int8Property", "Int16Property", "IntProperty", "Int64Property",
    "UInt16Property", "UInt32Property", "UInt64Property", "FloatProperty",
    "DoubleProperty", "BoolProperty", "ByteProperty", "StrProperty",
    "NameProperty", "TextProperty", "EnumProperty", "ArrayProperty",
    "SetProperty", "MapProperty", "StructProperty", "ObjectProperty",
    "SoftObjectProperty", "SoftClassProperty",
}

_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def read_property_list(r: BinaryReader) -> dict:
    """Read tagged properties until a 'None' terminator; used for the file
    header extras and for any struct/map value without compact binary form.
    """
    props = {}
    while True:
        name = r.fstring()
        if name == "" or name == "None":
            return props
        prop_type = r.fstring()
        size = r.i64()
        value, value_start = read_property_value(r, prop_type, size, tagged=True)
        # Always resync using the declared size relative to where the actual
        # value payload began (after any leading HasPropertyGuid byte(s),
        # which aren't counted in the declared size), regardless of whether
        # the value parsed cleanly, so one unknown struct can't corrupt the
        # rest of the file.
        r.seek(value_start + size)
        # CustomVersionData is per-struct Unreal serialization-version
        # bookkeeping -- never actual save content, attached to nearly every
        # struct in the file, so it's dropped unconditionally.
        if name != "CustomVersionData":
            props[name] = value
    return props


def try_decode_embedded_property(raw: bytes):
    """Some Palworld byte-array fields (e.g. "RawData") are themselves a
    single fully-serialized tagged property. Try decoding it as one; return
    None (letting the caller fall back to raw bytes) if it doesn't look like
    a property tag at all.
    """
    if len(raw) < 8:
        return None
    r = BinaryReader(raw)
    try:
        name = r.fstring()
        if not _FIELD_NAME_RE.match(name):
            return None
        prop_type = r.fstring()
        if prop_type not in KNOWN_PROPERTY_TYPES:
            return None
        size = r.i64()
        if not (0 <= size <= len(raw)):
            return None
        value, value_start = read_property_value(r, prop_type, size, tagged=True)
        if isinstance(value, dict) and ("_parse_error" in value or "_unhandled_type" in value):
            return None
    except ParseError:
        return None
    consumed = value_start + size
    if consumed > len(raw):
        return None
    return {
        "_embedded_property_name": name,
        "_embedded_property_type": prop_type,
        "value": value,
        "_trailing_bytes": len(raw) - consumed,
    }


def try_decode_character_handle(raw: bytes):
    """Character container slots (PalCharacterSlotSaveData.RawData) store a
    compact 38-byte reference to the occupying character rather than a full
    tagged property: PlayerUId (16 bytes) + InstanceId (16 bytes) + 6
    reserved/padding bytes. Returns None for anything not exactly 38 bytes,
    letting the caller fall back to raw bytes.
    """
    if len(raw) != 38:
        return None
    r = BinaryReader(raw)
    player_uid = r.guid_bytes()
    instance_id = r.guid_bytes()
    reserved = r.read(6)
    result = {"PlayerUId": player_uid, "InstanceId": instance_id}
    if reserved != b"\x00" * 6:
        result["_reserved_hex"] = reserved.hex()
    return result


def read_property_value(r: BinaryReader, prop_type: str, size: int, tagged: bool):
    """Read one property's tag-specific header fields (if `tagged`) followed
    by its value, and return a JSON-serializable representation.
    """
    struct_name = None
    inner_type = key_type = value_type = None
    enum_name = None
    bool_value = None

    if tagged:
        if prop_type == "StructProperty":
            struct_name = r.fstring()
            r.guid_bytes()  # struct guid, unused
        elif prop_type in ("ByteProperty", "EnumProperty"):
            enum_name = r.fstring()
        elif prop_type == "ArrayProperty":
            inner_type = r.fstring()
        elif prop_type == "SetProperty":
            inner_type = r.fstring()
        elif prop_type == "MapProperty":
            key_type = r.fstring()
            value_type = r.fstring()
        elif prop_type == "BoolProperty":
            # BoolProperty is the odd one out: its value lives in the tag
            # itself (1 byte), *before* the HasPropertyGuid flag below.
            bool_value = r.u8() != 0

        has_guid = r.u8()
        if has_guid:
            r.guid_bytes()

    value_start = r.pos

    try:
        value = _read_property_payload(r, prop_type, size, struct_name, inner_type, key_type, value_type, enum_name, bool_value)
    except ParseError:
        r.seek(value_start)
        remaining = max(0, min(size, r.remaining()))
        raw = r.read(remaining)
        value = {"_parse_error": True, "_type": prop_type, "_raw_base64": base64.b64encode(raw).decode("ascii")}

    return value, value_start


def _read_property_payload(r, prop_type, size, struct_name, inner_type, key_type, value_type, enum_name, bool_value):
    if prop_type == "BoolProperty":
        return bool_value
    if prop_type in SIMPLE_READERS:
        return SIMPLE_READERS[prop_type](r, size)
    if prop_type == "ByteProperty":
        if enum_name == "None":
            return r.u8()
        return r.fstring()
    if prop_type == "EnumProperty":
        return r.fstring()
    if prop_type == "TextProperty":
        return read_text_property(r)
    if prop_type == "StructProperty":
        return read_struct_body(r, struct_name, size)
    if prop_type == "ArrayProperty":
        return read_array_property(r, inner_type, size)
    if prop_type == "SetProperty":
        return read_set_property(r, inner_type)
    if prop_type == "MapProperty":
        return read_map_property(r, key_type, value_type, size)
    # Unknown property type: keep the raw bytes so no data is lost.
    raw = r.read(size)
    return {"_unhandled_type": prop_type, "_raw_base64": base64.b64encode(raw).decode("ascii")}


def read_text_property(r: BinaryReader):
    # Minimal FText support: flags + history type, culture-invariant text is
    # the common case in SaveGame data. Anything more exotic falls back to
    # the caller's raw-bytes recovery via the outer property Size.
    r.u32()  # flags
    history_type = r.i8()
    if history_type == -1:
        return None
    if history_type == 0:
        namespace = r.fstring()
        key = r.fstring()
        source = r.fstring()
        return {"namespace": namespace, "key": key, "source": source}
    raise ParseError(f"unsupported FText history type {history_type}")


def read_struct_body(r: BinaryReader, struct_name, size):
    if struct_name in COMPACT_STRUCTS:
        return read_compact_struct(r, struct_name, size)
    if struct_name is None:
        # Struct type wasn't declared by the container (e.g. some map
        # values) -- assume the common case of a tagged property list.
        return read_property_list(r)
    props = read_property_list(r)
    props["_struct_type"] = struct_name
    return props


def read_compact_struct(r: BinaryReader, struct_name, size):
    if struct_name == "Guid":
        return r.guid_bytes()
    if struct_name in ("DateTime", "Timespan"):
        return r.i64()
    if struct_name == "Color":
        b, g, rr, a = r.u8(), r.u8(), r.u8(), r.u8()
        return {"r": rr, "g": g, "b": b, "a": a}
    if struct_name == "IntPoint":
        return {"x": r.i32(), "y": r.i32()}
    if struct_name in ("Vector", "Rotator"):
        # UE5 uses double components (LWC); fall back to float32 if the
        # declared size doesn't match a 24-byte double vector.
        use_double = size >= 24
        comp = r.f64 if use_double else r.f32
        keys = ("pitch", "yaw", "roll") if struct_name == "Rotator" else ("x", "y", "z")
        return {k: comp() for k in keys}
    if struct_name == "Vector2D":
        use_double = size >= 16
        comp = r.f64 if use_double else r.f32
        return {"x": comp(), "y": comp()}
    if struct_name == "Quat":
        use_double = size >= 32
        comp = r.f64 if use_double else r.f32
        return {"x": comp(), "y": comp(), "z": comp(), "w": comp()}
    if struct_name == "LinearColor":
        return {"r": r.f32(), "g": r.f32(), "b": r.f32(), "a": r.f32()}
    raise ParseError(f"unhandled compact struct {struct_name}")


def read_array_property(r: BinaryReader, inner_type, size):
    count = r.u32()
    end = r.pos + size - 4

    if inner_type == "StructProperty":
        # Arrays of structs carry one dummy property tag up front describing
        # the element name/type/struct-name/size shared by every element.
        r.fstring()  # element field name
        elem_type = r.fstring()
        elem_size = r.i64()
        elem_struct_name = r.fstring()
        r.guid_bytes()
        r.u8()  # has property guid (always 0 here)
        items = []
        for _ in range(count):
            items.append(read_struct_body(r, elem_struct_name, elem_size))
        r.seek(end)
        return items

    if inner_type == "ByteProperty" and count == size - 4:
        # Common case: a plain byte blob. Palworld frequently stashes a
        # single fully-serialized tagged property inside these (e.g. a
        # "RawData" field wrapping a whole PalIndividualCharacterSaveParameter
        # struct) -- try decoding it as one before giving up to raw bytes.
        raw = r.read(count)
        decoded = try_decode_embedded_property(raw)
        if decoded is None:
            decoded = try_decode_character_handle(raw)
        if decoded is not None:
            return decoded
        return {"_byte_array_base64": base64.b64encode(raw).decode("ascii")}

    reader = SIMPLE_READERS.get(inner_type)
    items = []
    if reader is not None:
        for _ in range(count):
            items.append(reader(r, 0))
    elif inner_type == "BoolProperty":
        for _ in range(count):
            items.append(r.u8() != 0)
    elif inner_type == "EnumProperty":
        for _ in range(count):
            items.append(r.fstring())
    else:
        raw = r.read(max(0, end - r.pos))
        return {"_unhandled_array_inner": inner_type, "_raw_base64": base64.b64encode(raw).decode("ascii")}
    r.seek(end)
    return items


def read_first_struct_scalar(r: BinaryReader):
    """Read the first untagged StructProperty element of a Map/Set (which
    carries no name/size, so whether it's a bare Guid or a nested
    tagged-property list has to be guessed) and return (value, mode) so
    every later element of the same collection can be read directly with
    the resolved mode instead of re-detecting.

    Tries a property list first and keeps the result if it looks sane
    (plausible field names); otherwise rewinds and reads a bare Guid.
    """
    start = r.pos
    try:
        props = read_property_list(r)
        if props and all(_FIELD_NAME_RE.match(k) for k in props):
            return props, "proplist"
    except ParseError:
        pass
    r.seek(start)
    return read_struct_body(r, "Guid", 16), "guid"


def read_set_property(r: BinaryReader, inner_type):
    r.u32()  # legacy "removed items" count, always 0 in practice
    count = r.u32()
    if count == 0:
        return []
    if inner_type == "StructProperty":
        first, mode = read_first_struct_scalar(r)
    else:
        first, mode = read_scalar(r, inner_type, None), None
    items = [first]
    items.extend(read_scalar(r, inner_type, mode) for _ in range(count - 1))
    return items


def read_map_property(r: BinaryReader, key_type, value_type, size):
    r.u32()  # legacy "removed keys" count, always 0 in practice
    count = r.u32()
    if count == 0:
        return []

    if key_type == "StructProperty":
        key0, key_mode = read_first_struct_scalar(r)
    else:
        key0, key_mode = read_scalar(r, key_type, None), None
    if value_type == "StructProperty":
        value0, value_mode = read_first_struct_scalar(r)
    else:
        value0, value_mode = read_scalar(r, value_type, None), None

    entries = [{"key": key0, "value": value0}]
    for _ in range(count - 1):
        key = read_scalar(r, key_type, key_mode)
        value = read_scalar(r, value_type, value_mode)
        entries.append({"key": key, "value": value})
    return entries


def read_scalar(r: BinaryReader, prop_type, struct_mode):
    """Read one Array/Set/Map element that has no per-element property tag."""
    if prop_type == "StructProperty":
        if struct_mode == "guid":
            return read_struct_body(r, "Guid", 16)
        return read_struct_body(r, None, 0)
    if prop_type == "BoolProperty":
        return r.u8() != 0
    if prop_type == "EnumProperty":
        return r.fstring()
    reader = SIMPLE_READERS.get(prop_type)
    if reader is not None:
        return reader(r, 0)
    raise ParseError(f"unhandled scalar type {prop_type}")


def parse_gvas(data: bytes) -> dict:
    r = BinaryReader(data)
    magic = r.read(4)
    if magic != b"GVAS":
        raise ParseError(f"not a Gvas file (bad magic {magic!r})")

    header = {}
    header["save_game_file_version"] = r.i32()
    header["package_file_version_ue4"] = r.i32()
    header["package_file_version_ue5"] = r.i32()
    header["engine_version_major"] = r.u16()
    header["engine_version_minor"] = r.u16()
    header["engine_version_patch"] = r.u16()
    header["engine_version_changelist"] = r.u32()
    header["engine_version_branch"] = r.fstring()
    header["custom_version_format"] = r.i32()
    num_custom_versions = r.u32()
    header["custom_versions"] = [
        {"id": r.guid_bytes(), "version": r.i32()} for _ in range(num_custom_versions)
    ]
    header["save_game_class_name"] = r.fstring()

    properties = read_property_list(r)

    return {"header": header, "properties": properties, "trailing_bytes": r.remaining()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_gvas -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add gvas.py tests/test_gvas.py
git commit -m "Add Gvas tagged-property parser ported from palsave/main.py"
```

---

## Task 5: `diff.py` — structural new-pal diffing

**Files:**
- Create: `diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure function over plain dicts shaped like `gvas.parse_gvas`
  output).
- Produces: `diff.diff_new_pals(old_snapshot: dict, new_snapshot: dict) -> list[dict]`, each dict
  with keys `character_id, level, talent_hp, talent_shot, talent_defense, acquisition_type,
  owner_player_uid, is_rare_pal, is_awakening` — consumed by `watcher.py` (Task 7), which adds `id`
  and `snapshot`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diff.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_diff -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'diff'`

- [ ] **Step 3: Write `diff.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_diff -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add diff.py tests/test_diff.py
git commit -m "Add structural new-pal diff ported from palsave/recap.py"
```

---

## Task 6: `state.py` — persisted event log

**Files:**
- Create: `state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `state.load_state(state_path: Path) -> dict`, `state.save_state(state_path: Path, state:
  dict) -> None`, `state.append_events(state: dict, new_events: list[dict]) -> None` (mutates
  `state` in place, assigns `id` to each event), `state.query_events(state: dict, since: int, limit:
  int) -> list[dict]`. Consumed by `watcher.py` (Task 7) and `api.py` (Task 8).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
import json
import tempfile
import unittest
from pathlib import Path

from state import append_events, load_state, query_events, save_state


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_file_returns_default(self):
        state = load_state(self.state_path)
        self.assertIsNone(state["last_processed"])
        self.assertIsNone(state["last_snapshot_path"])
        self.assertEqual(state["next_event_id"], 1)
        self.assertEqual(state["events"], [])

    def test_default_state_events_list_not_shared_across_calls(self):
        a = load_state(self.state_path)
        a["events"].append({"id": 1})
        b = load_state(self.state_path)
        self.assertEqual(b["events"], [])

    def test_save_and_load_round_trip(self):
        state = {"last_processed": "folder1", "last_snapshot_path": "snapshots/folder1.sav",
                  "next_event_id": 3, "events": [{"id": 1, "character_id": "Lamball"}]}
        save_state(self.state_path, state)
        loaded = load_state(self.state_path)
        self.assertEqual(loaded, state)

    def test_corrupt_file_raises(self):
        self.state_path.write_text("not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            load_state(self.state_path)

    def test_append_events_assigns_increasing_ids(self):
        state = {"last_processed": None, "last_snapshot_path": None, "next_event_id": 5, "events": []}
        append_events(state, [{"character_id": "A"}, {"character_id": "B"}])
        self.assertEqual(state["events"][0]["id"], 5)
        self.assertEqual(state["events"][1]["id"], 6)
        self.assertEqual(state["next_event_id"], 7)

    def test_query_events_filters_since(self):
        state = {"events": [{"id": 1}, {"id": 2}, {"id": 3}]}
        self.assertEqual(query_events(state, since=1, limit=10), [{"id": 2}, {"id": 3}])

    def test_query_events_respects_limit(self):
        state = {"events": [{"id": 1}, {"id": 2}, {"id": 3}]}
        self.assertEqual(query_events(state, since=0, limit=2), [{"id": 1}, {"id": 2}])

    def test_query_events_since_beyond_max_is_empty(self):
        state = {"events": [{"id": 1}, {"id": 2}]}
        self.assertEqual(query_events(state, since=99, limit=10), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_state -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`

- [ ] **Step 3: Write `state.py`**

```python
"""Persisted watcher state: last processed backup folder, last archived
snapshot path, next event ID, and the append-only new-pal event log itself.
Read fresh from disk by both the watcher (after every tick) and the API
(on every request), so a restart of either always sees the latest committed
state.
"""

import json
from pathlib import Path

DEFAULT_STATE = {
    "last_processed": None,
    "last_snapshot_path": None,
    "next_event_id": 1,
    "events": [],
}


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return dict(DEFAULT_STATE, events=[])
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict) -> None:
    state_path.write_text(json.dumps(state), encoding="utf-8")


def append_events(state: dict, new_events: list) -> None:
    """Assign each event the next monotonically increasing ID and append it
    to the log in place, mutating `state`."""
    for event in new_events:
        event["id"] = state["next_event_id"]
        state["next_event_id"] += 1
        state["events"].append(event)


def query_events(state: dict, since: int, limit: int) -> list:
    matching = [e for e in state["events"] if e["id"] > since]
    return matching[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_state -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add state.py tests/test_state.py
git commit -m "Add persisted event log state module"
```

---

## Task 7: `watcher.py` — backup-rotation polling loop

**Files:**
- Create: `watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `decompress.decompress_sav` (Task 3), `gvas.parse_gvas` (Task 4), `diff.diff_new_pals`
  (Task 5), `state.load_state/save_state/append_events` (Task 6), `binary_reader.ParseError` (Task 2).
- Produces: `watcher.process_new_backups(backup_root: Path, archive_dir: Path, state_path: Path) ->
  None`, `watcher.run_forever(backup_root: Path, archive_dir: Path, state_path: Path) -> None`,
  `watcher.FOLDER_NAME_FORMAT: str`, `watcher.POLL_SECONDS: int`. `run_forever` consumed by `api.py`
  (Task 8, run in a background thread).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_watcher.py
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from state import load_state
from watcher import list_backup_folders, process_new_backups


def build_palsav(raw: bytes) -> bytes:
    body = zlib.compress(raw)
    header = struct.pack("<II", len(raw), len(body)) + b"PlZ" + struct.pack("<B", 0x31)
    return header + body


def fstring(s: str) -> bytes:
    if s == "":
        return struct.pack("<i", 0)
    raw = s.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def guid_bytes(hex32: str) -> bytes:
    # hex32 e.g. "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" -> raw little-endian GUID bytes
    parts = hex32.split("-")
    a = struct.pack("<I", int(parts[0], 16))
    b = struct.pack("<H", int(parts[1], 16))
    c = struct.pack("<H", int(parts[2], 16))
    d = bytes.fromhex(parts[3] + parts[4])
    return a + b + c + d


def struct_property(name: str, struct_name: str, body: bytes) -> bytes:
    return (
        fstring(name) + fstring("StructProperty") + struct.pack("<q", len(body))
        + fstring(struct_name) + (b"\x00" * 16) + struct.pack("<B", 0) + body
    )


def guid_property_value(instance_id_hex: str) -> bytes:
    # A CharacterSaveParameterMap key struct: read as a Guid-mode scalar
    # (16 raw bytes, no property-list wrapper) since it won't parse as a
    # sane property list.
    return guid_bytes(instance_id_hex)


def map_property_one_entry(key_guid_hex: str, value_props: bytes) -> bytes:
    value_body = value_props + fstring("None")
    body = (
        struct.pack("<I", 0)  # removed count
        + struct.pack("<I", 1)  # entry count
        + guid_property_value(key_guid_hex)  # key: bare Guid (16 bytes)
        + value_body  # value: property list (RawData etc.), terminated by None
    )
    return (
        fstring("CharacterSaveParameterMap") + fstring("MapProperty") + struct.pack("<q", len(body))
        + fstring("StructProperty") + fstring("StructProperty") + struct.pack("<B", 0) + body
    )


def raw_data_property(inner_props: bytes) -> bytes:
    # ArrayProperty<ByteProperty> whose bytes decode as one embedded tagged
    # property (a StructProperty named "RawData" wrapping inner_props).
    embedded = struct_property("RawData", "PalIndividualCharacterSaveParameter", inner_props + fstring("None"))
    body = struct.pack("<I", len(embedded)) + embedded
    return (
        fstring("RawData") + fstring("ArrayProperty") + struct.pack("<q", len(body))
        + fstring("ByteProperty") + struct.pack("<B", 0) + body
    )


def build_world_save_data(entries_bytes: bytes) -> bytes:
    body = entries_bytes + fstring("None")
    return (
        fstring("worldSaveData") + fstring("StructProperty") + struct.pack("<q", len(body))
        + fstring("None") + (b"\x00" * 16) + struct.pack("<B", 0) + body
    )


def build_gvas_with_pal(instance_id_hex: str, owner_hex: str, level) -> bytes:
    level_prop = b""
    if level is not None:
        level_prop = (
            fstring("Level") + fstring("IntProperty") + struct.pack("<q", 4)
            + struct.pack("<B", 0) + struct.pack("<i", level)
        )
    owner_prop = struct_property("OwnerPlayerUId", "Guid", guid_bytes(owner_hex))
    inner = level_prop + owner_prop
    csm_entry = map_property_one_entry(instance_id_hex, raw_data_property(inner))
    wsd = build_world_save_data(csm_entry)

    header = (
        b"GVAS"
        + struct.pack("<iii", 1, 2, 3)
        + struct.pack("<HHH", 5, 1, 0)
        + struct.pack("<I", 0)
        + fstring("release")
        + struct.pack("<i", 0)
        + struct.pack("<I", 0)
        + fstring("SaveGameClass")
    )
    return header + wsd + fstring("None")


def write_backup_folder(root: Path, name: str, gvas_bytes: bytes):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "Level.sav").write_bytes(build_palsav(gvas_bytes))


class TestListBackupFolders(unittest.TestCase):
    def test_lists_only_matching_folders_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "2026.01.02-10.00.00").mkdir()
            (root / "2026.01.01-10.00.00").mkdir()
            (root / "not-a-backup").mkdir()
            names = [f.name for _, f in list_backup_folders(root)]
            self.assertEqual(names, ["2026.01.01-10.00.00", "2026.01.02-10.00.00"])


class TestProcessNewBackups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "backups"
        self.root.mkdir()
        self.archive_dir = Path(self.tmp.name) / "snapshots"
        self.state_path = Path(self.tmp.name) / "state.json"
        self.owner = "22222222-2222-2222-2222-222222222222"

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_seeds_baseline_without_events(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)
        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.01-00.00.00")
        self.assertEqual(state["events"], [])
        self.assertTrue((self.archive_dir / "2026.01.01-00.00.00.sav").exists())

    def test_second_run_produces_new_pal_event(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        write_backup_folder(self.root, "2026.01.02-00.00.00",
                             build_gvas_with_pal("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", self.owner, 20))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["events"][0]["id"], 1)
        self.assertEqual(state["events"][0]["level"], 20)
        self.assertEqual(state["events"][0]["snapshot"], "2026.01.02-00.00.00")

    def test_missing_level_sav_is_skipped(self):
        folder = self.root / "2026.01.01-00.00.00"
        folder.mkdir()
        process_new_backups(self.root, self.archive_dir, self.state_path)
        state = load_state(self.state_path)
        self.assertIsNone(state["last_processed"])

    def test_parse_failure_advances_state_and_skips_diff(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        folder = self.root / "2026.01.02-00.00.00"
        folder.mkdir()
        (folder / "Level.sav").write_bytes(b"not a valid palsav file")
        process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.02-00.00.00")
        self.assertEqual(state["events"], [])

    def test_archive_failure_leaves_state_untouched(self):
        write_backup_folder(self.root, "2026.01.01-00.00.00",
                             build_gvas_with_pal("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", self.owner, 10))
        process_new_backups(self.root, self.archive_dir, self.state_path)

        write_backup_folder(self.root, "2026.01.02-00.00.00",
                             build_gvas_with_pal("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", self.owner, 20))
        with mock.patch("watcher.shutil.copy2", side_effect=OSError("disk full")):
            process_new_backups(self.root, self.archive_dir, self.state_path)

        state = load_state(self.state_path)
        self.assertEqual(state["last_processed"], "2026.01.01-00.00.00")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_watcher -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watcher'`

- [ ] **Step 3: Write `watcher.py`**

```python
"""Background polling loop for a Palworld dedicated server's own backup
rotation: archives each new Level.sav, decompresses + parses it and the
previous snapshot, diffs them for newly acquired pals, and appends the
resulting events to the persisted state (see state.py).

Palworld's dedicated server writes a complete, already-finished backup
folder on its own rotation -- by the time a folder shows up here it's safe
to read, no mid-write races to guard against. Since the server prunes old
backups on its own, each processed Level.sav is copied into a local archive
directory that isn't subject to that pruning, so history survives and the
watcher can resume correctly after a restart.
"""

import datetime
import logging
import shutil
import time
from pathlib import Path

import decompress
import diff
import gvas
import state as state_module
from binary_reader import ParseError

FOLDER_NAME_FORMAT = "%Y.%m.%d-%H.%M.%S"
POLL_SECONDS = 60

log = logging.getLogger("palsave_api.watcher")


def list_backup_folders(backup_root: Path):
    """Every subfolder matching Palworld's backup naming pattern, oldest first."""
    folders = []
    for child in backup_root.iterdir():
        if not child.is_dir():
            continue
        try:
            timestamp = datetime.datetime.strptime(child.name, FOLDER_NAME_FORMAT)
        except ValueError:
            continue
        folders.append((timestamp, child))
    folders.sort(key=lambda pair: pair[0])
    return folders


def archive_snapshot(sav_path: Path, archive_dir: Path, folder_name: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{folder_name}.sav"
    if not dest.exists():
        shutil.copy2(sav_path, dest)
    return dest


def _load_sav(path: Path) -> dict:
    data = path.read_bytes()
    gvas_data = decompress.decompress_sav(data)
    return gvas.parse_gvas(gvas_data)


def process_new_backups(backup_root: Path, archive_dir: Path, state_path: Path) -> None:
    state = state_module.load_state(state_path)
    folders = list_backup_folders(backup_root)
    if not folders:
        return

    if state["last_processed"] is None:
        # First ever run: seed the baseline from the latest existing backup
        # rather than replaying the server's whole backup history as if it
        # all just happened.
        timestamp, folder = folders[-1]
        sav_path = folder / "Level.sav"
        if sav_path.exists():
            archived = archive_snapshot(sav_path, archive_dir, folder.name)
            state["last_snapshot_path"] = str(archived)
            log.info("[%s] baseline snapshot (first run), nothing to diff against yet", folder.name)
        state["last_processed"] = folder.name
        state_module.save_state(state_path, state)
        return

    new_folders = [(ts, f) for ts, f in folders if f.name > state["last_processed"]]

    for timestamp, folder in new_folders:
        sav_path = folder / "Level.sav"
        if not sav_path.exists():
            log.warning("[%s] skip: no Level.sav found", folder.name)
            continue

        try:
            archived = archive_snapshot(sav_path, archive_dir, folder.name)
        except OSError:
            log.exception("[%s] failed to archive, will retry next cycle", folder.name)
            break  # leave state untouched so this folder is retried

        previous_path = state.get("last_snapshot_path")
        try:
            old_snapshot = _load_sav(Path(previous_path))
            new_snapshot = _load_sav(archived)
            new_events = diff.diff_new_pals(old_snapshot, new_snapshot)
            for event in new_events:
                event["snapshot"] = folder.name
            state_module.append_events(state, new_events)
        except ParseError:
            log.exception("[%s] failed to parse, skipping diff for this snapshot", folder.name)

        state["last_processed"] = folder.name
        state["last_snapshot_path"] = str(archived)
        state_module.save_state(state_path, state)


def run_forever(backup_root: Path, archive_dir: Path, state_path: Path) -> None:
    log.info("watching %s every %ds (archive: %s)", backup_root, POLL_SECONDS, archive_dir)
    while True:
        try:
            process_new_backups(backup_root, archive_dir, state_path)
        except Exception:
            log.exception("watcher tick failed, will retry next cycle")
        time.sleep(POLL_SECONDS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_watcher -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add watcher.py tests/test_watcher.py
git commit -m "Add backup-rotation watcher ported from palsave/watcher.py"
```

---

## Task 8: `api.py` — FastAPI events endpoint

**Files:**
- Create: `api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `config.BACKUP_DIR/ARCHIVE_DIR/STATE_PATH` (Task 1), `state.load_state/query_events`
  (Task 6), `watcher.run_forever` (Task 7).
- Produces: `api.app` (FastAPI instance) — consumed by `main.py` (Task 9).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PALSAVE_API_BACKUP_DIR", tempfile.mkdtemp())

from fastapi.testclient import TestClient

import config
from state import save_state


class TestEventsEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_backup_dir = config.BACKUP_DIR
        self._old_archive_dir = config.ARCHIVE_DIR
        self._old_state_path = config.STATE_PATH
        config.BACKUP_DIR = Path(self.tmp.name) / "backups"
        config.BACKUP_DIR.mkdir()
        config.ARCHIVE_DIR = Path(self.tmp.name) / "snapshots"
        config.STATE_PATH = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        config.BACKUP_DIR = self._old_backup_dir
        config.ARCHIVE_DIR = self._old_archive_dir
        config.STATE_PATH = self._old_state_path
        self.tmp.cleanup()

    def test_returns_events_after_since_up_to_limit(self):
        save_state(config.STATE_PATH, {
            "last_processed": "f", "last_snapshot_path": "s", "next_event_id": 4,
            "events": [{"id": 1, "character_id": "A"}, {"id": 2, "character_id": "B"},
                       {"id": 3, "character_id": "C"}],
        })
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals", params={"since": 1, "limit": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [{"id": 2, "character_id": "B"}])

    def test_defaults_since_zero_limit_twenty(self):
        events = [{"id": i} for i in range(1, 25)]
        save_state(config.STATE_PATH, {
            "last_processed": "f", "last_snapshot_path": "s", "next_event_id": 25, "events": events,
        })
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals")
        self.assertEqual(len(resp.json()), 20)
        self.assertEqual(resp.json()[0]["id"], 1)

    def test_missing_state_file_returns_empty_list(self):
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_corrupt_state_file_returns_500(self):
        config.STATE_PATH.write_text("not json", encoding="utf-8")
        import api
        with TestClient(api.app) as client:
            resp = client.get("/events/new-pals")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_api -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api'`

- [ ] **Step 3: Write `api.py`**

```python
"""FastAPI app exposing the persisted new-pal event log. Binds to
127.0.0.1 only (see main.py) -- no auth, nothing off-host can reach it.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

import config
import state as state_module
import watcher

log = logging.getLogger("palsave_api.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(
        target=watcher.run_forever,
        args=(config.BACKUP_DIR, config.ARCHIVE_DIR, config.STATE_PATH),
        daemon=True,
    )
    thread.start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/events/new-pals")
def get_new_pals(since: int = 0, limit: int = 20):
    try:
        state = state_module.load_state(config.STATE_PATH)
    except (OSError, ValueError):
        log.exception("failed to read event log")
        raise HTTPException(status_code=500, detail="event log unreadable")
    return state_module.query_events(state, since, limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_api -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api.py
git commit -m "Add FastAPI events endpoint"
```

---

## Task 9: `main.py` — entrypoint, ooz vendoring docs, final polish

**Files:**
- Modify: `main.py` (currently PyCharm boilerplate)
- Modify: `CLAUDE.md` ("Current state" section)
- Create: `ooz/bin/.gitkeep`

**Interfaces:**
- Consumes: `config.PORT` (Task 1), `api.app` (Task 8).
- Produces: running service (manual verification only, no new importable interface).

- [ ] **Step 1: Read current `main.py` to confirm it's still boilerplate**

Run: `cat main.py` (Bash) or open in editor.
Expected: PyCharm's default `# This is a sample Python script.` scaffold.

- [ ] **Step 2: Replace `main.py` with the service entrypoint**

```python
"""Entrypoint: starts the palsave-api service (FastAPI app + background
backup-rotation watcher thread, wired together in api.py's lifespan)."""

import logging

import uvicorn

import config
from api import app

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=config.PORT)
```

- [ ] **Step 3: Create `ooz/bin/.gitkeep` so the directory is tracked before the binary is vendored**

Empty file at `ooz/bin/.gitkeep`.

- [ ] **Step 4: Update `CLAUDE.md`'s "Current state" section**

Replace the existing "Current state" section (which says the repo is still a fresh scaffold) with:

```markdown
## Current state

The service is implemented: `binary_reader.py`, `decompress.py`, `gvas.py`, `diff.py`, `state.py`,
`watcher.py`, `api.py`, and `main.py` per the design spec at
`docs/superpowers/specs/2026-07-20-palsave-api-design.md`. `ooz/bin/libooz.so` still needs to be
built on the Linux deployment host before Oodle-compressed ("PlM") saves can be decompressed there
(see `decompress.py`'s module docstring for the build command) — zlib ("PlZ") saves work without it.
```

- [ ] **Step 5: Run the full test suite**

Run: `python -m unittest discover tests -v`
Expected: PASS, all tests from Tasks 1-8 (roughly 43 tests).

- [ ] **Step 6: Manual smoke test**

```bash
mkdir -p /tmp/palsave-smoke/backups
echo "PALSAVE_API_BACKUP_DIR=/tmp/palsave-smoke/backups" > .env
echo "PALSAVE_API_PORT=8787" >> .env
python main.py &
sleep 2
curl "http://127.0.0.1:8787/events/new-pals"
kill %1
```

Expected: `curl` returns `[]` (empty backup dir, no events yet), no server errors in stdout.

- [ ] **Step 7: Commit**

```bash
git add main.py CLAUDE.md ooz/bin/.gitkeep
git commit -m "Add service entrypoint and update CLAUDE.md for implemented state"
```

---

## Self-Review Notes

- **Spec coverage:** `decompress.py`/`gvas.py`/`diff.py`/`watcher.py`/`api.py` all present (Tasks
  3-8) plus the spec's `ooz/bin/libooz.so` vendoring path documented (Task 9, binary itself not
  built here — needs the Linux host's toolchain). Config env vars (`PALSAVE_API_BACKUP_DIR`,
  `PALSAVE_API_PORT`) covered in Task 1. Baseline-seeding, per-folder archive+diff+persist flow,
  and all three error-handling cases (parse failure, archive I/O failure, corrupt event log → 500)
  covered in Tasks 7-8 with dedicated tests. Notability/highlight logic correctly excluded (stays in
  `swee`). No CLI entry points added.
- **Extra modules beyond the spec's five:** `binary_reader.py`, `state.py`, and `config.py` were
  factored out as shared low-level pieces (DRY: avoids duplicating `BinaryReader` between
  `decompress.py`/`gvas.py`, and gives `api.py` read-only access to watcher state without importing
  polling logic). This doesn't contradict the spec's architecture section, which describes
  responsibilities rather than mandating exactly five files.
- **Placeholder scan:** no TBD/TODO markers; every step has complete runnable code.
- **Type consistency:** event dict shape (`character_id, level, talent_hp, talent_shot,
  talent_defense, acquisition_type, owner_player_uid, is_rare_pal, is_awakening`) matches from
  `diff.py` (Task 5) through `watcher.py`'s `snapshot`/`id` additions (Tasks 6-7) to `api.py`'s
  response (Task 8), and matches the field names swee's `palfeed` consumer spec expects.
