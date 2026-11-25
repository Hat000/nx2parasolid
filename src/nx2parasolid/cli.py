"""Command-line interface: ``nx2parasolid <command> ...``."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

from . import __version__
from . import solidworks as sw
from .census import census
from .container import FASTLOAD_JT, UG_PART, PrtFile
from .core import convert_prt
from .display import display_shapes
from .errors import Nx2ParasolidError, SchemaNotFound
from .schema import ENV_VAR, SchemaStore
from .streams import extract_streams
from .structure import build_tree, component_names, preview_jpeg
from .xt import read_transmit


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _expand(inputs: List[str]) -> List[Path]:
    out: List[Path] = []
    for i in inputs:
        p = Path(i)
        out += sorted(p.glob("*.prt")) if p.is_dir() else [p]
    return out


# --------------------------------------------------------------------------- convert
def cmd_convert(a: argparse.Namespace) -> int:
    prts = _expand(a.inputs)
    if not prts:
        print("no .prt files found", file=sys.stderr)
        return 1
    if a.solidworks and not sw.available():
        print("--solidworks needs Windows with SolidWorks installed "
              f"(or set {sw.ENV_REDIST} to its api\\redist folder)", file=sys.stderr)
        return 1
    schemas = SchemaStore(a.schema_dir)
    if not schemas.files:
        _log(f"warning: no Parasolid schema files found (use --schema-dir or {ENV_VAR}); "
             "NX's own part transmits will still be copied, partition-only bodies cannot be extracted")
    out_root = Path(a.out)
    results = []
    for prt in prts:
        r = convert_prt(prt, out_root / prt.stem, schemas, use_solidworks=a.solidworks,
                        all_bodies=a.all_bodies, keep_raw=not a.no_raw, log=_log)
        for w in r.warnings:
            _log(f"{r.part}: warning: {w}")
        results.append(r)
    summary = [dict(part=r.part, files=len(r.files), skipped=len(r.skipped), verdict=r.verdict,
                    error=r.error, warnings=len(r.warnings)) for r in results]
    (out_root / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    n_err = sum(1 for r in results if r.error)
    n_chk = sum(1 for r in results if r.verdict == "CHECK")
    with_geo = sum(1 for r in results if r.files)
    line = f"done: {len(results)} file(s), {with_geo} with geometry"
    if a.solidworks:
        line += f", {sum(1 for r in results if r.verdict == 'PASS')} PASS, {n_chk} CHECK"
    line += f", {n_err} error(s) -> {out_root.resolve()}"
    _log(line)
    return 1 if n_err else (2 if n_chk else 0)


# --------------------------------------------------------------------------- info
def _info(path: Path, schemas: SchemaStore) -> dict:
    prt = PrtFile.open(path)
    streams = extract_streams(prt)
    info = dict(file=str(path), size=len(prt.data), container=prt.magic.decode("latin1", "replace"),
                container_streams=len(prt.entries), parasolid=dict(Counter(s.kind for s in streams)),
                schema_keys=sorted({s.schema_key for s in streams}), bodies=None,
                display_shapes=len(display_shapes(prt)) if prt.has(FASTLOAD_JT) else 0,
                components=component_names(prt, path.stem), has_model=prt.has(UG_PART))
    try:
        rows = []
        for s in streams:
            if s.kind == "deltas":
                continue
            rows += [dict(stream=s.index, kind=s.kind, **vars(c))
                     for c in census(read_transmit(s.data, schemas.get(s.schema_key)))]
        info["bodies"] = rows
    except SchemaNotFound as e:
        info["bodies_note"] = str(e)
    return info


def cmd_info(a: argparse.Namespace) -> int:
    schemas = SchemaStore(a.schema_dir)
    infos = [_info(p, schemas) for p in _expand(a.inputs)]
    if a.json:
        print(json.dumps(infos, indent=1))
        return 0
    for i in infos:
        print(f"{Path(i['file']).name}  ({i['size'] / 1e6:.1f} MB, {i['container']} container, "
              f"{i['container_streams']} streams)")
        ps = i["parasolid"]
        if not ps:
            print("  Parasolid: none (assembly or skeleton part without bodies)")
        else:
            print("  Parasolid: " + ", ".join(f"{n} {k}" for k, n in sorted(ps.items()))
                  + f"   schema {', '.join(i['schema_keys'])}")
        if i["bodies"] is not None and ps:
            with_faces = [b for b in i["bodies"] if b["faces"]]
            faces = sum(b["faces"] for b in with_faces)
            surf: Counter = Counter()
            for b in with_faces:
                surf.update(b["surfaces"])
            print(f"  Bodies:    {len(with_faces)} with faces ({faces} faces), "
                  f"{len(i['bodies']) - len(with_faces)} construction (no faces)")
            if surf:
                print("  Surfaces:  " + ", ".join(f"{k.lower()} {v}" for k, v in surf.most_common()))
        elif ps:
            print(f"  Bodies:    not decoded - {i.get('bodies_note', '')}")
        print(f"  Displayed by NX: {i['display_shapes']} shapes")
        if i["components"]:
            print(f"  Uses:      {', '.join(i['components'])}")
    return 0


# --------------------------------------------------------------------------- tree
def cmd_tree(a: argparse.Namespace) -> int:
    root = build_tree(Path(a.input), Path(a.folder) if a.folder else None)
    missing = []

    def show(n, depth=0):
        tag = ""
        if n.missing:
            tag = "  [file missing]"
            missing.append(n.name)
        elif n.repeated:
            tag = "  (see above)"
        print("  " * depth + ("- " if depth else "") + n.name + tag)
        for c in n.children:
            show(c, depth + 1)

    show(root)
    if missing:
        names = sorted(set(missing))
        print(f"\n{len(names)} referenced part file(s) not found: {', '.join(names)}")
    print("Note: NX lists each child once per parent; quantities and placements are not shown.")
    return 0


# --------------------------------------------------------------------------- preview / schemas
def cmd_preview(a: argparse.Namespace) -> int:
    p = Path(a.input)
    jpg = preview_jpeg(PrtFile.open(p))
    if not jpg:
        print("no preview image in this file", file=sys.stderr)
        return 1
    out = Path(a.out) if a.out else p.with_suffix(".jpg")
    out.write_bytes(jpg)
    print(f"wrote {out} ({len(jpg)} bytes)")
    return 0


def cmd_schemas(a: argparse.Namespace) -> int:
    s = SchemaStore(a.schema_dir)
    if not s.files:
        print(f"no Parasolid schema files found. Searched: {', '.join(map(str, s.searched)) or 'nothing'}")
        print(f"Set --schema-dir or {ENV_VAR} to a folder holding sch_<n>.s_t files "
              "(e.g. <SOLIDWORKS install>\\data\\pschema).")
        return 1
    dirs = sorted({str(p.parent) for p in s.files.values()})
    print(f"{len(s.files)} schema files in {', '.join(dirs)}")
    print("base schemas: " + " ".join(s.available()))
    print(f"SolidWorks backend: {'available' if sw.available() else 'not available'}")
    return 0


# --------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nx2parasolid",
        description="Extract exact B-rep geometry from Siemens NX .prt files as Parasolid, without NX.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def schema_opt(p):
        p.add_argument("--schema-dir", help=f"folder with Parasolid schema files (or set {ENV_VAR})")

    c = sub.add_parser("convert", help="convert .prt files to Parasolid")
    c.add_argument("inputs", nargs="+", help=".prt files and/or folders of .prt files")
    c.add_argument("-o", "--out", required=True, help="output folder (one sub-folder per part)")
    c.add_argument("--solidworks", action="store_true",
                   help="merge each part into one checked .x_t (+ .SLDPRT) using SolidWorks")
    c.add_argument("--all-bodies", action="store_true",
                   help="with --solidworks: keep every distinct body, not only those NX displays")
    c.add_argument("--no-raw", action="store_true", help="do not write the raw/ stream dump")
    schema_opt(c)
    c.set_defaults(fn=cmd_convert)

    i = sub.add_parser("info", help="what is inside a .prt (streams, bodies, surfaces, components)")
    i.add_argument("inputs", nargs="+")
    i.add_argument("--json", action="store_true")
    schema_opt(i)
    i.set_defaults(fn=cmd_info)

    t = sub.add_parser("tree", help="assembly tree, resolving children to .prt files")
    t.add_argument("input")
    t.add_argument("--folder", help="where the child .prt files are (default: next to input)")
    t.set_defaults(fn=cmd_tree)

    p = sub.add_parser("preview", help="save the embedded JPEG thumbnail")
    p.add_argument("input")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_preview)

    s = sub.add_parser("schemas", help="show which Parasolid schema files were found")
    schema_opt(s)
    s.set_defaults(fn=cmd_schemas)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except Nx2ParasolidError as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
