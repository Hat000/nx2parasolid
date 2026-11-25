"""Optional SolidWorks backend (Windows, SolidWorks installed).

Used to merge the bodies of a part into ONE checked Parasolid ``.x_t`` (+ ``.SLDPRT``):

1. ``measure``: load each Parasolid file and report every body's volume, area, centre of mass,
   box, face count and SolidWorks Check faults;
2. ``build``: copy the chosen bodies unchanged into a new part (picked by volume/area/centre of
   mass, since SolidWorks does not keep body order stable), run Check, save, export ``.x_t``,
   then re-import the ``.x_t`` and report its bodies so the round trip can be verified.

The work is done by the PowerShell script ``solidworks_combine.ps1`` shipped with this package,
through the SolidWorks .NET interop assemblies. SolidWorks is started if it is not running; it
only opens and closes documents it creates.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .errors import SolidWorksError

ENV_REDIST = "NX2PARASOLID_SW_REDIST"
_DEFAULT_REDIST = [
    r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist",
    r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS (2)\api\redist",
]
SCRIPT = Path(__file__).with_name("solidworks_combine.ps1")


def find_redist() -> Optional[Path]:
    cands = [os.environ[ENV_REDIST]] if os.environ.get(ENV_REDIST) else []
    for c in cands + _DEFAULT_REDIST:
        p = Path(c)
        if (p / "SolidWorks.Interop.sldworks.dll").exists():
            return p
    return None


def available() -> bool:
    return sys.platform == "win32" and find_redist() is not None and shutil.which("powershell") is not None


def _run(args: List[str], timeout: float) -> None:
    redist = find_redist()
    if redist is None:
        raise SolidWorksError(f"SolidWorks API (SolidWorks.Interop.sldworks.dll) not found; set {ENV_REDIST}")
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
           "-Redist", str(redist)] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SolidWorksError(f"SolidWorks step timed out after {timeout:.0f} s") from e
    if r.returncode != 0:
        raise SolidWorksError(f"SolidWorks step failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")


def _jsonl(path: Path) -> List[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]


def measure(files: List[Path], work: Path, timeout: float = 3600) -> List[dict]:
    """Per-body properties of every body in ``files``."""
    lst, out = work / "sw_files.txt", work / "sw_measure.jsonl"
    lst.write_text("\n".join(str(f.resolve()) for f in files), encoding="utf-8")
    _run(["-Mode", "measure", "-FileList", str(lst), "-Out", str(out)], timeout)
    rows = _jsonl(out)
    bodies = []
    for r in rows:
        if "k" not in r:
            raise SolidWorksError(f"SolidWorks could not load {r.get('file')}: {r}")
        bodies.append(dict(file=r["file"], k=r["k"], solid=r["solid"], faces=r["faces"],
                           check_faults=r["check_faults"], volume=r.get("volume_mm3", 0.0),
                           area=r.get("area_mm2", 0.0), com=r.get("com_mm"), box=r.get("box_mm")))
    return bodies


def build(bodies: List[dict], out_base: Path, work: Path, timeout: float = 3600) -> Dict:
    """Combine ``bodies`` into ``out_base``.x_t / .SLDPRT; returns SolidWorks' report."""
    plan, res = work / "sw_plan.json", work / "sw_build.json"
    plan.write_text(json.dumps([dict(file=b["file"], solid=b["solid"], volume_mm3=b["volume"],
                                     area_mm2=b["area"], com_mm=b["com"]) for b in bodies]))
    _run(["-Mode", "build", "-Plan", str(plan), "-OutBase", str(out_base.resolve()), "-Out", str(res)], timeout)
    return json.loads(res.read_text(encoding="utf-8-sig"))


def verify(kept: List[dict], report: Dict, rel_tol: float = 1e-5) -> Dict:
    """Compare the re-imported .x_t body by body with the source bodies."""
    back = report.get("reimport_bodies", [])
    v = dict(requested=len(kept), inserted=report.get("inserted"), reimported=len(back),
             check_faults=sum(b["check_faults"] for b in report.get("part_bodies", [])),
             messages=report.get("messages", []))
    worst_v = worst_a = worst_c = 0.0
    free = list(range(len(back)))
    for b in kept:
        best, bd = None, None
        for j in free:
            r = back[j]
            if r["solid"] != b["solid"] or "com_mm" not in r:
                continue
            d = sum((x - y) ** 2 for x, y in zip(r["com_mm"], b["com"])) ** 0.5
            if b["solid"]:
                d += abs(r["volume_mm3"] - b["volume"]) / max(1.0, abs(b["volume"])) * 1e3
            if bd is None or d < bd:
                best, bd = j, d
        if best is None:
            continue
        free.remove(best)
        r = back[best]
        worst_c = max(worst_c, sum((x - y) ** 2 for x, y in zip(r["com_mm"], b["com"])) ** 0.5)
        worst_a = max(worst_a, abs(r["area_mm2"] - b["area"]) / max(1e-12, b["area"]))
        if b["solid"]:
            worst_v = max(worst_v, abs(r["volume_mm3"] - b["volume"]) / max(1e-12, abs(b["volume"])))
    v.update(worst_rel_volume=worst_v, worst_rel_area=worst_a, worst_com_mm=worst_c)
    ok = (v["reimported"] == len(kept) == v["inserted"] and v["check_faults"] == 0
          and worst_v <= rel_tol and worst_a <= rel_tol and not v["messages"])
    v["verdict"] = "PASS" if ok else "CHECK"
    return v
