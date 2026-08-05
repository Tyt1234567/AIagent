"""
Generic discrete Morse theory analysis over 2D scalar fields.

This is a from-scratch, pure Python/numpy reimplementation of the core
analyses used by the UMIACS GeoVis Lab's discrete-Morse terrain tools
(FormanGradient2D, Terrain_Trees -- https://geovis.umiacs.io/):

- Piecewise-linear critical point classification (minima / maxima / saddles)
  via connected components of the lower/upper vertex link (Banchoff's
  definition), which is the standard combinatorial stand-in for a Forman
  discrete gradient vector field.
- 0-dimensional persistence pairing (a merge tree over sublevel sets, and
  its dual split tree over superlevel sets) via a union-find sweep, giving
  each critical point a persistence value used to separate real topological
  features from noise -- the same role persistence plays in Terrain_Trees'
  "topology-aware simplification".
- Persistence-guided watershed segmentation: every vertex is assigned to a
  basin by steepest descent to a local minimum, then insignificant basins
  (persistence below a threshold) are merged into their spill partner.

None of this is specific to terrain: the same engine runs unmodified on
any scalar field sampled on a regular grid (elevation, hazard intensity,
population density, or anything else placed on a GeoDataset).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ScalarField:
    """A scalar quantity sampled on a regular (ny, nx) grid over the domain."""

    name: str
    xs: np.ndarray  # shape (nx,)
    ys: np.ndarray  # shape (ny,)
    values: np.ndarray  # shape (ny, nx)

    @property
    def shape(self) -> tuple[int, int]:
        return self.values.shape

    def coords(self, i: int, j: int) -> tuple[float, float]:
        return float(self.xs[j]), float(self.ys[i])


# -- triangulated-grid connectivity ------------------------------------
#
# Each grid cell (r, c) with corners TL=(r,c) TR=(r,c+1) BL=(r+1,c)
# BR=(r+1,c+1) is split by the diagonal TR-BL into two triangles:
#   {TL, TR, BL} and {TR, BL, BR}.
# This gives every interior vertex a hexagonal link, the standard
# triangulation used for discrete Morse theory on grid data.

def _triangles(ny: int, nx: int):
    for r in range(ny - 1):
        for c in range(nx - 1):
            tl, tr, bl, br = (r, c), (r, c + 1), (r + 1, c), (r + 1, c + 1)
            yield (tl, tr, bl)
            yield (tr, bl, br)


def _build_links(ny: int, nx: int) -> dict[tuple[int, int], dict[tuple[int, int], set]]:
    """For every vertex v, return {neighbor: set(other neighbors sharing a
    triangle edge with `neighbor`, i.e. the link graph of v)}."""
    links: dict[tuple[int, int], dict[tuple[int, int], set]] = {}
    for tri in _triangles(ny, nx):
        for v, a, b in ((tri[0], tri[1], tri[2]), (tri[1], tri[0], tri[2]), (tri[2], tri[0], tri[1])):
            link = links.setdefault(v, {})
            link.setdefault(a, set()).add(b)
            link.setdefault(b, set()).add(a)
    return links


def _order_key(field: ScalarField, v: tuple[int, int]) -> tuple[float, int, int]:
    """Total order on vertices used everywhere below. Breaking ties by grid
    index (simulation of simplicity) guarantees no two vertices compare
    equal, which the PL Morse classification and the persistence sweep both
    require."""
    i, j = v
    return (float(field.values[i, j]), i, j)


def _connected_components(vertices: set, link: dict[tuple[int, int], set]) -> int:
    if not vertices:
        return 0
    unvisited = set(vertices)
    components = 0
    while unvisited:
        components += 1
        stack = [unvisited.pop()]
        while stack:
            v = stack.pop()
            for nb in link.get(v, ()):
                if nb in unvisited:
                    unvisited.discard(nb)
                    stack.append(nb)
    return components


def classify_critical_points(field: ScalarField) -> dict[tuple[int, int], dict]:
    """PL Morse classification of every grid vertex.

    Returns {(i, j): {"type": "minimum"|"maximum"|"saddle"|"regular",
                       "multiplicity": int, "value": float}}
    multiplicity is (components - 1) for saddles, i.e. how many extra
    sheets merge there (>1 for "monkey saddle"-style degeneracies).
    """
    ny, nx = field.shape
    links = _build_links(ny, nx)
    key = lambda v: _order_key(field, v)

    result = {}
    for v, link in links.items():
        below = {u for u in link if key(u) < key(v)}
        above = {u for u in link if key(u) > key(v)}
        n_below = _connected_components(below, link)
        n_above = _connected_components(above, link)

        if n_below == 0:
            ctype, mult = "minimum", 0
        elif n_above == 0:
            ctype, mult = "maximum", 0
        elif n_below == 1 and n_above == 1:
            ctype, mult = "regular", 0
        else:
            ctype, mult = "saddle", max(n_below, n_above) - 1

        i, j = v
        result[v] = {"type": ctype, "multiplicity": mult, "value": float(field.values[i, j])}
    return result


# -- persistence via union-find sweep -----------------------------------

class _UnionFind:
    def __init__(self):
        self.parent: dict = {}
        self.birth: dict = {}  # component root -> vertex where it was born (extremum)

    def make(self, v, birth_value):
        self.parent[v] = v
        self.birth[v] = birth_value

    def find(self, v):
        root = v
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[v] != root:
            self.parent[v], v = root, self.parent[v]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
        return ra


def _persistence_sweep(field: ScalarField, links, descending: bool) -> dict:
    """One 0-dimensional persistence sweep. descending=False sweeps sublevel
    sets (pairs minima with merge saddles); descending=True sweeps
    superlevel sets (pairs maxima with split saddles, by sweeping in
    decreasing order)."""
    ny, nx = field.shape
    vertices = [(i, j) for i in range(ny) for j in range(nx)]
    vertices.sort(key=lambda v: _order_key(field, v), reverse=descending)

    uf = _UnionFind()
    active: set = set()
    pairs = []  # (extremum_vertex, saddle_vertex, persistence)

    for v in vertices:
        uf.make(v, v)
        active.add(v)
        neighbor_roots = {}
        for nb in links.get(v, {}):
            if nb in active:
                neighbor_roots.setdefault(uf.find(nb), nb)

        if not neighbor_roots:
            continue  # v is a local extremum of this sweep direction, starts a new component

        # Merge v into the oldest (most extreme-valued) neighboring component,
        # pairing off every other component's extremum with v as the saddle.
        def extreme_value(root):
            i, j = uf.birth[root]
            val = float(field.values[i, j])
            return -val if descending else val

        roots = sorted(neighbor_roots, key=extreme_value)
        survivor = roots[0]
        uf.union(survivor, v)
        for root in roots[1:]:
            born_at = uf.birth[root]
            i0, j0 = born_at
            iv, jv = v
            persistence = abs(float(field.values[iv, jv]) - float(field.values[i0, j0]))
            pairs.append((born_at, v, persistence))
            uf.union(survivor, root)
        # v itself also joins the survivor component
        uf.union(uf.find(v), survivor)

    # whatever component(s) remain unpaired persist to infinity (the global
    # extremum of the field, generically only one).
    roots_seen = {uf.find(v) for v in vertices}
    infinite = [uf.birth[r] for r in roots_seen]
    return {"pairs": pairs, "infinite": infinite}


def compute_persistence(field: ScalarField) -> dict:
    """Returns minima-saddle pairs (sublevel sweep) and maxima-saddle pairs
    (superlevel sweep), each persistence-annotated."""
    ny, nx = field.shape
    links = _build_links(ny, nx)
    sub = _persistence_sweep(field, links, descending=False)
    sup = _persistence_sweep(field, links, descending=True)
    return {
        "minima_saddle_pairs": [
            {"minimum": m, "saddle": s, "persistence": p} for m, s, p in sub["pairs"]
        ],
        "maxima_saddle_pairs": [
            {"maximum": m, "saddle": s, "persistence": p} for m, s, p in sup["pairs"]
        ],
        "global_minimum": min(sub["infinite"], key=lambda v: _order_key(field, v)),
        "global_maximum": max(sup["infinite"], key=lambda v: _order_key(field, v)),
    }


# -- watershed segmentation ----------------------------------------------

def watershed_basins(
    field: ScalarField, links=None, ascending: bool = False
) -> dict[tuple[int, int], tuple[int, int]]:
    """Assigns every vertex to the local extremum it reaches by steepest
    descent (ascending=False, the standard watershed: descending manifolds
    of the minima) or steepest ascent (ascending=True: ascending manifolds
    of the maxima -- the dual "peak catchment" decomposition). Together the
    two form the 2-cells of a Morse-Smale complex; the saddles are exactly
    where descending and ascending manifold boundaries meet.

    Returns {vertex: extremum_vertex}."""
    ny, nx = field.shape
    if links is None:
        links = _build_links(ny, nx)
    sink: dict = {}

    def better(a, b):
        # is `a` further downhill (ascending=False) / uphill (ascending=True) than `b`?
        return _order_key(field, a) > _order_key(field, b) if ascending else _order_key(field, a) < _order_key(field, b)

    def resolve(v):
        path = []
        cur = v
        while cur not in sink:
            path.append(cur)
            neighbors = list(links.get(cur, {}))
            candidates = [nb for nb in neighbors if better(nb, cur)]
            if not candidates:
                sink[cur] = cur  # local extremum
                break
            steepest = max(candidates, key=lambda nb: _order_key(field, nb)) if ascending \
                else min(candidates, key=lambda nb: _order_key(field, nb))
            cur = steepest
        target = sink[cur]
        for p in path:
            sink[p] = target
        return target

    return {(i, j): resolve((i, j)) for i in range(ny) for j in range(nx)}


# -- Euler characteristic validation --------------------------------------

def euler_characteristic_summary(field: ScalarField, critical_points: dict[tuple[int, int], dict]) -> dict:
    """Reports the domain's Euler characteristic and cross-checks the
    critical point classification against it.

    domain_euler_characteristic is computed directly from the mesh's exact
    vertex/edge/face counts -- independent of the field's values -- and is
    always 1 for a rectangular grid patch, since it is topologically a
    disk. This is the domain's actual Euler characteristic.

    The classic Poincare-Hopf relation (#minima - #saddles + #maxima = chi)
    additionally relates the *critical point counts* to that same
    invariant, but only holds exactly when every vertex has a closed
    (cyclic) link; boundary-row/column vertices here have an open
    (path-shaped) link by construction (there is no triangle beyond the
    domain edge), so their contribution to the alternating sum does not
    exactly match the closed-manifold convention. Both the full count and
    an interior-only count (excluding all boundary vertices, where the
    relation's assumptions hold) are reported so this boundary effect is
    visible rather than silently hidden."""
    ny, nx = field.shape
    v_count = ny * nx
    f_count = 2 * (ny - 1) * (nx - 1)
    e_count = (ny * (nx - 1)) + (nx * (ny - 1)) + ((ny - 1) * (nx - 1))
    domain_euler_characteristic = v_count - e_count + f_count

    def is_boundary(v):
        i, j = v
        return i in (0, ny - 1) or j in (0, nx - 1)

    def counts(points):
        n_min = sum(1 for info in points if info["type"] == "minimum")
        n_max = sum(1 for info in points if info["type"] == "maximum")
        n_saddle = sum(1 + info["multiplicity"] for info in points if info["type"] == "saddle")
        return {
            "num_minima": n_min, "num_saddles": n_saddle, "num_maxima": n_max,
            "alternating_sum": n_min - n_saddle + n_max,
        }

    all_points = list(critical_points.values())
    interior_points = [info for v, info in critical_points.items() if not is_boundary(v)]

    return {
        "domain_euler_characteristic": domain_euler_characteristic,
        "domain_expected": 1,
        "domain_valid": domain_euler_characteristic == 1,
        "all_vertices": counts(all_points),
        "interior_only": counts(interior_points),
    }


# -- top-level entry point ------------------------------------------------

def _merge_insignificant(pairs: list[dict], extremum_key: str, threshold: float, sinks: dict) -> dict:
    """Builds a spill-target map for one direction (minima or maxima) and
    resolves every extremum to its significant "root" by following spill
    chains through the saddle it merges/splits at."""
    spill_target: dict = {}
    for pair in sorted(pairs, key=lambda p: p["persistence"]):
        if pair["persistence"] < threshold:
            spill_target[pair[extremum_key]] = pair["saddle"]

    def significant_root(extremum):
        seen = set()
        cur = extremum
        while cur in spill_target and cur not in seen:
            seen.add(cur)
            saddle = spill_target[cur]
            cur = sinks.get(saddle, saddle)
        return cur

    return significant_root


def analyze_scalar_field(field: ScalarField, persistence_threshold: float | None = None) -> dict:
    """Full discrete-Morse analysis of one scalar field: critical points
    (peaks/pits/saddle-point passes), 0-dimensional persistence pairing,
    an Euler-characteristic self-consistency check, and both halves of the
    Morse-Smale-style 2-cell decomposition (descending "basins" from every
    pit, ascending "peak_basins" from every peak) via steepest-descent /
    ascent -- no heuristic depression-filling or breaching preprocessing is
    used anywhere in this pipeline; noise is instead removed after the fact
    by folding low-persistence critical-point pairs into their neighbor.

    persistence_threshold: if given, basins/peaks whose persistence is
    below this value are folded into their spill partner, i.e. reported
    as topological noise rather than genuine features. If omitted, a
    threshold of 5% of the field's value range is used.
    """
    critical_points = classify_critical_points(field)
    persistence = compute_persistence(field)
    euler = euler_characteristic_summary(field, critical_points)

    value_range = float(field.values.max() - field.values.min())
    threshold = persistence_threshold if persistence_threshold is not None else 0.05 * value_range

    links = _build_links(*field.shape)
    descent_sinks = watershed_basins(field, links, ascending=False)
    ascent_sinks = watershed_basins(field, links, ascending=True)

    # persistence of each minimum/maximum = persistence of its pairing in the
    # minima-saddle / maxima-saddle lists; the global extremum of each has no
    # pairing (infinite persistence, represented as None)
    min_persistence = {pair["minimum"]: pair["persistence"] for pair in persistence["minima_saddle_pairs"]}
    min_persistence.setdefault(persistence["global_minimum"], None)
    max_persistence = {pair["maximum"]: pair["persistence"] for pair in persistence["maxima_saddle_pairs"]}
    max_persistence.setdefault(persistence["global_maximum"], None)

    # a saddle's persistence/significance is the strongest of any pairing it
    # takes part in (a merge event in the sub-level sweep and/or a split
    # event in the super-level sweep) -- if it is responsible for even one
    # significant feature, the saddle itself is a real water gap, not noise.
    # A small number of boundary-row/column saddles (open, not cyclic, link)
    # can end up in neither sweep's pairing list; those get persistence=None
    # and, like an unpaired global extremum, default to significant=True
    # (cannot be ruled out as noise, so it is not suppressed).
    saddle_persistence: dict = {}
    for pair in persistence["minima_saddle_pairs"] + persistence["maxima_saddle_pairs"]:
        s, p = pair["saddle"], pair["persistence"]
        if s not in saddle_persistence or p > saddle_persistence[s]:
            saddle_persistence[s] = p

    min_root = _merge_insignificant(persistence["minima_saddle_pairs"], "minimum", threshold, descent_sinks)
    max_root = _merge_insignificant(persistence["maxima_saddle_pairs"], "maximum", threshold, ascent_sinks)

    basin_counts: dict = {}
    for v, m in descent_sinks.items():
        root = min_root(m)
        basin_counts[root] = basin_counts.get(root, 0) + 1

    peak_basin_counts: dict = {}
    for v, m in ascent_sinks.items():
        root = max_root(m)
        peak_basin_counts[root] = peak_basin_counts.get(root, 0) + 1

    def persistence_of(v, kind):
        table = {"minimum": min_persistence, "maximum": max_persistence, "saddle": saddle_persistence}[kind]
        return table.get(v)

    def is_significant(p):
        return p is None or p >= threshold

    def describe(v, kind):
        i, j = v
        x, y = field.coords(i, j)
        p = persistence_of(v, kind)
        return {
            "grid_index": [i, j],
            "coords": [x, y],
            "value": float(field.values[i, j]),
            "persistence": p,
            "significant": is_significant(p),
        }

    critical_summary = {"minimum": [], "maximum": [], "saddle": []}
    for v, info in critical_points.items():
        if info["type"] == "regular":
            continue
        entry = describe(v, info["type"])
        entry["multiplicity"] = info["multiplicity"]
        critical_summary[info["type"]].append(entry)
    for k in critical_summary:
        critical_summary[k].sort(key=lambda e: e.get("persistence") or e["value"], reverse=True)

    basins = [
        {
            "minimum": describe(root, "minimum"),
            "size_cells": count,
            "significant": is_significant(min_persistence.get(root)),
        }
        for root, count in basin_counts.items()
    ]
    basins.sort(key=lambda b: b["size_cells"], reverse=True)

    peak_basins = [
        {
            "maximum": describe(root, "maximum"),
            "size_cells": count,
            "significant": is_significant(max_persistence.get(root)),
        }
        for root, count in peak_basin_counts.items()
    ]
    peak_basins.sort(key=lambda b: b["size_cells"], reverse=True)

    return {
        "field": field.name,
        "grid_shape": list(field.shape),
        "value_range": [float(field.values.min()), float(field.values.max())],
        "persistence_threshold": threshold,
        "critical_points": critical_summary,
        "euler_characteristic": euler,
        "num_significant_basins": sum(1 for b in basins if b["significant"]),
        "num_raw_minima": len(critical_summary["minimum"]),
        "basins": basins,
        "num_significant_peak_basins": sum(1 for b in peak_basins if b["significant"]),
        "num_raw_maxima": len(critical_summary["maximum"]),
        "peak_basins": peak_basins,
    }
