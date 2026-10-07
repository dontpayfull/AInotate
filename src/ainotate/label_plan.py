"""Automatic labels, planned together.

Placing labels one by one in mark order is greedy: an early label can take the only clean spot
of a later one, which then lands far away with a long arrow over the page. The planner places
them in several orders (all of them for up to 3 labels; otherwise mark order, reversed, and each
badly placed label first) and keeps the order whose labels score best in total. Mark order is
tried first, and when every label in it is clean nothing else is tried.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations

from .geometry import contains, path_length

CLEAN = -40        # a label scoring at least this is fine where it is (little content under it or its arrow)
RELAXED = 200      # score penalty for a label that only fit with relaxed obstacles
LAST_RESORT = 500  # ... or that only fit by ignoring every mark except other labels


@dataclass
class Job:
    i: int                   # mark index
    target: list             # what the arrow points at (outline, rect or the mark's own box)
    size: tuple              # label pill size
    others: list             # strict obstacles: other marks and targets
    relaxed: list            # crowded fallback: only other targets and fixed marks
    near: float
    kw: dict = field(default_factory=dict)   # arrow, curve, style, tip for place_label
    mine: list = field(default_factory=list)  # the mark's own badge


def plan_labels(jobs, place, route, cmap, u, placed, lines):
    """({mark index: label box}, {mark index: score}) for every job that fits. place = placement.place_label (looked
    up by the caller, so tests can swap it), route(box, job, avoid, lines) -> arrow path."""
    if not jobs:
        return {}, {}
    memo = {}

    def run(order):
        """Place jobs in this order; memoized on the prefix, so orders that share a start share
        the work. Returns (total score, {i: box}, {i: score})."""
        state = (0.0, {}, {}, list(placed), list(lines), [])
        for k in range(1, len(order) + 1):
            key = tuple(order[:k])
            if key not in memo:
                memo[key] = _step(state, jobs[order[k - 1]], place, route, cmap, u)
            state = memo[key]
            if state is None:
                return None
        return state[:3]

    n = len(jobs)
    best = run(list(range(n)))
    if best and all(s >= CLEAN for s in best[2].values()):
        return best[1], best[2]
    if n <= 3:
        orders = [list(p) for p in permutations(range(n))]
    else:
        bad = [k for k, j in enumerate(jobs) if not best or best[2].get(j.i, -1e9) < CLEAN]
        orders = [list(range(n))[::-1]] + [[b] + [k for k in range(n) if k != b] for b in bad]
    for order in orders:
        got = run(order)
        if got and (best is None or got[0] > best[0]):
            best = got
    if best:
        return best[1], best[2]
    # no order fits every label: keep what mark order placed, the caller reports the rest
    state = (0.0, {}, {}, list(placed), list(lines), [])
    for j in jobs:
        state = _step(state, j, place, route, cmap, u) or state
    return state[1], state[2]


def _step(state, j, place, route, cmap, u):
    """One more label on top of `state`, or None when it fits nowhere."""
    total, boxes, scores, placed, lines, prev = state
    for obstacles, near, extra, penalty in ((j.others, j.near, {}, 0), (j.relaxed, 2.5, {}, RELAXED),
                                            ([], 2.5, {"lines": []}, LAST_RESORT)):
        kw = dict(j.kw, lines=lines, prev=prev, mine=j.mine, with_score=True)
        kw.update(extra)
        res = place(cmap, j.target, j.size, obstacles, placed, u, near=near, **kw)
        if res is not None:
            break
    else:
        return None
    box, s = res if isinstance(res, tuple) else (res, 0.0)   # a swapped-in placer may not score
    s -= penalty
    new_lines = list(lines)
    if j.kw.get("arrow", True):
        avoid = [o for o in j.others + placed if o not in j.mine and not contains(o, j.target)]
        path = route(box, j, avoid, lines)
        if path_length(path) > 2.8 * u:
            new_lines.append(path)
    return (total + s, {**boxes, j.i: box}, {**scores, j.i: s}, placed + [box], new_lines,
            prev + [(box, j.target)])
