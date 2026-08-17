"""Pure tests for the map's place-label layer.

Nothing here touches the network or mapshaper: what is under test is the
naming and tiering, which is where a wrong answer would be silent. A
mis-stripped name shows a reader "Houston city" forever, and a tiering bug
either buries a state's largest towns or buries the map in labels.
"""

from pipeline.etl.places import Place, assign_tiers, display_name, load_population


def test_type_suffixes_are_stripped() -> None:
    assert display_name("Houston city") == "Houston"
    assert display_name("Abanda CDP") == "Abanda"
    assert display_name("Essex Junction village") == "Essex Junction"
    assert display_name("Anchorage municipality") == "Anchorage"


def test_a_capitalised_type_word_inside_a_name_survives() -> None:
    """Census writes the legal type in lower case, which is the only reason
    stripping is safe. "Carson City city" must keep its City."""
    assert display_name("Carson City city") == "Carson City"
    assert display_name("Oklahoma City city") == "Oklahoma City"


def test_every_state_gets_top_tier_labels_however_small() -> None:
    """A national population floor alone leaves small states blank: Vermont's
    largest place is under 50,000, and a map of Vermont with no labels is the
    failure this rank floor exists to prevent."""
    places = [
        Place(geoid=f"50{i:05d}", state="VT", name=f"Town{i}", lat=44.0, lon=-73.0)
        for i in range(30)
    ]
    assign_tiers(places, {p.geoid: 900 - i for i, p in enumerate(places)})
    assert sum(1 for p in places if p.tier == 1) == 12
    # and the twelve chosen are the twelve largest, not an arbitrary slice
    top = sorted(places, key=lambda p: p.pop, reverse=True)[:12]
    assert all(p.tier == 1 for p in top)


def test_a_crowded_state_is_still_bounded_at_the_top_tier() -> None:
    """The opposite failure: at a 50,000 population floor California drew 178
    labels at state view. Rank caps what any one state can put on screen."""
    places = [
        Place(geoid=f"06{i:05d}", state="CA", name=f"City{i}", lat=34.0, lon=-118.0)
        for i in range(400)
    ]
    assign_tiers(places, {p.geoid: 600_000 - i for i, p in enumerate(places)})
    # Bounded by the hard rank ceiling, not by the population distribution
    # happening to be kind. Without the ceiling this returns all 400.
    assert sum(1 for p in places if p.tier == 1) == 25


def test_population_lets_a_big_city_outrank_its_queue_position() -> None:
    """Rank alone would hide a genuinely large city sitting 13th in a crowded
    state, so the population floor promotes it a tier."""
    places = [
        Place(geoid=f"48{i:05d}", state="TX", name=f"P{i}", lat=30.0, lon=-97.0)
        for i in range(60)
    ]
    pops = {p.geoid: 60_000 for p in places}
    assign_tiers(places, pops)
    ranked_out = [p for p in places if p.rank > 12]
    assert ranked_out, "fixture must have places past the tier-1 rank cap"
    # 60,000 clears the tier-2 floor, so none of them fall past tier 2.
    assert all(p.tier <= 2 for p in ranked_out)


def test_places_with_no_population_sink_to_the_deepest_tier() -> None:
    """Unincorporated CDPs carry no municipal population. They are kept, at
    the tier that only appears on deep zoom, because "the small towns" is what
    a reader zooming in is looking for."""
    places = [
        Place(geoid=f"01{i:05d}", state="AL", name=f"CDP{i}", lat=32.0, lon=-86.0)
        for i in range(200)
    ]
    assign_tiers(places, {})
    assert all(p.tier == 4 for p in places if p.rank > 150)


def test_population_takes_the_maximum_across_place_parts() -> None:
    """A place split across county lines appears once per part; summing would
    double count and taking the first would undercount."""
    csv_text = (
        "SUMLEV,STATE,COUNTY,PLACE,COUSUB,CONCIT,PRIMGEO_FLAG,FUNCSTAT,NAME,"
        "STNAME,ESTIMATESBASE2020,POPESTIMATE2024\n"
        "162,48,000,35000,00000,00000,0,A,Houston city,Texas,2300000,2300000\n"
        "157,48,201,35000,00000,00000,0,A,Houston city (part),Texas,2000000,2000000\n"
        "040,48,000,00000,00000,00000,0,A,Texas,Texas,29000000,31000000\n"
    )
    pops = load_population(csv_text.encode("utf-8"))
    assert pops["4835000"] == 2_300_000
    # the state-level row is not a place and must not leak in
    assert "4800000" not in pops
