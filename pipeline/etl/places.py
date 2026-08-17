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
    dropped = project(places)
    if dropped:
        logger.info("%d places outside the Albers USA domain, skipped", dropped)
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
