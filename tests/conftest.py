import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import synth  # noqa: E402


@pytest.fixture
def schema_dir(tmp_path, monkeypatch):
    d = tmp_path / "pschema"
    d.mkdir()
    (d / f"sch_{synth.BASE_ID}.s_t").write_text(synth.SCHEMA_TEXT, encoding="latin1")
    # keep the machine's real schema locations out of the tests
    monkeypatch.delenv("NX2PARASOLID_SCHEMA_DIR", raising=False)
    monkeypatch.delenv("P_SCHEMA", raising=False)
    monkeypatch.setattr("nx2parasolid.schema._KNOWN_GLOBS", [])
    return d


@pytest.fixture
def sample_prt(tmp_path):
    """A synthetic part: one part transmit (3 faces) and one partition (bodies with 2, 0, 4 faces)."""
    ug = synth.ug_part([synth.part_transmit(3), synth.partition_transmit((2, 0, 4))])
    data = synth.make_prt({
        "/Root/UG_PART/UG_PART": ug,
        "/Root/FastLoad/JT": synth.jt_segment([([0, 0, 0, 0.001, 0.001, 0.001], 6e-6)]),
        "/Root/FastLoad/Structure": synth.structure_stream(["BRACKET-01", "BOLT M6"]),
        "/Root/images/preview": b"\xff\xd8\xff\xe0fakejpeg\xff\xd9",
    })
    p = tmp_path / "SAMPLE-PART.prt"
    p.write_bytes(data)
    return p
