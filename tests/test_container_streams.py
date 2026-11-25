import pytest
import synth

from nx2parasolid.container import PREVIEW, UG_PART, PrtFile, read_directory
from nx2parasolid.display import display_shapes
from nx2parasolid.errors import NotAnNXFile, PossiblyEncrypted
from nx2parasolid.streams import extract_streams, iter_zlib
from nx2parasolid.structure import component_names, preview_jpeg, string_lists


def test_directory(sample_prt):
    prt = PrtFile.open(sample_prt)
    assert prt.magic == b"SPLMSSTR"
    assert UG_PART in prt.names() and prt.has(PREVIEW)
    assert prt.stream("/Root/does/not/exist") == b""


def test_not_an_nx_file():
    with pytest.raises(NotAnNXFile):
        read_directory(b"hello world, definitely not a part file")


def test_extract_streams_kinds_and_header(sample_prt):
    streams = extract_streams(PrtFile.open(sample_prt))
    assert [s.kind for s in streams] == ["part", "partition"]
    assert all(s.schema_key == synth.SCHEMA_KEY for s in streams)
    # the file's own text header is reused because it names the schema
    assert streams[0].header == synth.TEXT_HEADER
    assert streams[0].data.startswith(synth.TEXT_HEADER) and streams[0].ext == ".x_b"


def test_generic_header_when_file_has_none(tmp_path):
    ug = synth.ug_part([synth.part_transmit(1)], with_header=False)
    prt = PrtFile(synth.make_prt({UG_PART: ug}))
    s = extract_streams(prt)[0]
    assert b"SCH=" + synth.SCHEMA_SHORT.encode() + b";" in s.header
    assert b"%" in s.header        # the literal Parasolid character line survives formatting


def test_stops_on_non_zlib_after_header():
    prt = PrtFile(synth.make_prt({UG_PART: synth.ug_part([], junk_after_header=True)}))
    with pytest.raises(PossiblyEncrypted):
        extract_streams(prt)


def test_iter_zlib_skips_garbage():
    import zlib
    blob = b"\x78\x9cnot-really" + zlib.compress(b"payload") + b"\x78\xda\x00"
    assert [p for _o, _u, p in iter_zlib(blob)] == [b"payload"]


def test_structure_component_names(sample_prt):
    prt = PrtFile.open(sample_prt)
    assert component_names(prt) == ["BRACKET-01", "BOLT M6"]
    lists = [items for _o, items in string_lists(prt.stream("/Root/FastLoad/Structure"))]
    assert ["MODEL", "Entire Part", "Empty"] in lists      # reference-set list is not a component list


def test_no_components():
    prt = PrtFile(synth.make_prt({UG_PART: b"x", "/Root/FastLoad/Structure": synth.structure_stream([])}))
    assert component_names(prt) == []


def test_display_shapes(sample_prt):
    shapes = display_shapes(PrtFile.open(sample_prt))
    assert len(shapes) == 1
    assert shapes[0].bbox_mm == [0, 0, 0, 1.0, 1.0, 1.0] and shapes[0].area_mm2 == 6.0


def test_preview(sample_prt):
    assert preview_jpeg(PrtFile.open(sample_prt)).startswith(b"\xff\xd8")
