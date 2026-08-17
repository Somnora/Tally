"""Build the city and township label layer for the district map.

WHY THIS EXISTS. A congressional district map is unreadable without landmarks:
the shapes are drawn by equal population, so they follow no boundary a reader
recognises, and a state full of them looks like abstract art until a familiar
town name anchors it. These labels are that anchor, and nothing more.

WHAT THEY ARE NOT. A label does NOT tell a reader their district, and the app
must never imply it does. Measured against this repo's own district geometry,
five points across Houston fall in five different districts (TX-18, TX-38,
TX-22, TX-36, TX-02); Phoenix spans at least two. A city label attached to one
district would therefore be wrong for most residents of every large city. The
labels orient; the address lookup answers.

SOURCES, both public Census bulk files needing no API key:

  places      https://www2.census.gov/geo/docs/maps-data/data/gazetteer/
              2024_Gazetteer/2024_Gaz_place_national.zip
              32,333 places with name, state and an interior point.
  population  https://www2.census.gov/programs-surveys/popest/datasets/
              2020-2024/cities/totals/sub-est2024.csv
              Joins to the Gazetteer on GEOID (state FIPS + place FIPS).

Population covers 19,479 of the 32,333: the remainder are Census Designated
Places, which are unincorporated communities and so have no municipal
population of their own. They are kept, at the deepest tier, because "the
small towns" is precisely what a reader zooming in is looking for.

PROJECTION. Coordinates must land in the same space as data/geo/us_cd119.topo
.json or the labels sit in the wrong place. That file is Census cb_2024 cd119
projected to Albers USA by mapshaper, which outputs metres, so this runs the
SAME projection over a point layer:

    mapshaper <points>.json -proj albersusa -o format=geojson

Verified rather than assumed: projecting Washington DC, Los Angeles,
Anchorage, Honolulu and Houston through this path and testing each against the
district polygons in us_cd119.topo.json puts every one inside its correct
district, Alaska and Hawaii included via their conventional insets.

Run:  uv run python -m pipeline.etl.places --dry-run
      uv run python -m pipeline.etl.places
"""

import argparse
import csv
import hashlib
import io
import json
import logging
import math
import subprocess
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2024_Gazetteer/2024_Gaz_place_national.zip"
)
# ACS rather than the Population Estimates programme, which was the first
# choice and was wrong. PEP covers incorporated places and essentially no
# Census Designated Places, so in Hawaii, where nearly every place is a CDP,
# every population came back 0, the sort fell through to input order, and the
# state's top tier rendered alphabetically: Ahuimanu, Aiea, Ainaloa, with
# Honolulu and Hilo buried. ACS covers 99.8% of the Gazetteer.
POPULATION_URL = (
    "https://www2.census.gov/programs-surveys/acs/summary_file/2023/"
    "table-based-SF/data/5YRData/acsdt5y2023-b01003.dat"
)
# ACS keys places as 1600000US<GEOID>; the Gazetteer carries the bare GEOID.
ACS_PLACE_PREFIX = "1600000US"
OUT_PATH = Path("data/geo/us_places.json")
DISTRICTS_PATH = Path("data/geo/us_cd119.topo.json")

# How far a label may be moved onto the drawn coastline before we give up and
# drop it instead. Census interior points are guaranteed inside the place
# INCLUDING its water, so a coastal city's point can sit offshore: San
# Francisco's is 31km out to sea, dragged there by the Farallon Islands, and
# Portland, Newport and Bayonne are all in open water. Those belong on the
# coast beside them. What does not is a community whose land this map never
# draws: St. Paul and St. George in the Pribilofs are 125km and 135km from
# anything, and snapping them would plant Alaskan island villages on the
# mainland. Moving a label is a smaller lie than that, but only up to a point,
# and this is the point.
# A label may be moved about as far as the place itself is wide, because that
# is roughly how far its own boundary could plausibly reach toward the drawn
# coast. San Francisco spans ~11km and its interior point sits 31km out to sea
# (the city limits include the Farallon Islands), so it earns the move onto
# its own peninsula. Duck Key spans a few hundred metres, so a 40km snap would
# put it on somebody else's island; it gets dropped instead. Bounded at both
# ends so the rule can neither strand a hamlet nor teleport a metropolis.
SNAP_SPAN_MULTIPLE = 3.0
SNAP_MIN_METRES = 2_000
SNAP_MAX_METRES = 40_000
NUDGE_METRES = 1_000

