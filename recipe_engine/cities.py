"""
cities.py — the 13 anchor cities + county attribution ground truth.

PROVENANCE NOTE (2026-06-12, T3): the CODE PACK said "extract the REAL
13-anchor-city list from ~/Documents/landos-engine docs" — no such list
exists anywhere in that repo (verified by full-text sweep; 'Dowagiac'
appears nowhere). This list is RECONSTRUCTED from verified ground truth:
  - operating geography = Berrien MI (26021) + St. Joseph IN (18141),
    ZIP/place rosters copied from landos-engine app/score_absentee.py
    COUNTY_GEO (the canonical county-membership reference)
  - the three report-only cities the CODE PACK names explicitly
    (Dowagiac / Edwardsburg -> cass-mi, Elkhart -> elkhart-in)
Report-only cities are scraped but NEVER pushed to the engine or
territory — territory expansion is an operator decision, never a side
effect.

County attribution happens per-EVENT (from the SaleEvent's own address),
never per-anchor: estatesales.net city pages are market pages that list
sales from a wide region (the Benton Harbor anchor returns sales from
Hammond IN and Elmhurst IL).
"""

# anchor slug -> estatesales.net path. 13 anchors.
ANCHOR_CITIES = {
    # Berrien County, MI — in-territory (berrien-mi)
    "benton-harbor":   "MI/Benton-Harbor",
    "saint-joseph-mi": "MI/Saint-Joseph",
    "niles":           "MI/Niles",
    "buchanan":        "MI/Buchanan",
    "berrien-springs": "MI/Berrien-Springs",
    "coloma":          "MI/Coloma",
    "new-buffalo":     "MI/New-Buffalo",
    # St. Joseph County, IN — in-territory (st-joseph-in)
    "south-bend":      "IN/South-Bend",
    "mishawaka":       "IN/Mishawaka",
    "granger":         "IN/Granger",
    # REPORT-ONLY — outside territory, scraped for the expansion queue
    "dowagiac":        "MI/Dowagiac",
    "edwardsburg":     "MI/Edwardsburg",
    "elkhart":         "IN/Elkhart",
}

# County membership (from landos-engine COUNTY_GEO — do not edit by hand;
# re-sync from score_absentee.py if that reference changes).
BERRIEN_MI_ZIPS = {
    "49022", "49023", "49038", "49039", "49085", "49098", "49101", "49102",
    "49103", "49104", "49106", "49107", "49111", "49115", "49116", "49117",
    "49119", "49120", "49125", "49126", "49127", "49128", "49129", "49130",
}
BERRIEN_MI_PLACES = {
    "BENTON HARBOR", "BENTON", "ST JOSEPH", "SAINT JOSEPH", "COLOMA",
    "WATERVLIET", "BARODA", "BERRIEN CENTER", "BERRIEN SPRINGS", "BRIDGMAN",
    "BUCHANAN", "EAU CLAIRE", "HARBERT", "LAKESIDE", "NEW BUFFALO",
    "NEW TROY", "NILES", "SAWYER", "SODUS", "STEVENSVILLE", "THREE OAKS",
    "UNION PIER", "GALIEN", "SHOREHAM", "LINCOLN TOWNSHIP",
}
ST_JOSEPH_IN_ZIPS = {
    "46601", "46613", "46614", "46615", "46616", "46617", "46619", "46628",
    "46635", "46637", "46544", "46545", "46546", "46530", "46536", "46552",
    "46554", "46556", "46561", "46574",
}
ST_JOSEPH_IN_PLACES = {
    "SOUTH BEND", "MISHAWAKA", "GRANGER", "WALKERTON", "NORTH LIBERTY",
    "NEW CARLISLE", "LAKEVILLE", "OSCEOLA", "NOTRE DAME", "ROSELAND",
    "INDIAN VILLAGE",
}

# Approximate city-center coordinates — an explicit stopgap, every row using
# one is flagged geo_precision='centroid'. Exact geocoding is the W2 pass.
CITY_CENTROIDS = {
    ("MI", "BENTON HARBOR"):   (42.1167, -86.4542),
    ("MI", "ST JOSEPH"):       (42.1098, -86.4801),
    ("MI", "SAINT JOSEPH"):    (42.1098, -86.4801),
    ("MI", "NILES"):           (41.8298, -86.2542),
    ("MI", "BUCHANAN"):        (41.8273, -86.3611),
    ("MI", "BERRIEN SPRINGS"): (41.9464, -86.3389),
    ("MI", "COLOMA"):          (42.1861, -86.3083),
    ("MI", "NEW BUFFALO"):     (41.7937, -86.7439),
    ("MI", "STEVENSVILLE"):    (42.0145, -86.5195),
    ("MI", "BRIDGMAN"):        (41.9431, -86.5564),
    ("MI", "THREE OAKS"):      (41.7986, -86.6108),
    ("MI", "WATERVLIET"):      (42.1867, -86.2611),
    ("MI", "EAU CLAIRE"):      (41.9831, -86.3042),
    ("MI", "GALIEN"):          (41.8003, -86.5008),
    ("MI", "BARODA"):          (41.9534, -86.4853),
    ("MI", "SODUS"):           (42.0395, -86.3792),
    ("IN", "SOUTH BEND"):      (41.6764, -86.2520),
    ("IN", "MISHAWAKA"):       (41.6620, -86.1586),
    ("IN", "GRANGER"):         (41.7536, -86.1108),
    ("IN", "OSCEOLA"):         (41.6650, -86.0758),
    ("IN", "LAKEVILLE"):       (41.5250, -86.2733),
    ("IN", "NEW CARLISLE"):    (41.7003, -86.5092),
    ("IN", "NORTH LIBERTY"):   (41.5342, -86.4264),
    ("IN", "WALKERTON"):       (41.4667, -86.4828),
    ("IN", "NOTRE DAME"):      (41.7002, -86.2379),
}


def attribute_county(state: str, postal_code: str, locality: str):
    """
    Per-event county attribution from the event's OWN address.
    Returns (county_slug, in_territory). ZIP wins over place name (ZIPs are
    unambiguous; localities drift in form). Anything not in the two operating
    counties is report-only, slugged by state/locality for the expansion queue.
    """
    st = (state or "").strip().upper()
    zp = (postal_code or "").strip()[:5]
    loc = (locality or "").strip().upper().replace(".", "")

    if st == "MI" and (zp in BERRIEN_MI_ZIPS or loc in BERRIEN_MI_PLACES):
        return "berrien-mi", True
    if st == "IN" and (zp in ST_JOSEPH_IN_ZIPS or loc in ST_JOSEPH_IN_PLACES):
        return "st-joseph-in", True

    # Report-only: honest slug for the territory-expansion decision queue.
    if loc and st:
        return f"report-only:{loc.lower().replace(' ', '-')}-{st.lower()}", False
    return "report-only:unknown", False


def centroid_for(state: str, locality: str):
    """(lat, lng) city centroid or None. Callers must flag geo_precision."""
    loc = (locality or "").strip().upper().replace(".", "")
    return CITY_CENTROIDS.get(((state or "").strip().upper(), loc))
