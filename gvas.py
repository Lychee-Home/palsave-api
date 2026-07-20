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