# Rings smaller than this across are ignored as snap targets. Without it San
# Francisco, whose interior point sits 31km out to sea because the city limits
# reach the Farallon Islands, snaps onto a Farallon islet a kilometre wide and
# the inland nudge carries it straight back off the far side. A city label
# belongs on the coastline a reader can see, not on a rock.
MIN_SNAP_RING_METRES = 3_000

# Where to look for solid ground once the coastline is found: rings of
# candidate points around the landfall, nearest first, so a label settles just
# inland rather than as far in as the search will reach.
SEARCH_RADII_METRES = (400, 900, 1_800, 3_500, 7_000)
SEARCH_DIRECTIONS = 16

# FIPS -> USPS, to match district geometry (keyed by STATEFP) against places
# (keyed by USPS). Mirrors the same table in web/map.js.
STATE_BY_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY",
}

USER_AGENT = "tally-civic-transparency/0.1 (nonpartisan transparency project; local ingestion)"

# Census writes the legal type into NAME ("Houston city", "Abanda CDP"). The
# suffix is lower case for every type except CDP, which is what makes stripping
# safe: "Carson City city" loses only the trailing lower-case word.
TYPE_SUFFIXES = (
    " city", " town", " village", " borough", " municipality", " township",
    " CDP", " comunidad", " zona urbana", " urbana", " plantation", " gore",
    " grant", " location", " purchase", " reservation",
)

# Tier 1 is drawn at state view, 4 only at deep zoom. Tiering is driven by
# RANK WITHIN THE STATE first and population second, because both pure forms
# fail at one end. A national population threshold leaves small states blank
# (Vermont's largest place is under 50,000) and floods big ones: at a 50,000
# floor California alone drew 178 labels at state view, which is not a map, it
# is a wall of text. Rank bounds what any one state can draw; the population
# floor then lets a genuinely large city appear a tier earlier than its rank
# would allow in a state crowded with them.
#
# Fields: (tier, population floor, guaranteed rank, hard rank ceiling).
# A place takes the tier if it is among its state's top `guaranteed` by
# population, OR clears the population floor while still inside the hard
# ceiling. The ceiling is what makes the density bound structural rather than
# incidental: with only "floor OR rank" a state where many cities clear the
# floor is unbounded, and it is only the real population distribution that
# keeps that from exploding. A test pins the bound rather than trusting the
# distribution to keep holding.
TIERS = (
    (1, 250_000, 12, 25),
    (2, 50_000, 40, 80),
    (3, 10_000, 150, 300),
)


@dataclass
class Place:
    geoid: str
    state: str
    name: str
    lat: float
    lon: float
    pop: int = 0
    aland: int = 0
    rank: int = 0
    tier: int = 4
    xy: tuple[float, float] | None = field(default=None)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return bytes(response.read())


def load_population(raw: bytes) -> dict[str, int]:
    """GEOID -> population, from the ACS table-based summary file.

    Pipe-delimited GEO_ID|estimate|margin. Only place-level rows are kept;
    the file also carries nation, state and dozens of other geography levels.
    """
    text = raw.decode("utf-8", errors="replace")
    population: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        geo = (row.get("GEO_ID") or "").strip()
        if not geo.startswith(ACS_PLACE_PREFIX):
            continue
        try:
            count = int(row.get("B01003_E001") or 0)
        except ValueError:
            continue
        # Suppressed or unavailable cells come through as large negatives.
        if count < 0:
            continue
        population[geo[len(ACS_PLACE_PREFIX):]] = count
    return population


