"""The conversion pipeline: one NX ``.prt`` in, exact Parasolid out."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from . import solidworks as sw
from .census import census
from .container import PrtFile
from .display import display_shapes
from .errors import Nx2ParasolidError, SchemaNotFound
from .partition import split_bodies
from .schema import SchemaStore
from .selection import is_displayed, same_body
from .streams import extract_streams
from .xt import read_transmit

Log = Callable[[str], None]


@dataclass
class OutputFile:
    file: str                    # file name inside bodies/
    source: str                  # stream it came from
    kind: str                    # "part" (NX's own transmit) | "partition" (re-emitted body)
    bodies: List[Dict] = field(default_factory=list)   # census per body (empty if not decoded)


@dataclass
class PartResult:
    part: str
    input: str
    out_dir: str = ""
    streams: List[Dict] = field(default_factory=list)
    files: List[OutputFile] = field(default_factory=list)
    skipped: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    selection: Optional[Dict] = None
    bodies: Optional[List[Dict]] = None
    solidworks: Optional[Dict] = None
    error: Optional[str] = None

    @property
    def verdict(self) -> Optional[str]:
        return (self.solidworks or {}).get("verdict")

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, default=str)


def _census_rows(data: bytes, schemas: SchemaStore, key: str) -> List[Dict]:
    tx = read_transmit(data, schemas.get(key))
    return [asdict(c) for c in census(tx)]


def convert_prt(path: Union[str, Path], out_dir: Union[str, Path], schemas: Optional[SchemaStore] = None,
                use_solidworks: bool = False, all_bodies: bool = False, keep_raw: bool = True,
                log: Log = lambda s: None) -> PartResult:
    """Convert one NX part. Writes ``<out_dir>/bodies/*.x_b`` (always), ``raw/`` (if
    ``keep_raw``), ``<part>.x_t`` + ``.SLDPRT`` (with SolidWorks) and ``manifest.json``."""
    path, out = Path(path), Path(out_dir)
    schemas = schemas or SchemaStore()
    res = PartResult(part=path.stem, input=str(path.resolve()), out_dir=str(out.resolve()))
    try:
        _stage1(path, out, schemas, keep_raw, res, log)
        if use_solidworks and res.files:
            _stage2(path, out, all_bodies, res, log)
    except Nx2ParasolidError as e:
        res.error = f"{type(e).__name__}: {e}"
        log(f"{res.part}: {res.error}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(res.to_json(), encoding="utf-8")
    return res


def _stage1(path: Path, out: Path, schemas: SchemaStore, keep_raw: bool, res: PartResult, log: Log) -> None:
    prt = PrtFile.open(path)
    streams = extract_streams(prt)
    base = path.stem
    bodies_dir = out / "bodies"
    for d in (bodies_dir, out / "raw"):
        shutil.rmtree(d, ignore_errors=True)
    bodies_dir.mkdir(parents=True)
    if keep_raw and streams:
        (out / "raw").mkdir()
    for s in streams:
        name = f"{base}__{s.index:02d}{s.ext}"
        res.streams.append(dict(name=name, kind=s.kind, title=s.title, schema_key=s.schema_key,
                                size=len(s.body), compressed=s.compressed_size))
        if keep_raw:
            (out / "raw" / name).write_bytes(s.data)
    if not streams:
        log(f"{base}: no Parasolid data (assembly or skeleton part without bodies)")
        return
    missing_schema = 0
    for s in streams:
        name = f"{base}__{s.index:02d}"
        if s.kind == "deltas":
            continue
        try:
            schema = schemas.get(s.schema_key)
        except SchemaNotFound as e:
            schema = None
            missing_schema += 1
            if not res.warnings:
                res.warnings.append(str(e))
        if s.kind == "part":
            fn = f"{name}.x_b"
            (bodies_dir / fn).write_bytes(s.data)
            rows = _census_rows(s.data, schemas, s.schema_key) if schema else []
            _keep_or_skip(res, bodies_dir, OutputFile(fn, f"{name}.x_b", "part", rows), schema is not None)
        elif schema is not None:
            tx = read_transmit(s.data, schema)
            for k, bt in enumerate(split_bodies(tx)):
                fn = f"{name}_partition_b{k:02d}.x_b"
                (bodies_dir / fn).write_bytes(bt.data)
                rows = _census_rows(bt.data, schemas, s.schema_key)   # re-decode = self-check
                _keep_or_skip(res, bodies_dir, OutputFile(fn, f"{name}.p_b", "partition", rows), True)
    if missing_schema:
        res.warnings.append(f"{missing_schema} stream(s) not decoded (no schema): part transmits were "
                            f"copied as they are; partition-only bodies were NOT extracted")
    nb = sum(len(f.bodies) for f in res.files)
    log(f"{base}: {len(streams)} Parasolid streams -> {len(res.files)} files in bodies/"
        + (f" ({nb} bodies)" if nb else "") + f", {len(res.skipped)} faceless skipped")


def _keep_or_skip(res: PartResult, bodies_dir: Path, f: OutputFile, decoded: bool) -> None:
    if decoded and sum(b["faces"] for b in f.bodies) == 0:
        (bodies_dir / f.file).unlink()
        res.skipped.append(dict(asdict(f), why="no faces (point/wire construction body)"))
    else:
        res.files.append(f)


def _stage2(path: Path, out: Path, all_bodies: bool, res: PartResult, log: Log) -> None:
    if not sw.available():
        raise sw.SolidWorksError("--solidworks needs Windows with SolidWorks installed")
    work = out / "solidworks_work"
    work.mkdir(exist_ok=True)
    files = [out / "bodies" / f.file for f in res.files]
    log(f"{res.part}: measuring {len(files)} files in SolidWorks")
    bodies = sw.measure(files, work)
    distinct: List[Dict] = []
    for b in bodies:
        if any(same_body(b, d) for d in distinct):
            b.update(kept=False, why="duplicate of another body")
        else:
            distinct.append(b)
    shapes = display_shapes(PrtFile.open(path))
    use_display = bool(shapes) and not all_bodies
    for b in distinct:
        b["displayed_by_nx"] = is_displayed(b, shapes) if shapes else None
        b["kept"] = bool(b["displayed_by_nx"]) if use_display else True
        if not b["kept"]:
            b["why"] = "not in NX's display list for this part"
    kept = [b for b in distinct if b["kept"]]
    rule = ("displayed by NX" if use_display else
            "all distinct bodies" + (" (--all-bodies)" if all_bodies else " (no NX display data)"))
    res.selection = dict(rule=rule, display_shapes=len(shapes), measured=len(bodies),
                         distinct=len(distinct), kept=len(kept))
    res.bodies = bodies
    log(f"{res.part}: {len(bodies)} bodies measured, {len(distinct)} distinct, {len(kept)} kept ({rule})")
    if not kept:
        res.warnings.append("no bodies selected; nothing to combine")
        return
    out_base = out / res.part
    log(f"{res.part}: combining into {out_base.name}.x_t")
    report = sw.build(kept, out_base, work)
    res.solidworks = sw.verify(kept, report)
    res.solidworks["x_t"] = str(out_base.with_suffix(".x_t").resolve())
    v = res.solidworks
    log(f"{res.part}: {v['verdict']}  bodies {v['reimported']}/{v['requested']}, Check faults "
        f"{v['check_faults']}, worst rel. volume {v['worst_rel_volume']:.1e}, area "
        f"{v['worst_rel_area']:.1e}, centre of mass {v['worst_com_mm']:.1e} mm")
