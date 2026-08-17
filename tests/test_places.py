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


def test_population_reads_place_rows_and_ignores_other_geographies() -> None:
    """The ACS file carries every geography level in one stream. Only place
    rows (1600000US) are population for a place; a state row leaking in would
    hand one town its whole state's population and pin it to tier 1."""
    data = (
        "GEO_ID|B01003_E001|B01003_M001\n"
        "1600000US4835000|2300000|500\n"       # Houston
        "0400000US48|31000000|0\n"             # Texas, a state
        "1600000US1571550|344967|900\n"        # Urban Honolulu, a CDP
        "1600000US0100124|-555555555|0\n"      # suppressed cell
    )
    pops = load_population(data.encode("utf-8"))
    assert pops["4835000"] == 2_300_000
    assert pops["1571550"] == 344_967, "CDPs must carry population, unlike PEP"
    assert "48" not in pops
    assert "0100124" not in pops, "suppressed negatives are not populations"


def test_land_area_breaks_ties_so_ordering_is_never_alphabetical() -> None:
    """The Hawaii failure: every place there is a CDP, the old source gave
    them all 0, and the sort fell through to file order, which is
    alphabetical. Tier 1 read Ahuimanu, Aiea, Ainaloa."""
    places = [
        Place(geoid=f"15{i:05d}", state="HI", name=n, lat=21.0, lon=-157.0, aland=area)
        for i, (n, area) in enumerate([("Ahuimanu", 10), ("Zzz Big", 9_000), ("Aiea", 20)])
    ]
    assign_tiers(places, {})
    assert places[1].rank == 1, "largest by area ranks first, not the alphabet"