def display_name(raw_name: str) -> str:
    name = raw_name.strip()
    for suffix in TYPE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def load_places(raw_zip: bytes) -> list[Place]:
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        member = next(n for n in archive.namelist() if n.endswith(".txt"))
        # UTF-8, verified: reading this as latin-1 turns "La Cañada
        # Flintridge" into "La CaÃ±ada Flintridge" on the public map.
        text = archive.read(member).decode("utf-8", errors="replace")
    places: list[Place] = []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        clean = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        try:
            lat = float(clean["INTPTLAT"])
            lon = float(clean["INTPTLONG"])
        except (KeyError, ValueError):
            continue
        try:
            aland = int(clean.get("ALAND") or 0)
        except ValueError:
            aland = 0
        places.append(Place(
            geoid=clean["GEOID"], state=clean["USPS"],
            name=display_name(clean["NAME"]), lat=lat, lon=lon, aland=aland,
        ))
    return places


def assign_tiers(places: list[Place], population: dict[str, int]) -> None:
    """Tier each place by population, floored by its rank within its state."""
    for place in places:
        place.pop = population.get(place.geoid, 0)

    by_state: dict[str, list[Place]] = defaultdict(list)
    for place in places:
        by_state[place.state].append(place)
    for group in by_state.values():
        # Land area breaks ties so that a state whose places all lack a
        # population figure still ranks by something meaningful rather than
        # falling through to file order, which is alphabetical.
        group.sort(key=lambda p: (p.pop, p.aland), reverse=True)
        for rank, place in enumerate(group, start=1):
            place.rank = rank

    for place in places:
        place.tier = 4
        for level, floor, guaranteed, ceiling in TIERS:
            if place.rank <= guaranteed or (place.pop >= floor and place.rank <= ceiling):
                place.tier = level
                break


def project(places: list[Place]) -> int:
    """Albers USA metres, via the same mapshaper projection as the districts.

    Doing this here rather than in the browser is what guarantees alignment:
    the district geometry was projected by this exact named projection, and a
    hand-rolled reimplementation would drift, especially across the Alaska and
    Hawaii insets which are separate sub-projections.
    """
    features = [
        {
            "type": "Feature",
            "properties": {"i": index},
            "geometry": {"type": "Point", "coordinates": [place.lon, place.lat]},
        }
        for index, place in enumerate(places)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "points.json"
        target = Path(tmp) / "projected.json"
        source.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}),
            encoding="utf-8",
        )
        result = subprocess.run(
            ["npx", "-y", "mapshaper@latest", str(source),
             "-proj", "albersusa", "-o", "format=geojson", "precision=1", str(target)],
            capture_output=True, text=True, timeout=900, check=False,
        )
        if not target.exists():
            raise RuntimeError(f"mapshaper projection failed: {result.stderr[-400:]}")
        raw = cast(dict[str, Any], json.loads(target.read_text(encoding="utf-8")))

    for item in cast(list[Any], raw.get("features") or []):
        feature = cast(dict[str, Any], item)
        props = cast(dict[str, Any], feature.get("properties") or {})
        geometry = cast(dict[str, Any], feature.get("geometry") or {})
        if geometry.get("type") != "Point":
            continue
        try:
            index = int(props.get("i", -1))
        except (TypeError, ValueError):
            continue
        coords = cast(list[Any], geometry.get("coordinates") or [])
        if index < 0 or index >= len(places) or len(coords) != 2:
            continue
        places[index].xy = (float(coords[0]), float(coords[1]))
    # A point outside the projection's domain (a territory the Albers USA
    # composite does not cover) comes back with no coordinates rather than
    # silently at 0,0, which would plant a label in the middle of Kansas.
    return sum(1 for p in places if p.xy is None)


@dataclass(frozen=True)
class BuildResult:
    places: int
    states: int
    bytes_written: int


