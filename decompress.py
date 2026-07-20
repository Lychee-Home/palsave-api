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
