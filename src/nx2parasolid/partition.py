"""Turn the bodies of a Parasolid partition into stand-alone part transmits.

A partition transmit holds a WORLD node and every body of the partition, linked to each other.
CAD systems do not read partitions, and NX keeps some bodies *only* there. For each BODY:

* the BODY becomes the root node;
* every node reachable from it through pointer fields is written - except the WORLD and the
  other bodies, which are not followed;
* the body's own ``next`` / ``previous`` / ``owner`` links become null;
* nodes are renumbered 2..N in that order (BODY = 2, as in NX's own part transmits).

No geometry is computed or changed: every field value is copied bit-for-bit; only node index
values (pointers) change.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Set

from .xt import NULL, Node, Transmit, write_transmit

_SIBLING_LINKS = ("next", "previous", "owner")


@dataclass
class BodyTransmit:
    body_index: int       # index of the BODY node in the source transmit
    node_count: int
    data: bytes           # complete Parasolid part file


def reachable(tx: Transmit, root: int, stop: Callable[[Node, str, int], bool]) -> List[int]:
    """Breadth-first list of node indices reachable from ``root`` (``root`` first)."""
    by = tx.by_index
    order: List[int] = []
    seen: Set[int] = {root}
    queue = deque([root])
    while queue:
        i = queue.popleft()
        order.append(i)
        nd = by[i]
        for fname, t, _n in tx.layout[nd.type][0]:
            if t != "p":
                continue
            v = nd.fields[fname]
            for x in (v if isinstance(v, list) else [v]):
                if x == NULL or x in seen or x not in by or stop(nd, fname, x):
                    continue
                seen.add(x)
                queue.append(x)
    return order


def split_bodies(tx: Transmit) -> List[BodyTransmit]:
    """One part transmit per BODY of ``tx`` (normally a partition)."""
    world = {n.index for n in tx.nodes_named("WORLD")}
    bodies = tx.nodes_named("BODY")
    title = tx.title.replace(" (partition)", "")
    out: List[BodyTransmit] = []
    for body in bodies:
        others: Dict[int, bool] = {b.index: True for b in bodies if b is not body}

        def stop(nd: Node, fname: str, x: int, body: Node = body, others=others) -> bool:
            if x in world or x in others:
                return True
            return nd is body and fname in _SIBLING_LINKS

        def keep(nd: Node, fname: str, v, body: Node = body):
            return NULL if (nd is body and fname in _SIBLING_LINKS) else v

        order = reachable(tx, body.index, stop)
        out.append(BodyTransmit(body.index, len(order), write_transmit(tx, order, title=title, keep=keep)))
    return out
