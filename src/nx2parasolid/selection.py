"""Rules for choosing which measured bodies make up a part.

``body`` dicts carry SolidWorks-measured properties: ``solid`` (bool), ``volume`` (mm^3),
``area`` (mm^2), ``box`` ([xmin, ymin, zmin, xmax, ymax, zmax] mm), ``faces``.
"""
from __future__ import annotations

from typing import Iterable

from .display import DisplayShape


def same_body(a: dict, b: dict, rel: float = 1e-6, box_tol: float = 1e-3) -> bool:
    """Geometrically identical bodies (NX keeps WAVE-linked copies of the same body)."""
    if a["solid"] != b["solid"]:
        return False
    if abs(a["area"] - b["area"]) > rel * max(1.0, a["area"]):
        return False
    if a["solid"] and abs(a["volume"] - b["volume"]) > rel * max(1.0, abs(a["volume"])):
        return False
    if not a.get("box") or not b.get("box"):
        return False
    if not all(abs(x - y) < box_tol for x, y in zip(a["box"], b["box"])):
        return False
    return a["solid"] or a.get("faces") == b.get("faces")


def shown_by(shape: DisplayShape, body: dict, area_rel: float = 0.02, tol: float = 1.0) -> bool:
    """Does NX's display shape ``shape`` show ``body``?

    Solids: display area within ``area_rel`` of the body area and display box inside the body
    box + ``tol`` mm (the display mesh is coarse); or an exact box with area within 5 %.
    Sheets: area of an open sheet is not reliable in every CAD system, so a tight box is used
    (display box inside the body box, every side within 3 mm).
    """
    b = body.get("box")
    if not b:
        return False
    a = shape.bbox_mm
    if not body["solid"]:
        inside = all(a[i] >= b[i] - 0.05 for i in range(3)) and all(a[i] <= b[i] + 0.05 for i in range(3, 6))
        return inside and all(abs(x - y) <= 3.0 for x, y in zip(a, b))
    area = body["area"]
    if area <= 0:
        return False
    if all(abs(x - y) <= 0.05 for x, y in zip(a, b)) and abs(shape.area_mm2 - area) <= 0.05 * area:
        return True
    if abs(shape.area_mm2 - area) > area_rel * area:
        return False
    return all(a[i] >= b[i] - tol for i in range(3)) and all(a[i] <= b[i] + tol for i in range(3, 6))


def is_displayed(body: dict, shapes: Iterable[DisplayShape]) -> bool:
    return any(shown_by(s, body) for s in shapes)