def _state_rings() -> dict[str, list[list[tuple[float, float]]]]:
    """Decoded district outlines per state, in projected metres.

    Deliberately the SAME file the map draws. A label's job is to sit on the
    coastline a reader can see, not the true one: this geometry is simplified
    to 3%, so snapping to anything else would put labels off the drawn edge.
    """
    topo = cast(dict[str, Any], json.loads(DISTRICTS_PATH.read_text(encoding="utf-8")))
    scale = cast(list[float], topo["transform"]["scale"])
    translate = cast(list[float], topo["transform"]["translate"])
    arcs: list[list[tuple[float, float]]] = []
    for arc in cast(list[Any], topo["arcs"]):
        x = y = 0.0
        points: list[tuple[float, float]] = []
        for dx, dy in cast(list[Any], arc):
            x += dx
            y += dy
            points.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        arcs.append(points)

    def arc_points(index: int) -> list[tuple[float, float]]:
        return list(reversed(arcs[~index])) if index < 0 else arcs[index]

    out: dict[str, list[list[tuple[float, float]]]] = defaultdict(list)
    for geometry in cast(list[Any], topo["objects"]["districts"]["geometries"]):
        geom = cast(dict[str, Any], geometry)
        props = cast(dict[str, Any], geom.get("properties") or {})
        state = STATE_BY_FIPS.get(str(props.get("STATEFP") or ""))
        if not state:
            continue
        polygons = [geom["arcs"]] if geom["type"] == "Polygon" else geom["arcs"]
        for polygon in cast(list[Any], polygons):
            for ring in cast(list[Any], polygon):
                points: list[tuple[float, float]] = []
                for index in cast(list[int], ring):
                    points.extend(arc_points(index))
                if len(points) >= 3:
                    out[state].append(points)
    return out


def _inside(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    hit = False
    count = len(ring)
    for i in range(count):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % count]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1):
            hit = not hit
    return hit


def _ring_span(ring: list[tuple[float, float]]) -> float:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _nearest_on_rings(
    point: tuple[float, float], rings: list[list[tuple[float, float]]]
) -> tuple[float, tuple[float, float], list[tuple[float, float]]] | None:
    px, py = point
    best_distance = float("inf")
    best_point: tuple[float, float] | None = None
    best_ring: list[tuple[float, float]] | None = None
    for ring in rings:
        if _ring_span(ring) < MIN_SNAP_RING_METRES:
            continue
        count = len(ring)
        for i in range(count):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % count]
            dx, dy = x2 - x1, y2 - y1
            length = dx * dx + dy * dy
            if length == 0:
                t = 0.0
            else:
                t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length))
            qx, qy = x1 + t * dx, y1 + t * dy
            distance = math.hypot(px - qx, py - qy)
            if distance < best_distance:
                best_distance, best_point, best_ring = distance, (qx, qy), ring
    if best_point is None or best_ring is None:
        return None
    return (best_distance, best_point, best_ring)


def anchor_to_land(places: list[Place]) -> tuple[int, int]:
    """Move offshore labels onto the drawn coast; drop the ones too far out.

    Returns (snapped, dropped). A dropped place keeps xy = None and is left
    out of the payload, because a label floating in open ocean is worse than
    no label, and a label teleported 135km inland is worse than both.

    The placement SEARCHES rather than reasons. Two earlier versions picked a
    direction and stepped that way: outward along the line the place came in
    on, then inward toward the ring's centroid. Both are only reliable on a
    straight coast, and a coast is the one thing that is never straight, so
    each fixed a few labels and stranded others. Sampling a ring of candidate
    points around the landfall and keeping the first that is genuinely inside
    a polygon tests the property we actually want instead of predicting it.
    """
    rings_by_state = _state_rings()
    # Bounding boxes make the search affordable: most candidates can be
    # rejected against a rectangle instead of walking a thousand vertices.
    boxed: dict[str, list[tuple[list[tuple[float, float]], float, float, float, float]]] = {}
    for state, rings in rings_by_state.items():
        entries: list[tuple[list[tuple[float, float]], float, float, float, float]] = []
        for ring in rings:
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            entries.append((ring, min(xs), min(ys), max(xs), max(ys)))
        boxed[state] = entries

    def on_land(point: tuple[float, float], state: str) -> bool:
        x, y = point
        for ring, x0, y0, x1, y1 in boxed.get(state, ()):
            if x0 <= x <= x1 and y0 <= y <= y1 and _inside(point, ring):
                return True
        return False

    snapped = dropped = 0
    for place in places:
        if place.xy is None or place.state not in boxed:
            continue
        if on_land(place.xy, place.state):
            continue
        # sqrt(land area) approximates the place's width; a 121 km2 city is
        # ~11km across, a small key a few hundred metres.
        allowed = min(SNAP_MAX_METRES,
                      max(SNAP_MIN_METRES, math.sqrt(place.aland) * SNAP_SPAN_MULTIPLE))
        found = _nearest_on_rings(place.xy, rings_by_state[place.state])
        if found is None or found[0] > allowed:
            place.xy = None
            dropped += 1
            continue
        _, (qx, qy), _ring = found
        placed: tuple[float, float] | None = None
        for radius in SEARCH_RADII_METRES:
            for step in range(SEARCH_DIRECTIONS):
                angle = 2 * math.pi * step / SEARCH_DIRECTIONS
                candidate = (qx + radius * math.cos(angle), qy + radius * math.sin(angle))
                if on_land(candidate, place.state):
                    placed = candidate
                    break
            if placed is not None:
                break
        # Falling back to the landfall itself is still right: it is ON the
        # drawn coastline, which is where a coastal name belongs, even when
        # the polygon test cannot decide a point sitting exactly on its edge.
        place.xy = placed or (qx, qy)
        snapped += 1
    return snapped, dropped


