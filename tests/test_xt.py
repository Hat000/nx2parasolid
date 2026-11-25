import struct

import pytest
import synth

from nx2parasolid.census import census
from nx2parasolid.errors import XTDecodeError
from nx2parasolid.partition import split_bodies
from nx2parasolid.xt import NULL, read_transmit, schema_key_of, split_header, write_transmit


def test_roundtrip_is_byte_identical():
    data = synth.partition_transmit()
    tx = read_transmit(data, synth.schema_dict())
    assert tx.schema_key == synth.SCHEMA_KEY
    again = write_transmit(tx, [n.index for n in tx.nodes])
    assert again == data


def test_decoded_values():
    tx = read_transmit(synth.part_transmit(2), synth.schema_dict())
    body = tx.nodes_named("BODY")[0]
    assert body.index == 2 and body.fields["body_type"] == 1
    assert "scratch" not in body.fields          # not transmitted per the schema
    planes = tx.nodes_named("PLANE")
    assert planes[1].fields["pos"] == (0.0, 0.0, 1.0)
    assert planes[0].fields["box"] == (0, 0, 0, 1, 1, 1)


def test_variable_length_nodes():
    tx = read_transmit(synth.partition_transmit((1, 1)), synth.schema_dict())
    lst = tx.nodes_named("POINTER_LIS_BLOCK")[0]
    assert lst.nvar == 2 and len(lst.fields["entries"]) == 2
    assert tx.nodes_named("NAME_TEXT")[0].fields["text"] == b"hello"


def test_census_counts_faces_and_surfaces():
    rows = census(read_transmit(synth.partition_transmit((2, 0, 4)), synth.schema_dict()))
    assert [r.faces for r in rows] == [2, 0, 4]
    assert rows[2].surfaces == {"PLANE": 4}
    assert not rows[1].has_faces


def test_split_partition_into_parts():
    schema = synth.schema_dict()
    tx = read_transmit(synth.partition_transmit((2, 0, 4)), schema)
    parts = split_bodies(tx)
    assert len(parts) == 3
    for p, expected_faces in zip(parts, (2, 0, 4)):
        out = read_transmit(p.data, schema)
        root = out.nodes[0]
        assert root.name == "BODY" and root.index == 2
        assert root.fields["next"] == NULL and root.fields["previous"] == NULL and root.fields["owner"] == NULL
        assert not out.nodes_named("WORLD") and len(out.nodes_named("BODY")) == 1
        assert census(out)[0].faces == expected_faces
        assert "(partition)" not in out.title
    # geometry values are copied exactly
    src_planes = [n.fields["pos"] for n in tx.nodes_named("PLANE")]
    out_planes = [n.fields["pos"] for p in parts for n in read_transmit(p.data, schema).nodes_named("PLANE")]
    assert out_planes == src_planes


def test_wide_pointers_roundtrip():
    """Indices >= 0x8000 are written as two u16 with the high bit set (real bodies get there)."""
    b = synth.Builder()
    synth.add_body(b, 2, 16400)                    # 3 + 2 * 16400 nodes -> indices past 0x8000
    data = b.encode()
    tx = read_transmit(data, b.schema)
    assert max(n.index for n in tx.nodes) > 0x8000
    assert tx.nodes_named("FACE")[-1].fields["surface"] == tx.nodes[-1].index
    assert census(tx)[0].faces == 16400
    assert write_transmit(tx, [n.index for n in tx.nodes]) == data


def test_schema_edit_codes_and_inline_types():
    """A file that declares its own layout: FACE edited with C/D/A codes, plus a node type the
    base schema does not know, defined inline."""
    b = synth.Builder()
    # FACE: copy next, drop surface, copy tolerance, copy sense, append 'flags' (u8) -> 4 fields
    face_def = bytes([4]) + b"C" + b"D" + b"C" + b"C" + b"A" + bytes([5]) + b"flags" + \
        struct.pack(">HH", 0, 2) + b"\x00u" + b"Z"
    b.extra_defbytes[14] = face_def
    b.extra_layout[14] = ([("next", "p", 1), ("tolerance", "f", 1), ("sense", "c", 1), ("flags", "u", 1)], 0, "FACE")
    # new type 900: two fields (a vector and a pointer), not variable
    new_def = bytes([2]) + bytes([7]) + b"GADGETS" + bytes([4]) + b"desc" + \
        bytes([3]) + b"vec" + struct.pack(">HH", 0, 2) + b"\x00v" + \
        bytes([3]) + b"ref" + struct.pack(">HH", 14, 2) + bytes([0])
    b.extra_defbytes[900] = new_def
    b.extra_layout[900] = ([("vec", "v", 1), ("ref", "p", 1)], 0, "GADGETS")
    b.add(12, 2, next=NULL, previous=NULL, owner=NULL, body_type=1, region=NULL, shell=NULL)
    b.add(14, 3, next=NULL, tolerance=0.5, sense=1, flags=7)
    b.add(900, 4, vec=(1.0, 2.0, 3.0), ref=3)
    data = b.encode()
    tx = read_transmit(data, synth.schema_dict())
    face = tx.nodes_named("FACE")[0]
    assert face.fields == {"next": NULL, "tolerance": 0.5, "sense": 1, "flags": 7}
    g = tx.nodes_named("GADGETS")[0]
    assert g.fields == {"vec": (1.0, 2.0, 3.0), "ref": 3}
    assert write_transmit(tx, [n.index for n in tx.nodes]) == data


def test_truncated_data_raises():
    data = synth.part_transmit(2)
    with pytest.raises(XTDecodeError):
        read_transmit(data[:-30], synth.schema_dict())


def test_header_helpers():
    data = synth.TEXT_HEADER + synth.part_transmit(1)
    head, body = split_header(data)
    assert head == synth.TEXT_HEADER and body.startswith(b"PS\x00\x00\x00")
    assert schema_key_of(data) == synth.SCHEMA_KEY
    with pytest.raises(XTDecodeError):
        split_header(b"not parasolid")
