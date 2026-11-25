import json

import pytest
import synth

from nx2parasolid import convert_prt
from nx2parasolid.cli import main
from nx2parasolid.display import DisplayShape
from nx2parasolid.errors import SchemaNotFound
from nx2parasolid.schema import SchemaStore, base_schema_id
from nx2parasolid.selection import same_body, shown_by
from nx2parasolid.solidworks import verify
from nx2parasolid.xt import read_transmit


def test_convert_writes_bodies_and_manifest(sample_prt, schema_dir, tmp_path):
    out = tmp_path / "out"
    res = convert_prt(sample_prt, out, SchemaStore(schema_dir))
    assert res.error is None and not res.warnings
    names = sorted(f.file for f in res.files)
    # part transmit kept as is; partition split into 3 bodies, the faceless one skipped
    assert names == ["SAMPLE-PART__00.x_b", "SAMPLE-PART__01_partition_b00.x_b", "SAMPLE-PART__01_partition_b02.x_b"]
    assert len(res.skipped) == 1 and "no faces" in res.skipped[0]["why"]
    schema = synth.schema_dict()
    for f in res.files:
        tx = read_transmit((out / "bodies" / f.file).read_bytes(), schema)
        assert tx.nodes[0].name == "BODY"
    assert (out / "raw" / "SAMPLE-PART__01.p_b").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["part"] == "SAMPLE-PART" and len(manifest["files"]) == 3


def test_convert_without_schema_copies_part_transmits(sample_prt, schema_dir, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = convert_prt(sample_prt, tmp_path / "out", SchemaStore(empty))
    assert [f.file for f in res.files] == ["SAMPLE-PART__00.x_b"]
    assert any("partition-only bodies were NOT extracted" in w for w in res.warnings)


def test_cli_convert_info_tree(sample_prt, schema_dir, tmp_path, capsys):
    out = tmp_path / "cli"
    assert main(["convert", str(sample_prt), "-o", str(out), "--schema-dir", str(schema_dir)]) == 0
    assert (out / "SAMPLE-PART" / "bodies" / "SAMPLE-PART__00.x_b").exists()
    assert json.loads((out / "summary.json").read_text())[0]["files"] == 3
    assert main(["info", str(sample_prt), "--schema-dir", str(schema_dir)]) == 0
    text = capsys.readouterr().out
    assert "3 with faces" in text and "BRACKET-01" in text
    assert main(["tree", str(sample_prt)]) == 0
    assert "BOLT M6  [file missing]" in capsys.readouterr().out
    assert main(["preview", str(sample_prt), "-o", str(tmp_path / "p.jpg")]) == 0


def test_cli_reports_protected_file(tmp_path, schema_dir):
    p = tmp_path / "LOCKED.prt"
    p.write_bytes(synth.make_prt({"/Root/UG_PART/UG_PART": synth.ug_part([], junk_after_header=True)}))
    assert main(["convert", str(p), "-o", str(tmp_path / "o"), "--schema-dir", str(schema_dir)]) == 1
    m = json.loads((tmp_path / "o" / "LOCKED" / "manifest.json").read_text())
    assert m["error"].startswith("PossiblyEncrypted")


def test_schema_lookup(schema_dir, monkeypatch, tmp_path):
    assert base_schema_id("SCH_3801167_38101_13006") == "13006"
    with pytest.raises(SchemaNotFound):
        base_schema_id("garbage")
    monkeypatch.setenv("NX2PARASOLID_SCHEMA_DIR", str(schema_dir))
    store = SchemaStore()
    assert store.available() == [synth.BASE_ID]
    assert 12 in store.get(synth.SCHEMA_KEY)
    with pytest.raises(SchemaNotFound, match="sch_13006"):
        store.get("SCH_1_2_13006")


def _body(**kw):
    b = dict(solid=True, volume=1000.0, area=600.0, box=[0, 0, 0, 10, 10, 10], faces=6, com=[5, 5, 5])
    b.update(kw)
    return b


def test_same_body_and_display_match():
    assert same_body(_body(), _body(volume=1000.0000001))
    assert not same_body(_body(), _body(volume=1001.0))
    assert not same_body(_body(), _body(solid=False))
    shape = DisplayShape(0, [0.2, 0.2, 0.2, 9.8, 9.8, 9.8], 595.0)          # coarse mesh, inside the box
    assert shown_by(shape, _body())
    assert not shown_by(DisplayShape(0, [0, 0, 0, 10, 10, 10], 300.0), _body())   # area far off
    assert not shown_by(DisplayShape(0, [0, 0, 0, 30, 10, 10], 600.0), _body())   # outside the box


def test_verify_round_trip_report():
    kept = [_body(), _body(volume=50.0, area=80.0, com=[1, 2, 3])]
    good = dict(inserted=2, part_bodies=[dict(check_faults=0)] * 2, messages=[], reimport_bodies=[
        dict(solid=True, volume_mm3=50.0, area_mm2=80.0, com_mm=[1, 2, 3]),
        dict(solid=True, volume_mm3=1000.0, area_mm2=600.0, com_mm=[5, 5, 5])])
    assert verify(kept, good)["verdict"] == "PASS"
    bad = dict(good, reimport_bodies=[dict(good["reimport_bodies"][0]),
                                      dict(solid=True, volume_mm3=1000.1, area_mm2=600.0, com_mm=[5, 5, 5])])
    v = verify(kept, bad)
    assert v["verdict"] == "CHECK" and v["worst_rel_volume"] == pytest.approx(1e-4)