def build(out_path: Path = OUT_PATH) -> BuildResult:
    logger.info("fetching Census Gazetteer places")
    gazetteer_raw = fetch(GAZETTEER_URL)
    logger.info("fetching Census sub-county population estimates")
    population_raw = fetch(POPULATION_URL)

    places = load_places(gazetteer_raw)
    population = load_population(population_raw)
    assign_tiers(places, population)
    logger.info("%d places, %d with population", len(places), len(population))

    logger.info("projecting to Albers USA via mapshaper")
    outside = project(places)
    if outside:
        logger.info("%d places outside the Albers USA domain, skipped", outside)
    snapped, dropped = anchor_to_land(places)
    logger.info("%d labels snapped onto the drawn coast, %d dropped as too far "
                "from any drawn land", snapped, dropped)
    kept = [p for p in places if p.xy is not None]

    # Column-wise and grouped by state: the map only ever needs one state at a
    # time, and columns drop the repeated key names that would otherwise cost
    # more than the values.
    names: dict[str, list[str]] = defaultdict(list)
    xs: dict[str, list[int]] = defaultdict(list)
    ys: dict[str, list[int]] = defaultdict(list)
    tiers: dict[str, list[int]] = defaultdict(list)
    for place in kept:
        if place.xy is None:
            continue
        names[place.state].append(place.name)
        xs[place.state].append(round(place.xy[0]))
        ys[place.state].append(round(place.xy[1]))
        tiers[place.state].append(place.tier)
    by_state = {
        state: {"n": names[state], "x": xs[state], "y": ys[state], "t": tiers[state]}
        for state in sorted(names)
    }

    payload = {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": [
            {"url": GAZETTEER_URL, "sha256": hashlib.sha256(gazetteer_raw).hexdigest()},
            {"url": POPULATION_URL, "sha256": hashlib.sha256(population_raw).hexdigest()},
        ],
        "projection": "albersusa (mapshaper), matching data/geo/us_cd119.topo.json",
        "tiers": {"1": "state view", "2": "zoomed", "3": "closer", "4": "deep zoom"},
        "states": by_state,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return BuildResult(places=len(kept), states=len(by_state),
                       bytes_written=out_path.stat().st_size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the map's place-label layer")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--dry-run", action="store_true",
                        help="report tier counts and fetch nothing but the sources")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.dry_run:
        places = load_places(fetch(GAZETTEER_URL))
        assign_tiers(places, load_population(fetch(POPULATION_URL)))
        counts: dict[int, int] = defaultdict(int)
        for place in places:
            counts[place.tier] += 1
        print(f"{len(places)} places")
        for tier in sorted(counts):
            print(f"  tier {tier}: {counts[tier]:>6}")
        return

    result = build(args.out)
    print(f"{args.out}  {result.bytes_written / 1024:.0f} KB  "
          f"{result.places} places across {result.states} states")


if __name__ == "__main__":
    main()
