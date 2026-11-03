"""Render Penn's undergrad campus from an OSM export as a 3-color pixel-art aerial map.

The map is rotated so Locust Walk runs perfectly horizontal, cropped to the
undergrad core (Chestnut to Spruce, 40th to 33rd), and drawn at PX_PER_M pixels
per meter with per-floor windows.

Usage: uv run --group map tools/render_campus.py data/map.osm locusts/static/map [--list]
Map data (c) OpenStreetMap contributors, ODbL.
"""
import hashlib, json, math, sys, os, statistics, struct
import xml.etree.ElementTree as ET
import numpy as np, random, zlib
from shapely.geometry import Polygon as SPoly, Point as SPoint
from scipy.ndimage import distance_transform_edt as edt, binary_dilation, binary_erosion, gaussian_filter, label, find_objects
from PIL import Image, ImageDraw

# ---- tunables -------------------------------------------------------------
PX_PER_M  = 5.0      # resolution
GROUND_Y  = 0.75     # ground-plane foreshortening (camera tilt)
HEIGHT_K  = 0.85     # building height squash relative to ground scale
LEAN_X    = -0.12    # roofs drift left as buildings rise (off-center camera)
LEVEL_M   = 3.6
DEFAULT_M = 10.8     # 3 floors when OSM has no height
PALETTE   = [(236, 226, 200), (176, 72, 58), (40, 28, 34), (104, 134, 78)]  # cream, brick, near-black, leaf green

# crop outline, built from street positions (see crop_polygon) so it survives a re-export.
# Covers Chestnut to Spruce from just past 40th to 33rd (and Amy Gutmann Hall, north of Chestnut at 34th), plus the Quad and Leidy south of
# Spruce (skipping the hospital block), and Franklin Field, the Palestra and the Penn Museum east of 33rd.

# inside the crop, but not part of undergrad life
EXCLUDE = {
    "Hospital of the University of Pennsylvania", "Ryan Veterinary Hospital",
    "Clyde F. Barker Transplant House", "Sheraton Philadelphia University City Hotel",
    "Penn Center for Rehabilitation and Care", "Curie Garage", "Spruce Garage",
    "Penn Museum Garage", "Drexel Lot G", "Gerri C. LeBow Hall", "University City Station",
    "John Morgan", "Lot 37",
}
HEIGHT_OVERRIDES = {"way/749918535": 4}   # "way/123": meters, for buildings OSM gets wrong (this one: Penn Law's courtyard pavilion)
# Heights (m) where OSM has none or is off, estimated from floor counts and the aerial imagery.
HEIGHT_BY_NAME = {
    "John M. Huntsman Hall": 24, "Irvine Auditorium": 22, "Franklin Building": 45, "Vance Hall": 20,
    "University Square": 23, "Wistar Institute": 30, "Towne Building": 18, "Annenberg Center for the Performing Arts": 10,
    "3401 Walnut": 20, "38th Street Garage": 18, "Spruce Street Green-Roof Wing": 14,
    "McNeil Center": 11, "Lauder Fischer Hall": 14, "Jaffe Building": 13, "Fox-Fels Hall": 12,
    "President's House": 11, "Civic House": 10, "Houston Hall": 17, "Van Pelt Library": 24,
    "Annenberg School of Communication": 18,
    "David Rittenhouse Laboratories": 25, "Department of Earth and Environmental Science": 15,
    "Chemistry Building 1958 Wing": 20, "English College House": 15, "King's Court": 15,
    "Gittis Hall": 18, "Golkin Hall": 19, "Silverman Hall": 20, "Tanenbaum Hall": 18,
    "Graduate Student Center": 12, "Hillel": 16, "Leidy Laboratories": 19, "Locust House": 12,
    "Penn-Drexel Newman Center": 13, "Perry World House": 13, "Kelly Writers House": 11,
    "Saint Mary's Church": 14, "Saints Agatha and James Catholic Church": 22, "Santander": 9,
    "Sigma Chi Fraternity": 12, "Zeta Psi": 12, "Stouffer College House": 15, "Stouffer Commons": 8,
    "Sweeten Alumni Building": 13, "The ARCH": 16, "The Chestnut": 20, "The Palestra": 21,
    "Tse and Cheng Sports Center": 12, "University Meeting and Guesthouse": 12, "University Square": 18,
    "Walnut Street West": 9, "Weightman Hall": 18, "Weiss Pavillion": 17, "Dunning Coaches' Center": 13,
    "Albert M. Greenfield Intercultural Center": 12, "Colonial Penn Center": 12, "Mask & Wig": 12,
    "Avril 50": 11, "Doc Magrogan's": 11, "Masala Kitchen": 11, "New Deck Tavern": 11,
    "White Dog Cafe": 11, "Hummus Grill": 8, "Fisher Fine Arts Library": 20,
    "Memorial Tower": 27, "Provost Tower": 25, "Ashhurst": 16, "Birthday": 16, "Bishop White": 16,
    "Cleemann": 16, "Coxe": 16, "E.F. Smith": 16, "Graduate": 16, "Magee": 16, "McIlhenney": 16,
    "Rodney": 16, "Thomas Penn": 16, "Ward": 16, "Warwick": 16, "Wilson": 16,
}
# OSM ways that are buildings but carry no building tag, so they'd be skipped
ADD_BUILDINGS = {"way/1349603488"}   # Tabernacle United Church (SPARC), Chestnut & 37th: tagged only as a place of worship
NAME_FIX = {"way/51469213": "Fisher Fine Arts Library",   # 220 S 34th St is unnamed in OSM
            "way/335523312": "3401 Walnut", "way/190511111": "38th Street Garage",
            "way/1156071498": "Spruce Street Green-Roof Wing", "way/202984683": "Penn 3930 Chestnut",
            "way/747954545": "Thomas W. Evans Building", "way/748793576": "Robert Schattner Center"}   # both "Penn Dental School"

# Facade material per building, read off the labeled Google Earth views.
#   brick (default) | dbrick dark brick | white concrete/limestone | stone gray stone
#   serp green serpentine | glass curtain wall | dpanel charcoal panels
WALLS = {
    "Kelly Writers House": "green", "New Deck Tavern": "green", "Civic House": "green", "Carriage House": "green",
    "Albert M. Greenfield Intercultural Center": "green", "Hummus Grill": "green",      # painted deep green
    "Van Pelt Library": "dbrick", "Franklin Building": "dbrick", "Goddard & Richards Laboratories": "dbrick",
    "McNeil Building": "dbrick", "Vance Hall": "dbrick", "Du Bois College House": "dbrick",
    "Irvine Auditorium": "brick", "Class of 1925 (Gregory College House)": "dbrick",
    "Harold C. Mayer Residence Hall": "dbrick", "Stouffer College House": "dbrick", "Skirkanich Hall": "dbrick",
    "Hill College House": "dbrick", "Solomon Lab": "dbrick", "Steinberg-Dietrich Hall": "dbrick",
    "Harrison College House": "white", "Harnwell College House": "white", "Rodin College House": "white",
    "Sansom Place East": "white", "Sansom Place West": "white", "Annenberg School of Communication": "white",
    "Stiteler Hall": "white", "Chestnut Hall": "brick", "University of Pennsylvania Graduate School of Education": "white",
    "Hillel": "white", "Chemistry Building 1958 Wing": "white", "3401 Walnut": "white", "The Chestnut": "white",
    "38th Street Garage": "brick", "David Rittenhouse Laboratories": "brick",
    "Houston Hall": "stone", "The ARCH": "stone", "Saint Mary's Church": "stone", "Towne Building": "stone",
    "Saints Agatha and James Catholic Church": "stone", "Leidy Laboratories": "stone", "Jaffe Building": "stone",
    "Dunning Coaches' Center": "stone", "Penn-Drexel Newman Center": "stone",
    "Annenberg Center for the Performing Arts": "stone",
    "College Hall": "serp", "Claudia Cohen Hall": "serp",
    "Wistar Institute": "glass", "Spruce Street Green-Roof Wing": "glass", "The Radian": "dpanel",
}
# Flat-roof tone: white | mid | dark | green | copper
ROOF_TONE = {
    "Hill College House": "white", "The Radian": "white", "Stiteler Hall": "green", "Chemistry Building 1958 Wing": "white",
    "Annenberg School of Communication": "white", "3401 Walnut": "white", "Steinberg Conference Center": "white",
    "The Chestnut": "white", "Chestnut Hall": "white", "Wistar Institute": "white", "38th Street Garage": "white",
    "Van Pelt Library": "dark", "Franklin Building": "dark", "McNeil Building": "dark", "Vance Hall": "dark",
    "Pottruck Health and Fitness Center": "dark", "University Square": "dark", "Irvine Auditorium": "dark", "Steinberg-Dietrich Hall": "dark",
    "Du Bois College House": "dark", "Stouffer College House": "dark", "Harold C. Mayer Residence Hall": "dark",
    "Harnwell College House": "dark", "Harrison College House": "dark", "Rodin College House": "dark",
    "Sansom Place East": "dark", "Sansom Place West": "dark", "Charles Addams Fine Arts Hall": "dark",
    "Solomon Lab": "dark", "University of Pennsylvania Graduate School of Education": "dark",
    "Goddard & Richards Laboratories": "dark", "Skirkanich Hall": "dark", "David Rittenhouse Laboratories": "dark",
    "Class of 1925 (Gregory College House)": "dark", "Annenberg Center for the Performing Arts": "dark",
    "Lauder College House": "green", "Gutmann College House": "green", "Spruce Street Green-Roof Wing": "green",
    "Meyerson Hall": "mid", "John M. Huntsman Hall": "mid", "Education Commons": "dark",
}
# Roof strips as fractions of the building's bounding box in map space (x: west->east, y: south->north)
STRIPS = {
    "Steinberg-Dietrich Hall": [((0.35, 0.85), (0.52, 0.64), "glass")],
    "The Chestnut": [((0.2, 0.8), (0.06, 0.24), "pool")],
    "Fisher Fine Arts Library": [((0.15, 0.55), (0.05, 0.25), "glass")],
    "Meyerson Hall": [((0.08, 0.3), (0.15, 0.85), "copper"), ((0.38, 0.6), (0.15, 0.85), "copper")],
}
PART_HEIGHT = {"345137082": 44, "345137084": 30, "345137085": 30,   # Irvine's central tower and the blocks flanking it
               "350513167": 25, "350513168": 16,           # Annenberg Center fly tower and octagon
               "351053389": 18,
               "748793578": 5, "748793574": 5}   # Penn Dental's Levy tower and Schattner wing: drawn in MASS instead    # Hill's atrium part: kept at roof level, its glass clerestory is in MASS
# stepped rings on top of a part: (scale about its center, meters added)
CAPS = {"375969460": [(0.95, 2.2), (0.83, 2.2), (0.70, 2.0)]}  # Huntsman's drum: three sloped metal tiers round an open deck
SERPENTINE = {"College Hall", "Claudia Cohen Hall"}        # green serpentine stone
STONE = {"Houston Hall", "The ARCH", "Saint Mary's Church", "Saints Agatha and James Catholic Church"}
RED_TILE = {"Fisher Fine Arts Library", "College Hall", "The ARCH"}
COPPER = {"Memorial Tower", "Provost Tower"}
STANDS = {"Weiss Pavillion"}                               # Franklin Field's horseshoe
HIP_EXTRA = {"The Palestra", "Fisher Fine Arts Library", "Penn Museum", "McNeil Center", "Lauder Fischer Hall",
             "Jaffe Building", "Dunning Coaches' Center", "Department of Earth and Environmental Science",
             "Towne Building", "Weightman Hall", "Fisher Bennett Hall", "Fox-Fels Hall", "President's House",
             "University Meeting and Guesthouse", "Civic House", "The ARCH", "College Hall", "Houston Hall"}
QUAD = {"Ashhurst", "Baird", "Baldwin", "Birthday", "Bishop White", "Bodine", "Brooks", "Butcher", "Carruth",
        "Chestnut", "Class of '28", "Cleemann", "Coxe", "Craig", "E.F. Smith", "Fitler", "Foerderer", "Franklin",
        "Graduate", "Hopkinson", "Leidy", "Lippincott", "Magee", "McClelland", "McIlhenney", "McKean",
        "Memorial Tower", "Morgan", "Morris", "New York Alumni", "Provost Tower", "Rodney", "Smith", "Speakman",
        "Thomas Penn", "Ward", "Warwick", "Wilson"}
NO_DORMERS = {"Claudia Cohen Hall", "Irvine Auditorium", "Kelly Writers House",
              "Saint Mary's Church", "Saints Agatha and James Catholic Church"}
# glazed gables on Huntsman's roof (rotated-map meters), read off the aerial view
SKYLIGHTS = {}
# slate-roofed collegiate gothic (the Quad, College Green); everything else gets a light flat roof
SLATE = {"Ashhurst", "Baird", "Baldwin", "Birthday", "Bishop White", "Bodine", "Brooks", "Butcher",
         "Carruth", "Chestnut", "Class of '28", "Cleemann", "Coxe", "Craig", "E.F. Smith", "Fitler",
         "Foerderer", "Franklin", "Graduate", "Hopkinson", "Leidy", "Lippincott", "Magee", "McClelland",
         "McIlhenney", "McKean", "Memorial Tower", "Morgan", "Morris", "New York Alumni", "Provost Tower",
         "Rodney", "Smith", "Speakman", "Thomas Penn", "Ward", "Warwick", "Wilson", "College Hall",
         "Claudia Cohen Hall", "Houston Hall", "Irvine Auditorium", "Kelly Writers House",
         "Saint Mary's Church", "Saints Agatha and James Catholic Church"}
# pale concrete/limestone facades instead of brick
CONCRETE = {"Harnwell College House", "Harrison College House", "Rodin College House", "Sansom Place East",
            "Sansom Place West", "Van Pelt Library", "Annenberg Center for the Performing Arts",
            "Steinberg-Dietrich Hall", "David Rittenhouse Laboratories", "The Radian"}
TREE_SPACING_M = 7.5     # procedural canopy density on lawns
WINDOW_STYLE = {"Van Pelt Library": "strips", "Irvine Auditorium": "strips", "College Hall": "paired", "Houston Hall": "paired", "Weiss Pavillion": "arches", "John M. Huntsman Hall": "punched",
                "Harnwell College House": "punched", "Harrison College House": "punched",
                "Rodin College House": "punched"}
FALL_SHARE = 0.12       # it's October: some crowns are turning

GROUND, GRASS, ROAD, PATH, WATER, PITCH, MARK, OFFMAP, TRACK, CROSS = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
ROOF, WALL_FRONT, WALL_SIDE, OUTLINE, TREE, TREE_LIT, WINDOW, GLARE, SLATE_ROOF, TREE_SHADE = 10, 11, 12, 13, 14, 15, 16, 17, 18, 19
CONC_FRONT, CONC_SIDE = 20, 21
(CURB, PARKING, FLOWER, STEPS, SLOPE_LIT, SLOPE_MID, SLOPE_DARK, RIDGE, DORMER, GREEN_ROOF, FALL, FALL_LIT,
 CORNICE, DOOR, UNIT_TOP, UNIT_SIDE, LAMP, BENCH, PARAPET, GRASS_DARK, METAL) = range(22, 43)
(SERP_FRONT, SERP_SIDE, STONE_FRONT, STONE_SIDE, TILE_LIT, TILE_MID, TILE_DARK, COP_LIT, COP_MID, COP_DARK,
 ROOF_LIGHT, ROOF_DARK, SEATS, GLASS_LIT, GLASS_MID, GLASS_DARK) = range(43, 59)
(DB_FRONT, DB_SIDE, GW_FRONT, GW_SIDE, DP_FRONT, DP_SIDE, WIN_LIGHT, DORMER_COP) = range(59, 67)
(LB_FRONT, LB_SIDE, TAN_FRONT, TAN_SIDE, AWNING, ROOF_SHADOW, FAN, EVERGREEN, CAR_WHITE, CAR_RED, CAR_GRAY,
 CAR_GREEN, CAR_SILVER, CAR_DARKRED, ENDZONE, YARD) = range(67, 83)
CAR_PAINTS = (CAR_WHITE, CAR_RED, CAR_GRAY, CAR_GREEN, CAR_SILVER, CAR_DARKRED)   # columns of cars.png
DECK = 83                                       # poured-concrete bridge deck
PODIUM_LAWN = 84                                # lawn on a podium roof: looks like grass, but ground shadows skip it
GRAVEL = 85                                     # tan gravel ballast on flat roofs
DECK_TAN, DECK_LINE = 93, 94                    # a parking deck's tan stall fields and their faded paint
PINE_DEEP = 86                                  # the shaded flank of an evergreen, darker than TREE_SHADE
SOIL_FRONT, SOIL_DEEP, SOIL_SIDE = 87, 88, 89      # the cut face of the base the campus stands on
GREEN_FRONT, GREEN_SIDE = 91, 92                 # painted deep green, the planting beds' black-green checker
BASE_M = 10.0          # depth of that base, in metres
CONIFER_SHARE = 0.12
DARK_SHARE = 0.15       # crowns in deep shade-green (copper beeches, old oaks), for variety
DORMER_SET = {"Houston Hall", "College Hall", "Towne Building", "Department of Earth and Environmental Science",
              "Dunning Coaches' Center", "Fisher Bennett Hall"}
HOLE_FILL = {}
PAVED_COURTS = {"Hill College House"}          # Hill's two light wells are paved, not planted
# Ground painted over OSM's where its footway classes miss the width or paving: way id -> (width m, look).
# Width 0 fills a mapped area instead of stroking a line.
# Looks: pavers (red brick), deck (concrete, outlined like a bridge), planting (dark beds).
WAY_PAINT = {"977458953": (5, "pavers"),      # Hill's sunken brick walk along the moat, Walnut to the bridge
             "977458955": (5.5, "deck"),      # Hill's entrance bridge over the moat
             "977458954": (4, "deck"),        # ... and its landing at the door
             "708552405": (5, "pavers"),      # Lauder's brick forecourt along Woodland Walk
             "708552410": (5, "pavers"),      # ... and in front of the entrance and the wedge
             "32120090": (3.5, "pavers"),     # Class of 1956 Walk along Dietrich's south face,
             "156097910": (3.5, "steps"),     # ... its steps down to Blanche Levy Park
             "164656747": (3.5, "pavers"),    # ... and on to the Van Pelt plaza
             "164656751": (3, "pavers"),      # walk along Van Pelt's front
             "156097906": (3, "steps"), "164656750": (3, "pavers"),
             "574614414": (3, "pavers"),      # up Van Pelt's east side to Walnut
             "748810604": (4, "pavers"),      # Huntsman's south walk, up to its Locust Walk entrance
             "330138978": (3, "pavers"),      # walk between Huntsman's courtyard and Solomon Lab, Walnut to Locust Walk
             "190327588": (0, "paving"),      # concrete plaza between Solomon, Stiteler and GSE (width 0 = fill the area)
             "335523315": (5, "pavers"),
             "165416402": (0, "paving"),      # Annenberg Center Plaza: pale pavers, not brick
             "165416403": (4, "paving"), "459125152": (4, "paving")}   # Annenberg Plaza walks      # Woodland Walk's brick stretch along Wistar, 36th & Spruce side to 37th
# Unmapped ground as polygons in rotated-map meters (x along Locust, y north), like SKYLIGHTS.
_disc = lambda cx, cy, r, n=20: [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
_VP_PLAZA = [(312, 222), (329, 222), (352, 216), (347, 208), (336, 198), (318, 191), (308, 195), (310, 210)]
GROUND_PATCH = [([(522, 312.9), (530.8, 312.9), (530.8, 356.6), (522, 356.6)], "planting"),   # Hill's planted west moat
                ([(499, 389.3), (505, 389.3), (505, 391.6), (499, 391.6)], "steps"),           # Lauder's entrance steps
                (_VP_PLAZA, "pavers"),                                                         # Van Pelt's brick plaza
                ([(331, 208), (340, 208), (342, 210), (340, 212.5), (331, 212.5), (329.5, 210)], "lawn"),  # ... its oval lawn bed
                ([(317.5, 224.6), (324, 224.6), (324, 228.4), (317.5, 228.4)], "steps"),       # ... its entrance stair: upper flight,
                ([(315, 223.4), (326.5, 223.4), (326.5, 224.6), (315, 224.6)], "deck"),        # ... landing,
                ([(314.5, 218.6), (326.5, 218.6), (326.5, 223.4), (314.5, 223.4)], "steps"),   # ... and the wide lower flight
                (_disc(325.9, 206.9, 2.4), "art"),
                ([(740.5, 222.5), (768.5, 222.5), (768.5, 263.5), (740.5, 263.5)], "lawn"),     # DRL's sunken garden
                ([(718, 251), (745, 251), (747, 254.5), (744, 258), (720, 258), (717.5, 254.5)], "planting"),    # ... its courtyard bed
                ([(316.5, 411.6), (379.6, 411.6), (380.2, 432.6), (316.3, 433.9)], "grove"),                     # Penn Law's tree court
                ([(105.5, 68), (112.5, 70), (112.5, 86), (105.5, 86)], "pavers"),          # brick path up from Woodland Walk by Wistar,
                ([(106, 76), (112, 76), (112, 80), (106, 80)], "steps"),                   # ... its steps up to Steinberg-Dietrich
                ([(117, 76), (125, 76), (125, 85), (117, 85)], "paving"),                  # ... and the little plaza beside it
                ([(-426.8, 183.6), (-393.4, 183.6), (-393.4, 229), (-426.8, 229)], "grove"),        # Gutmann's garden between the wings,
                ([(-426.8, 183.6), (-423.6, 183.6), (-423.6, 229), (-426.8, 229)], "pavers"),       # ... its walk along the west wing
                ([(-399, 183.6), (-396, 183.6), (-405.5, 229), (-408.5, 229)], "pavers"),           # ... and the diagonal ramp
                ([(-545, 94.5), (-515.5, 94.5), (-515.5, 123), (-545, 123)], "grove")] + [            # Penn Dental's lawn                                         # the Split Button
               (_disc(325.9 + dx, 206.9 + dy, 0.35, 8), "hole") for dx, dy in ((-0.6, 0.5), (0.6, 0.5), (-0.6, -0.5), (0.6, -0.5))]
# No procedural or OSM trees in these (rotated-map meter polygons), e.g. open forecourts. The trees are still
# generated and skipped only when drawn, so the rest of the canopy keeps its layout and leaf pattern.
TREE_CLEAR = [[(488, 383), (494, 383), (494, 374), (512, 374), (512, 383), (530, 383), (530, 398), (488, 398)],  # Lauder's forecourt
              _VP_PLAZA + [(312, 222)], [(314, 221), (328, 221), (328, 233), (314, 233)],                    # Van Pelt's plaza and steps
              [(-58, 164), (-46, 164), (-46, 178), (-58, 178)]]                                              # Huntsman's Locust Walk entrance
STOREFRONTS = {"University Square", "3401 Walnut", "The Radian", "Shops at 40th Street", "Doc Magrogan's",
               "Masala Kitchen", "New Deck Tavern", "White Dog Cafe", "Avril 50", "Hummus Grill",
               "Ekta Indian Cuisine", "Starbucks", "Santander", "Hamilton Court"}

# Building-level overrides, by name or (for buildings OSM leaves unnamed) by "way/123": h (m), wall, style, roof ((shape, material) or flat tone), base course material,
# chimneys, ducts, units, bulk(heads), grid (skylight rows), crenel(lated parapet), store(fronts).
# 38th Street Garage's top deck, traced from the aerial view through a perspective fit of its four corners. Fractions of
# the building's bbox (x east, y north; the deck proper ends at x 0.911, the rest is the stair tower's bump).
# Three tiers: pale concrete lanes round tan stall fields, a dark ramp at each split.
GARAGE_DECK = {
    "zones": [("tan", 0.035, 0.29, 0.755, 0.965), ("tan", 0.025, 0.295, 0.53, 0.71), ("shade", 0.295, 0.396, 0.53, 1.0),   # back tier
              ("ramp", 0.41, 0.49, 0.54, 1.0),
              ("tan", 0.095, 0.6, 0.275, 0.505), ("patch", 0.395, 0.45, 0.295, 0.428), ("tan", 0.618, 0.81, 0.262, 0.485),  # middle tier
              ("tan", 0.09, 0.237, 0.006, 0.205), ("ramp", 0.24, 0.283, 0.006, 0.21), ("tan", 0.298, 0.79, 0.008, 0.235)],   # front tier
    "hatch": [(0.53, 0.595, 0.427, 0.507), (0.298, 0.355, 0.008, 0.068), (0.117, 0.165, 0.006, 0.056)],
    # stall lines: a run along the aisle from A to B, each line reaching toward C from its spot on the run
    "rows": [((0.397, 0.009), (0.795, 0.011), (0.392, 0.072), 10), ((0.567, 0.223), (0.786, 0.22), (0.576, 0.151), 5),
             ((0.302, 0.231), (0.319, 0.081), (0.369, 0.232), 4), ((0.101, 0.48), (0.589, 0.504), (0.117, 0.402), 11),
             ((0.121, 0.258), (0.389, 0.274), (0.116, 0.327), 7), ((0.057, 0.958), (0.275, 0.967), (0.067, 0.849), 5),
             ((0.069, 0.504), (0.296, 0.508), (0.064, 0.589), 6), ((0.107, 0.201), (0.229, 0.204), (0.114, 0.144), 3)],
    "cars": [(0.116, 0.704, 35, "gray"), (0.148, 0.686, 80, "white"), (0.281, 0.578, 110, "red"), (0.156, 0.434, 90, "silver"),   # x, y, heading, paint
             (0.193, 0.445, 90, "white"), (0.228, 0.445, 90, "red"), (0.27, 0.435, 90, "gray"), (0.388, 0.472, 100, "white"),
             (0.437, 0.476, 90, "black"), (0.558, 0.398, 0, "black"), (0.565, 0.342, 0, "black"), (0.564, 0.286, 0, "silver"),
             (0.636, 0.436, 65, "white"), (0.153, 0.294, 60, "gray"), (0.269, 0.305, 60, "red"), (0.307, 0.307, 60, "white"),
             (0.348, 0.308, 60, "red"), (0.757, 0.302, 120, "black"), (0.342, 0.222, 0, "white"), (0.336, 0.172, 0, "red"),
             (0.336, 0.122, 0, "orange"), (0.335, 0.072, 0, "black"), (0.582, 0.186, 105, "white"), (0.629, 0.199, 105, "black"),
             (0.669, 0.204, 105, "black"), (0.71, 0.2, 105, "black"), (0.755, 0.183, 105, "black"), (0.393, 0.05, 80, "silver"),
             (0.442, 0.05, 80, "white"), (0.486, 0.047, 80, "green"), (0.561, 0.043, 80, "white"), (0.604, 0.051, 80, "black"),
             (0.647, 0.048, 80, "silver"), (0.688, 0.048, 80, "white"), (0.766, 0.041, 80, "white"), (0.189, 0.165, 90, "gray"),
             (0.229, 0.16, 90, "white"), (0.273, 0.166, 90, "gray"), (0.174, 0.045, 90, "black"), (0.137, 0.034, 90, "gray"),
             (0.258, 0.038, 90, "black")],
}
BLD = {
    "Van Pelt Library": {"h": 13, "wall": "dbrick", "style": "strips", "grid": True, "units": 2},
    "Franklin Building": {"h": 45, "wall": "dbrick", "style": "punched", "units": 1, "bulk": 0},
    "3401 Walnut": {"store": True, "units": 3, "bulk": 1},
    # four storeys over the shops (the back wings and the ground-floor shopfronts); the six-storey Walnut front in MASS
    "University Square": {"h": 14.4, "wall": "brick", "store": True, "base": None, "roof": ("flat", "dark"), "units": 2, "ducts": 0, "bulk": 0, "belts": True},
    # west: glass offices whose banded Walnut front steps back storey by storey (the corner is cut to one storey, and
    # each storey above set back in MASS); a glass barrel atrium; east: the brick gym block
    "Pottruck Health and Fitness Center": {"h": 18, "wall": "glass", "style": "ribbon", "roof": ("flat", "dark"), "units": 0, "bulk": 0,
                                           "cuts": [(0.098, 0.301, -0.05, 0.355)], "cut_z": 3.6, "cut_roof": "white"},
    # OSM's outline covers the whole block; the courtyard is a lawn podium and the wings and drum stand on it (MASS)
    "John M. Huntsman Hall": {"h": 1.2, "wall": "brick", "roof": ("flat", "lawn"), "units": 0, "bulk": 0, "ducts": 0},
    "Steinberg Conference Center": {"units": 3, "bulk": 1},
    "McNeil Building": {"h": 22, "units": 0},
    "Irvine Auditorium": {"h": 14, "wall": "dbrick", "style": "tall", "roof": ("flat", "dark"), "chimneys": 0, "units": 0, "bulk": 0},
    "Fisher Fine Arts Library": {"wall": "brick", "style": "tall", "chimneys": 3},
    "College Hall": {"chimneys": 4, "style": "tall"},
    "Houston Hall": {"chimneys": 5, "style": "paired"},
    # four hipped ranges round two light courts (filled in at one storey) with a north-south range between them
    "Towne Building": {"h": 16, "chimneys": 4, "style": "paired", "roof": ("hip", "metal"), "cut_roof": "mid", "cut_z": 5, "cut_units": 4,
                       "cuts": [(0.142, 0.394, 0.240, 0.503), (0.536, 0.842, 0.240, 0.503)]},
    "Fisher Bennett Hall": {"h": 15, "wall": "dbrick", "style": "paired", "roof": ("flat", "glass"), "units": 0, "bulk": 0},  # glazed link; wings in MASS
    "Lerner Center": {"h": 14.4, "wall": "brick", "roof": ("flat", "dark"), "units": 1, "bulk": 0},
    "Levine Hall": {"h": 10, "wall": "brick", "roof": ("flat", "dark"), "units": 1, "bulk": 0},     # GRASP Lab block; the tower in MASS
    "Amy Gutmann Hall": {"h": 26, "wall": "dpanel", "style": "vfins", "roof": ("flat", "white"), "units": 2, "bulk": 0, "cornice": False},
    "Skirkanich Hall": {"h": 21, "wall": "dpanel", "style": "punched", "roof": ("flat", "dark"), "units": 2, "bulk": 0},
    "The Palestra": {"roof": ("barrel_x", "light"), "k": 0.35, "style": "arches"},
    "Wistar Institute": {"h": 15, "wall": "brick", "style": "strips", "roof": ("flat", "white"), "units": 3, "ducts": 3, "bulk": 1},  # lab wing
    "Class of 1920 Commons": {"base": STONE_FRONT, "ducts": 3, "units": 3},
    "Stouffer College House": {"ducts": 4, "units": 2},
    "Harold C. Mayer Residence Hall": {"ducts": 3, "units": 2},
    "Du Bois College House": {"style": "paired", "ducts": 2},
    "The Radian": {"style": "scatter", "store": True},
    "Solomon Lab": {"h": 14.5, "wall": "dbrick", "crenel": True, "style": "tall", "piers": CONC_FRONT, "roof": ("flat", "dark"), "units": 1},
    "University of Pennsylvania Graduate School of Education": {"h": 15, "wall": "glass", "style": "none", "roof": ("flat", "dark"),
                                                                "units": 4, "bulk": 0, "ducts": 2},   # dark glass between pale fins
    "Stiteler Hall": {"wall": "dbrick", "style": "strips", "roof": ("flat", "white"), "units": 2},
    "Caster Building": {"roof": ("flat", "dark")},
    "David Rittenhouse Laboratories": {"h": 4.5, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 2, "bulk": 0, "ducts": 0},
    "Chemistry Laboratories 1973 Wing": {"units": 4, "ducts": 2},
    "Chemistry Building 1958 Wing": {"units": 5, "ducts": 3},
    "Annenberg School of Communication": {"h": 12, "style": "tall", "base": None, "roof": ("flat", "mid"), "units": 1, "bulk": 0},
    "Charles Addams Fine Arts Hall": {"units": 3, "bulk": 1},
    # --- the Annenberg block, Walnut to Locust Walk between 36th and 37th ---
    "Tabernacle United Church": {"h": 6, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "units": 0, "bulk": 0},
    "Thomas W. Evans Building": {"h": 14, "wall": "dbrick", "style": "paired", "roof": ("flat", "white"), "crenel": True, "units": 2, "ducts": 2, "bulk": 0},
    "Robert Schattner Center": {"h": 5, "wall": "brick", "roof": ("flat", "dark"), "units": 0, "bulk": 0},
    "Baker Walkway": {"wall": "glass", "style": "none", "roof": ("flat", "white"), "units": 0, "bulk": 0},
    # --- frat row, Walnut between 38th and 39th (unnamed in OSM, keyed by way) ---
    "way/520723130": {"h": 11, "wall": "stone", "roof": ("hip", "metal"), "cap": 3, "dormers": True, "chimneys": 2},   # 3831: mansard
    "way/520723131": {"h": 10.5, "wall": "dbrick", "roof": ("hip", "metal"), "cap": 3, "dormers": True, "chimneys": 1},  # 3827: mansard
    "way/520723132": {"h": 15, "wall": "white", "style": "punched", "roof": ("hip", "light"), "cap": 3.5, "chimneys": 2},  # 3821 Sigma Nu
    "way/520723133": {"h": 13, "wall": "white", "style": "paired", "roof": ("hip", "light"), "cap": 1.5, "chimneys": 1},  # 3817
    "way/520723134": {"h": 14, "wall": "brick", "roof": ("hip", "slate"), "cap": 3.5, "dormers": True, "chimneys": 2},   # 3813-15
    "way/520723135": {"h": 14, "wall": "brick", "roof": ("hip", "metal"), "cap": 3, "dormers": True, "chimneys": 1},    # 3809-11
    "way/520723136": {"h": 14, "wall": "dbrick", "roof": ("hip", "slate"), "cap": 3.5, "dormers": True, "chimneys": 2},  # 3805 TEP
    "way/350187730": {"wall": "brick", "roof": ("flat", "white"), "units": 2},                                       # 119 S. 39th
    "Annenberg Center for the Performing Arts": {"h": 12, "wall": "lbrick", "style": "none", "roof": ("flat", "dark"), "units": 2, "bulk": 1},
    "Annenberg Public Policy Center": {"wall": "white", "style": "ribbon", "roof": ("flat", "white"), "units": 0, "bulk": 0},
    "The ARCH": {"wall": "stone", "roof": ("hip", "metal"), "chimneys": 2, "style": "tall"},
    "Locust House": {"wall": "brick", "roof": ("flat", "dark"), "units": 1},
    "Colonial Penn Center": {"wall": "brick", "roof": ("flat", "mid"), "units": 2},
    "Graduate Student Center": {"h": 14, "wall": "dbrick", "roof": ("flat", "dark"), "units": 1},
    "way/32122181": {"h": 13, "wall": "brick", "roof": ("flat", "white"), "units": 2},    # 3631-33 Locust Walk
    "way/32122193": {"h": 12, "wall": "brick", "roof": ("flat", "white"), "units": 1},    # St. Elmo Club (Delta Phi)
    "way/164655924": {"h": 13, "wall": "dbrick", "roof": ("flat", "dark"), "units": 1},   # beside the ARCH
    "Steinberg-Dietrich Hall": {"units": 3, "bulk": 1},
    "Vance Hall": {"units": 1},
    "Chestnut Hall": {"belts": True, "units": 2, "bulk": 1},
    "Leidy Laboratories": {"style": "tall", "units": 3},
    "Goddard & Richards Laboratories": {"style": "strips", "units": 2},
    "Lauder College House": {"h": 9, "wall": "dbrick", "style": "paired", "units": 0, "bulk": 0},   # wedge height; wings in MASS
    # --- fixes from the second round of side-by-side screenshots ---
    "Hill College House": {"ducts": 6, "units": 2, "style": "punched"},
    "Zeta Psi": {"roof": ("hip", "light"), "cap": 3, "chimneys": 3},     # pale shingle mansard, flat top
    "Gutmann College House": {"h": 7, "wall": "brick", "roof": ("flat", "dark"), "units": 1, "bulk": 0},   # the low link; wings, tower in MASS
    "Walnut Street West": {"wall": "brick", "roof": ("flat", "white"), "units": 0, "bulk": 0},
    "Saint Mary's Church": {"h": 5, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "chimneys": 0, "units": 0, "bulk": 0},
    "Saints Agatha and James Catholic Church": {"h": 19, "wall": "stone", "style": "tall", "roof": ("gable_y", "slate"), "slope": 1.1, "chimneys": 0},
    "Penn-Drexel Newman Center": {"h": 15, "wall": "stone", "style": "tall", "roof": ("flat", "dark")},
    "The Chestnut": {"h": 70, "wall": "glass", "style": "fins", "roof": ("flat", "tan"), "units": 0, "bulk": 0},
    "Chestnut Hall": {"h": 40, "wall": "lbrick", "roof": ("flat", "mid"), "base": STONE_FRONT, "belts": True,
                      "cuts": [(0.5, 1.02, 0.24, 0.40), (0.5, 1.02, 0.60, 0.76)], "cut_z": 8, "units": 3},
    "Hamilton Court": {"wall": "dbrick", "roof": ("flat", "dark"), "ducts": 3, "style": "paired"},
    "Verizon Building": {"h": 19, "wall": "stone", "roof": ("flat", "mid"), "style": "tall", "units": 3},
    "Penn 3930 Chestnut": {"h": 28, "wall": "white", "style": "punched", "roof": ("flat", "white"), "units": 4, "bulk": 1},
    "Tangen Hall": {"h": 30, "wall": "glass", "style": "ribbon", "roof": ("flat", "white"), "units": 5, "bulk": 1},
    "38th Street Garage": {"style": "garage", "units": 0, "bulk": 0, "cornice": False, "deck": GARAGE_DECK},
    "40th Street Parking Lot": {"wall": "white", "style": "ribbon", "roof": ("flat", "mid"), "stalls": True, "units": 0, "bulk": 2, "cornice": False},
    "Tanenbaum Hall": {"h": 10, "wall": "dbrick", "roof": ("flat", "gravel"), "units": 0, "bulk": 0},    # Sansom wing; main block in MASS
    "Silverman Hall": {"h": 18, "wall": "dbrick", "roof": ("hip", "metal"), "cap": 4, "chimneys": 0, "style": "tall", "base": STONE_FRONT},
    "Gittis Hall": {"h": 12, "wall": "dbrick", "style": "arches", "roof": ("flat", "gravel"), "units": 0, "bulk": 0},
    "Golkin Hall": {"h": 14, "wall": "dbrick", "style": "paired", "roof": ("flat", "gravel"), "units": 0, "bulk": 0},
    "English College House": {"h": 6, "roof": ("flat", "dark"), "wall": "dbrick", "units": 0, "bulk": 0},  # low dark core; the bar in MASS
    "King's Court": {"h": 0.4, "roof": ("flat", "lawn"), "wall": "brick", "units": 0, "bulk": 0},          # planted court; blocks in MASS
    "3401 Walnut": {"store": True, "units": 3, "bulk": 1, "style": "ribbon"},
    "Franklin Building": {"h": 45, "wall": "dbrick", "style": "punched", "units": 0, "bulk": 0},
}
_HR = [{"box": (0.3, 0.7, 0.28, 0.72), "top": 4.5, "wall": "white", "style": "none", "roof": ("flat", "dark"), "units": 2},
       {"box": (0.42, 0.58, 0.4, 0.6), "top": 7.0, "wall": "white", "style": "none", "roof": ("flat", "dark"), "units": 0}]
_TURRET = lambda fx, fy, r, z1, wall="brick", mat="slate", slope=2.3: {
    "cyl": (fx, fy, r), "z0": 0, "z1": z1, "wall": wall, "style": "tall", "roof": ("cone", mat), "noclip": True, "slope": slope}
# Hand-modeled volumes: fractions of the building's bounding box in map space (x west->east, y south->north).
MASS = {
    "John M. Huntsman Hall": [
        # west wing along 38th and south wing along Locust Walk, each under a pitched metal roof
        {"box": (0.0, 0.346, 0.0, 0.702), "z1": 13, "wall": "brick", "style": "strips", "roof": ("gable_y", "metal"), "slope": 0.8, "ends": True, "units": 0},
        {"box": (0.346, 1.0, 0.0, 0.191), "z1": 13, "wall": "brick", "style": "punched", "roof": ("gable_x", "metal"), "slope": 0.85, "ends": True, "units": 0},
        # courtyard: paved strip with six pyramid skylights along the west wing, stone terrace at the drum's foot
        {"box": (0.346, 0.418, 0.205, 0.536), "z0": 1.2, "z1": 1.3, "wall": "white", "style": "none", "roof": ("flat", "paving"), "units": 0, "cornice": False},
        {"box": (0.346, 0.764, 0.487, 0.604), "z0": 1.2, "z1": 1.3, "wall": "white", "style": "none", "roof": ("flat", "paving"), "units": 0, "cornice": False},
    ] + [{"box": (0.368, 0.397, (y - 1 - 178) / 102.6, (y + 1 - 178) / 102.6), "z0": 1.3, "z1": 1.5, "wall": "white", "style": "none",
          "roof": ("hip", "glass"), "cap": 1.6, "cornice": False} for y in (202, 207.5, 213, 218.5, 224, 229.5)] + [
        # Locust Walk entrance: stone surround with a dark glazed door, mid south face where the path comes up
        {"box": (0.627, 0.726, -0.0136, 0.0039), "z1": 6.5, "wall": "stone", "style": "none", "roof": ("flat", "dark"), "noclip": True, "units": 0, "cornice": False},
        {"box": (0.648, 0.706, -0.0195, -0.0117), "z1": 4.2, "wall": "glass", "style": "none", "roof": ("flat", "dark"), "noclip": True, "units": 0, "cornice": False},
        # brick chimney stacks: south-west corner and the south wing's east gable
        {"box": (0.0144, 0.079, -0.0146, 0.0244), "z1": 25, "wall": "brick", "style": "none", "roof": ("flat", "dark"), "noclip": True, "units": 0},
        {"box": (0.98, 1.0145, 0.073, 0.117), "z1": 25, "wall": "brick", "style": "none", "roof": ("flat", "dark"), "noclip": True, "units": 0}],
    "Solomon Lab": [   # notched outer block, higher centre, and a top storey at the north end
        {"box": (0.22, 0.78, 0.18, 0.86), "z1": 19, "wall": "dbrick", "style": "tall", "roof": ("flat", "dark"), "crenel": True, "units": 1},
        {"box": (0.30, 0.70, 0.55, 0.84), "z1": 21.6, "wall": "dbrick", "style": "tall", "roof": ("flat", "dark"), "crenel": True, "units": 2}],
    "University of Pennsylvania Graduate School of Education": [   # pale end block at the east
        {"box": (0.85, 1.0, 0.0, 1.0), "z1": 15.5, "wall": "white", "style": "punched", "roof": ("flat", "white"), "units": 1}],
    "Stiteler Hall": [   # green-roofed annexes: west of the bar's north end, and the link to GSE
        {"box": (0.074, 0.438, 0.657, 0.986), "z1": 9, "wall": "white", "style": "ribbon", "roof": ("flat", "green"), "noclip": True, "units": 0},
        {"box": (0.44, 1.0, 1.0, 1.163), "z1": 4.5, "wall": "white", "style": "none", "roof": ("flat", "green"), "noclip": True, "units": 0}],
    "Amy Gutmann Hall": [   # banded stair tower at the north-west corner, plant penthouses across the back of the roof
        {"box": (0.026, 0.151, 0.593, 1.0), "z1": 33, "wall": "white", "style": "ribbon", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.138, 0.29, 0.672, 0.996), "z0": 26, "z1": 30, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.206, 0.414, 0.356, 0.672), "z0": 26, "z1": 28.5, "wall": "metal", "style": "none", "roof": ("flat", "mid"), "units": 0},
        {"box": (0.414, 0.553, 0.316, 0.751), "z0": 26, "z1": 29.5, "wall": "metal", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.664, 0.845, 0.356, 0.79), "z0": 26, "z1": 29, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 1}],
    "Thomas W. Evans Building": [   # Penn Dental on Spruce: slate front range with dormers, crenellated tower, white-roofed lab wings
        {"box": (0.0, 1.0, 0.0, 0.337), "z1": 15, "wall": "dbrick", "style": "paired", "roof": ("hip", "metal"), "cap": 5,
         "dormers": True, "chimneys": 6},
        {"box": (0.468, 0.584, 0.337, 0.673), "z1": 15, "wall": "stone", "style": "tall", "roof": ("gable_y", "metal"), "slope": 1.0},
        {"box": (0.403, 0.579, -0.041, 0.282), "z1": 27, "wall": "stone", "style": "tall", "roof": ("flat", "white"), "noclip": True,
         "crenel": True, "units": 0}, {"cyl": (0.413, -0.031, 1.3), "z0": 27, "z1": 29.5, "wall": "stone", "style": "none", "roof": ("flat", "dark"), "noclip": True}, {"cyl": (0.413, 0.272, 1.3), "z0": 27, "z1": 29.5, "wall": "stone", "style": "none", "roof": ("flat", "dark"), "noclip": True}, {"cyl": (0.568, -0.031, 1.3), "z0": 27, "z1": 29.5, "wall": "stone", "style": "none", "roof": ("flat", "dark"), "noclip": True}, {"cyl": (0.568, 0.272, 1.3), "z0": 27, "z1": 29.5, "wall": "stone", "style": "none", "roof": ("flat", "dark"), "noclip": True}],
    "Robert Schattner Center": [   # the Levy tower (pale piers on dark brick, stair towers) and the five-storey Schattner wing
        {"box": (0.395, 1.0, 0.0, 0.973), "z1": 18, "wall": "brick", "style": "tall", "roof": ("flat", "dark"), "units": 4},
        {"box": (0.644, 0.797, 0.091, 0.188), "z0": 18, "z1": 18.4, "wall": "white", "style": "none", "roof": ("hip", "glass"), "cap": 1.5},
        {"box": (0.0, 0.588, 0.429, 1.0), "z1": 40, "wall": "dbrick", "style": "bays", "roof": ("flat", "dark"), "units": 3,
         "ducts": 2, "cornice": False},
        {"box": (0.0, 0.067, 0.429, 1.0), "z1": 43, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.497, 0.588, 0.429, 0.625), "z1": 45, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.118, 0.464, 0.586, 0.917), "z0": 40, "z1": 43.5, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 2}],
    "way/520723134": [   # 3813-15: two front gables
        {"box": (a, a + 0.4, 0.0, 0.25), "z1": 14, "wall": "brick", "style": "punched", "roof": ("gable_y", "slate"), "slope": 1.2, "ends": True}
        for a in (0.08, 0.52)],
    "way/520723136": [   # Tau Epsilon Phi: polygonal bays up the front, capped with slate
        {"cyl": (x, 0.02, 2.2), "sides": 6, "z1": 12.5, "wall": "dbrick", "style": "tall", "roof": ("cone", "slate"), "slope": 1.3, "noclip": True}
        for x in (0.2, 0.8)],
    "Tabernacle United Church": [   # schist church under red tile: steep nave, transept, chancel, parish house, corner tower
        {"box": (0.066, 0.662, 0.467, 0.994), "z1": 12, "wall": "stone", "style": "tall", "roof": ("gable_y", "tile"), "slope": 1.3, "ends": True},
        {"box": (0.0, 0.714, 0.467, 0.706), "z1": 12, "wall": "stone", "style": "tall", "roof": ("gable_x", "tile"), "slope": 1.2, "ends": True},
        {"box": (0.108, 0.692, 0.142, 0.467), "z1": 11, "wall": "stone", "style": "tall", "roof": ("gable_y", "tile"), "slope": 1.1, "ends": True,
         "dormers": True},
        {"box": (0.692, 1.0, 0.0, 0.216), "z1": 10, "wall": "stone", "style": "paired", "roof": ("hip", "tile"), "dormers": True, "chimneys": 1},
        {"box": (0.22, 0.577, 0.0, 0.144), "z1": 6, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.647, 0.859, 0.872, 1.011), "z1": 36, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "noclip": True, "crenel": True, "units": 0}] + [
        {"cyl": (cx, cy, 0.7), "z0": 36, "z1": 36.2, "wall": "stone", "style": "none", "roof": ("cone", "slate"), "slope": 8, "noclip": True}
        for cx in (0.665, 0.841) for cy in (0.883, 1.001)],      # the tower's four pinnacles
    "Annenberg School of Communication": [   # pale stone, stepping up from the plaza to the Walnut front
        {"box": (0.0, 0.76, 0.55, 1.0), "z1": 18, "wall": "white", "style": "tall", "roof": ("flat", "mid"), "units": 2},
        {"box": (0.25, 0.72, 0.72, 0.98), "z1": 23, "wall": "white", "style": "punched", "roof": ("flat", "mid"), "units": 2}],
    "Annenberg Public Policy Center": [   # big glass hipped skylight over its atrium
        {"box": (0.15, 0.85, 0.22, 0.80), "z0": 15.5, "z1": 15.8, "wall": "white", "style": "none", "roof": ("hip", "glass"), "cap": 3, "cornice": False}],
    "Fisher Bennett Hall": [   # steep pitched wings round a court open to the south; gables face us
        {"box": (0.811, 1.0, 0.0, 1.0), "z1": 15, "wall": "dbrick", "style": "paired", "roof": ("gable_y", "metal"), "slope": 1.0,
         "ends": True, "dormers": True, "chimneys": 2, "units": 0},                                       # east wing
        {"box": (0.329, 0.811, 0.506, 1.0), "z1": 15, "wall": "dbrick", "style": "paired", "roof": ("gable_x", "metal"), "slope": 1.0,
         "dormers": True, "units": 0},                                                                     # north wing on Walnut
        {"box": (0.0, 0.271, 0.0, 0.352), "z1": 15, "wall": "dbrick", "style": "paired", "roof": ("gable_y", "metal"), "slope": 1.0,
         "ends": True, "chimneys": 1, "units": 0}],                                                        # south-west pavilion
    "Lerner Center": [{"box": (0.0, 0.52, 0.0, 1.0), "z1": 14.4, "wall": "brick", "roof": ("hip", "slate"), "units": 0}],  # hipped west half
    "Levine Hall": [
        {"box": (0.0, 1.0, 0.33, 1.0), "z1": 21.6, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 2},
        {"box": (0.353, 0.755, 0.731, 0.951), "z0": 21.6, "z1": 24.5, "wall": "brick", "style": "none", "roof": ("flat", "dark"), "units": 0}],
    "Skirkanich Hall": [
        {"box": (0.16, 0.576, 0.378, 0.924), "z0": 21, "z1": 25, "wall": "metal", "style": "none", "roof": ("flat", "dark"), "units": 3},  # plant penthouse
        {"box": (0.527, 0.772, -0.04, 0.015), "z0": 8, "z1": 14, "wall": "glass", "style": "none", "roof": ("flat", "white"), "noclip": True, "units": 0, "cornice": False}],  # glazed bay
    "Van Pelt Library": [   # entrance vestibule out on the plaza, glass front between pale piers
        {"box": (0.651, 0.702, -0.105, 0.01), "z1": 4.5, "wall": "glass", "style": "none", "roof": ("flat", "glass"), "noclip": True, "units": 0, "cornice": False},
        {"box": (0.638, 0.643, -0.30, -0.10), "z1": 1.2, "wall": "white", "style": "none", "roof": ("flat", "white"), "noclip": True, "units": 0, "cornice": False},  # stair cheek wall
        {"box": (0.722, 0.745, -0.27, -0.10), "z1": 0.9, "wall": "white", "style": "none", "roof": ("flat", "green"), "noclip": True, "units": 0, "cornice": False},  # planter
        {"box": (0.40, 1.0, 0.0, 1.0), "z1": 26, "wall": "dbrick", "style": "bays", "roof": ("flat", "dark"), "units": 3, "bulk": 1, "cornice": False},
        {"box": (0.50, 0.92, 0.30, 0.80), "z0": 26, "z1": 30, "wall": "white", "style": "none", "roof": ("flat", "dark"), "units": 2},
        {"box": (0.60, 0.80, 0.45, 0.65), "z0": 30, "z1": 32, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0}],
    "Franklin Building": [
        {"box": (0.15, 0.45, 0.3, 0.7), "top": 3.5, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.55, 0.85, 0.3, 0.7), "top": 3.5, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0}],
    "3401 Walnut": [
        {"cyl": (0.43, 0.04, 4.5), "z1": 23, "wall": "white", "style": "ribbon", "roof": ("flat", "copper"), "noclip": True, "units": 0},
        {"cyl": (0.90, 0.04, 4.5), "z1": 23, "wall": "white", "style": "ribbon", "roof": ("flat", "copper"), "noclip": True, "units": 0}],
    "University Square": [   # Walnut front over the shops, the Inn at Penn rising mid-block, octagonal corner tower at 36th
        {"box": (0.0, 1.0, 0.0, 0.40), "z0": 3.6, "z1": 22, "wall": "brick", "style": "paired", "roof": ("flat", "dark"),
         "ducts": 4, "units": 3, "bulk": 1, "cornice": True},
        {"box": (0.375, 0.5, 0.0, 0.6), "z0": 3.6, "z1": 27, "wall": "brick", "style": "strips", "roof": ("flat", "dark"), "units": 2},
        {"box": (0.40, 0.475, 0.2, 0.5), "z0": 27, "z1": 30, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"cyl": (0.95, 0.10, 7.0), "sides": 8, "z0": 3.6, "z1": 25, "wall": "white", "style": "ribbon", "roof": ("flat", "dark"),
         "noclip": True, "units": 0}],
    "Pottruck Health and Fitness Center": [
        {"box": (0.098, 0.301, (3.2 + 0.9 * k) / 66.8, 0.355), "z0": 3.6 * k, "z1": 3.6 * (k + 1), "wall": "white", "style": "ribbon",
         "roof": ("flat", "white" if k < 4 else "dark"), "units": 0, "cornice": False} for k in range(5)] + [   # stepped banded front
        {"box": (0.301, 0.392, 0.088, 0.355), "z1": 25, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 1},  # brick shaft
        {"box": (0.392, 0.475, 0.183, 1.0), "z1": 19, "wall": "glass", "style": "none", "roof": ("barrel_y", "glass"), "k": 1.0,
         "ends": True, "units": 0, "cornice": False},                                                 # glass barrel atrium, arched front
        {"box": (0.21, 0.39, 0.66, 0.9), "z0": 18, "z1": 21.5, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.23, 0.36, 0.45, 0.58), "z0": 18, "z1": 20.5, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.475, 1.0, 0.0, 1.0), "z1": 22, "wall": "dbrick", "style": "none", "piers": DB_SIDE, "roof": ("flat", "dark"),
         "grid": True, "units": 1, "bulk": 0},                                                         # the gym block
        {"box": (0.55, 0.97, -0.004, 0.012), "z0": 19.5, "z1": 21, "wall": "glass", "style": "none", "roof": ("flat", "dark"),
         "noclip": True, "units": 0, "cornice": False},                                                # its clerestory band
        {"box": (0.57, 0.67, 0.43, 0.8), "z0": 22, "z1": 25, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.68, 0.74, 0.8, 0.98), "z0": 22, "z1": 25, "wall": "metal", "style": "none", "roof": ("flat", "mid"), "units": 0}],
    "38th Street Garage": [     # from the aerial view: round brick stair tower at 38th and Walnut, brick stair tower on the
        # bump at the east end of the Walnut front, then what stands on the top deck (GARAGE_DECK): the low walls
        # between its tiers, the cooling tower up on its steel trestle, the pipes beside it, a unit, the stair head
        {"cyl": (0.035, 0.07, 5.0), "z1": 21, "wall": "brick", "style": "ribbon", "roof": ("flat", "dark"), "units": 0, "noclip": True},
        {"box": (0.911, 1.0, 0.0, 0.22), "z1": 21, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 3}] + [
        {"box": b, "z0": 18, "z1": 18.9, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0, "cornice": False}
        for b in ((0.09, 0.612, 0.515, 0.527), (0.617, 0.911, 0.5, 0.512), (0.09, 0.285, 0.232, 0.245), (0.285, 0.82, 0.25, 0.262),
                  (0.396, 0.41, 0.527, 1.0), (0.603, 0.615, 0.262, 0.527), (0.283, 0.297, 0.0, 0.262), (0.812, 0.822, 0.512, 0.945))] + [
        {"box": b, "z0": 18, "z1": 19.3, "wall": "tan", "style": "none", "roof": ("flat", "tan"), "units": 0, "cornice": False}
        for b in ((0.756, 0.77, 0.555, 0.94), (0.78, 0.794, 0.555, 0.94), (0.755, 0.905, 0.985, 0.998))] + [
        {"box": (0.642, 0.709, 0.267, 0.307), "z0": 18, "z1": 20.6, "wall": "metal", "style": "none", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.815, 0.911, 0.008, 0.053), "z0": 18, "z1": 20.6, "wall": "metal", "style": "none", "roof": ("flat", "white"), "units": 0},
        {"box": (0.495, 0.74, 0.545, 0.94), "z0": 18, "z1": 21.5, "wall": "tan", "style": "trestle", "roof": ("flat", "dark"), "units": 0, "cornice": False},
        {"box": (0.495, 0.74, 0.545, 0.94), "z0": 21.5, "z1": 31, "wall": "white", "style": "none", "roof": ("flat", "white"),
         "units": 0, "fans": 2, "ladder": True, "cornice": False}],
    "Steinberg Conference Center": [_TURRET(0.04, 0.52, 3.2, 25), _TURRET(0.06, 0.97, 3.2, 25)],
    "Vance Hall": [{"box": (0.22, 0.86, 0.45, 0.82), "top": 3.5, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 2}],
    "McNeil Building": [{"box": (0.1, 0.9, 0.12, 0.88), "top": 4, "wall": "glass", "style": "ribbon", "roof": ("flat", "dark"), "units": 1}],
    "Harrison College House": _HR, "Harnwell College House": _HR, "Rodin College House": _HR,
    "Sansom Place West": [{"box": (0.35, 0.65, 0.3, 0.7), "top": 4, "wall": "white", "style": "none", "roof": ("flat", "dark"), "units": 1}],
    "Irvine Auditorium": [   # octagonal hall on Spruce under a slate pyramid; slate-hipped side wings; spire on the tower (an OSM part)
        {"cyl": (0.494, 0.144, 9.0), "sides": 8, "z1": 22, "wall": "dbrick", "style": "tall", "roof": ("cone", "slate"), "slope": 1.4},
        {"box": (0.0, 0.195, 0.27, 0.87), "z1": 18, "wall": "dbrick", "style": "tall", "roof": ("hip", "slate"), "cap": 4},
        {"box": (0.805, 1.0, 0.27, 0.87), "z1": 18, "wall": "dbrick", "style": "tall", "roof": ("hip", "slate"), "cap": 4},
        {"cyl": (0.492, 0.502, 1.7), "z0": 50, "z1": 50.2, "wall": "stone", "style": "none", "roof": ("cone", "slate"), "slope": 7, "noclip": True}],
    "Fisher Fine Arts Library": [
        {"box": (0.08, 0.26, 0.52, 0.68), "z0": 0, "z1": 33, "wall": "brick", "style": "tall", "roof": ("flat", "dark"), "crenel": True, "units": 0}],
    "Memorial Tower": [_TURRET(0.1, 0.1, 2.0, 31, "brick", "copper", 2.6), _TURRET(0.9, 0.1, 2.0, 31, "brick", "copper", 2.6),
                       _TURRET(0.1, 0.9, 2.0, 31, "brick", "copper", 2.6), _TURRET(0.9, 0.9, 2.0, 31, "brick", "copper", 2.6)],
    "Department of Earth and Environmental Science": [
        {"cyl": (0.5, 0.5, 2.2), "z0": 15, "z1": 22, "wall": "white", "style": "none", "roof": ("dome", "copper"), "noclip": True, "k": 1.0}],
    "Wistar Institute": [   # brick lab along Woodland Walk, Fox glass tower on Spruce, the 1894 building on 36th
        {"box": (0.464, 0.829, 0.013, 0.583), "z1": 34, "wall": "glass", "style": "ribbon", "roof": ("flat", "white"), "units": 6, "bulk": 1},
        {"box": (0.348, 0.464, 0.013, 0.76), "z1": 22, "wall": "metal", "style": "none", "roof": ("flat", "white"), "units": 2},  # grey-clad step
        {"box": (0.829, 1.0, 0.0, 0.88), "z1": 16, "wall": "brick", "style": "tall", "roof": ("hip", "metal"), "cap": 3, "units": 0},  # 1894 building
        {"box": (0.66, 1.0, 0.65, 1.0), "z1": 9, "wall": "brick", "style": "punched", "roof": ("flat", "mid"), "units": 1},          # low north-east wing
        {"box": (0.74, 0.79, 0.76, 0.83), "z0": 9, "z1": 9.3, "wall": "white", "style": "none", "roof": ("hip", "glass"), "cap": 2},  # its pyramid skylight
        {"box": (0.16, 0.30, 0.22, 0.40), "z0": 15, "z1": 15.2, "wall": "white", "style": "none", "roof": ("flat", "green"), "units": 0, "cornice": False}],
    "Weiss Pavillion": [
        {"box": (0.0, 1.0, 0.62, 1.0), "z1": 26, "wall": "brick", "style": "arches", "roof": ("flat", "seats"), "stands": True}],
    "Lauder College House": [   # C around Hill Square: tall north and west wings, stepping down to the wedge at the tip
        {"box": (0.0, 1.0, 0.60, 1.0), "z1": 25.2, "wall": "dbrick", "roof": ("flat", "green"), "units": 0},       # north wing, 4 bays
        {"box": (0.0, 0.07, 0.33, 1.0), "z1": 25.2, "wall": "dbrick", "roof": ("flat", "green"), "units": 0},      # west wing
        {"box": (0.0, 0.298, 0.0, 0.34), "z1": 18, "wall": "dbrick", "roof": ("flat", "green"), "units": 0},       # south-west block
        {"box": (0.349, 0.5, 0.0, 0.34), "z1": 9, "wall": "glass", "style": "none", "roof": ("flat", "green"), "units": 0, "cornice": False},  # wedge
        # entrance: pale frame standing proud of the wedge, two-storey glass entry, paved terrace over the garden roof
        {"box": (0.298, 0.353, 0.03, 0.34), "z1": 10.5, "wall": "white", "style": "none", "roof": ("flat", "green"), "noclip": True, "units": 0},
        {"box": (0.31, 0.343, 0.024, 0.05), "z1": 7.2, "wall": "glass", "style": "none", "roof": ("flat", "white"), "noclip": True, "units": 0, "cornice": False},
        {"box": (0.298, 0.353, 0.23, 0.34), "z0": 10.5, "z1": 10.6, "wall": "white", "style": "none", "roof": ("flat", "tan"), "noclip": True, "units": 0, "cornice": False},
        {"box": (0.07, 0.10, 0.33, 0.63), "z1": 22, "wall": "glass", "style": "none", "roof": ("flat", "glass"), "noclip": True, "units": 0},
    ] + [   # pale stair towers between the bays, standing proud of the facade and a storey above the roof
        {"box": (a - 0.025, a + 0.025, b, 1.0), "z1": z, "wall": "white", "style": "strips", "roof": ("flat", "white"),
         "noclip": True, "cornice": False, "units": 0}
        for a, b, z in ((0.258, 0.583, 28.5), (0.462, 0.583, 28.5), (0.663, 0.583, 28.5), (0.875, 0.583, 28.5))] + [
        {"box": (0.04, 0.085, -0.03, 0.34), "z1": 21, "wall": "white", "style": "strips", "roof": ("flat", "white"),
         "noclip": True, "cornice": False, "units": 0}],
    "Chemistry Laboratories 1973 Wing": [
        {"box": (0.3, 0.7, 0.3, 0.7), "top": 4, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 2}],
    "Steinberg-Dietrich Hall": [
        {"box": (0.08, 0.32, 0.55, 0.95), "top": 4, "wall": "white", "style": "ribbon", "roof": ("flat", "white"), "units": 1}],
    "David Rittenhouse Laboratories": [   # low and stepped: a 1-storey base under everything (BLD h), wings on it
        {"box": (0.154, 0.589, 0.731, 1.0), "z1": 15, "wall": "dbrick", "style": "punched", "roof": ("flat", "dark"), "units": 1},   # north wing, west half
        {"box": (0.589, 1.001, 0.701, 1.0), "z1": 15, "wall": "dbrick", "style": "ribbon", "roof": ("flat", "dark"), "units": 1},    # ... east half
        {"box": (0.296, 0.565, 0.807, 0.974), "z0": 15, "z1": 18.5, "wall": "white", "style": "ribbon", "roof": ("flat", "dark"), "units": 1},  # clerestory penthouses
        {"box": (0.676, 0.985, 0.792, 0.974), "z0": 15, "z1": 18.5, "wall": "white", "style": "ribbon", "roof": ("flat", "dark"), "units": 1},
        {"box": (0.565, 0.668, 0.761, 0.989), "z0": 15, "z1": 21.5, "wall": "white", "style": "none", "roof": ("flat", "dark"), "units": 0},    # mechanical tower
        {"box": (0.003, 0.154, 0.67, 1.0), "z1": 11, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 1},    # Walnut/33rd corner block
        {"box": (0.003, 0.439, 0.442, 0.731), "z1": 8.5, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 2},   # west wing
        {"box": (0.201, 0.296, 0.518, 0.67), "z0": 8.5, "z1": 12, "wall": "white", "style": "none", "roof": ("flat", "dark"), "units": 0},     # ... its frame tower
        {"box": (0.154, 0.439, 0.305, 0.442), "z1": 7, "wall": "brick", "style": "ribbon", "roof": ("flat", "dark"), "units": 0},       # Math/Physics/Astronomy library clerestory
        {"box": (0.003, 0.154, 0.002, 0.381), "z1": 9, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 1},     # south-west corner block
        {"box": (0.074, 0.106, -0.009, 0.008), "z1": 9, "wall": "glass", "style": "none", "roof": ("flat", "dark"), "noclip": True, "units": 0, "cornice": False},  # its glass stair
        {"box": (0.439, 0.589, 0.002, 0.731), "z1": 13, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 1},    # north-south spine
        {"box": (0.549, 0.581, -0.011, 0.008), "z1": 13, "wall": "glass", "style": "none", "roof": ("flat", "dark"), "noclip": True, "units": 0, "cornice": False},  # its glass stair
        {"box": (0.447, 0.502, 0.564, 0.67), "z0": 13, "z1": 16, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0},     # white hood on the spine
        {"box": (0.589, 0.771, 0.062, 0.473), "z1": 7, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 1},      # south-east block
        {"box": (0.629, 0.732, 0.153, 0.336), "z0": 7, "z1": 10.5, "wall": "brick", "style": "ribbon", "roof": ("flat", "dark"), "units": 1}],  # ... and its raised box
    "Hill College House": [   # one-storey glass clerestory over the central atrium, exactly between the two wells
        {"box": (0.278, 0.717, 0.252, 0.775), "top": 3.6, "wall": "glass", "style": "none", "roof": ("flat", "white"), "units": 4, "cornice": False}],
    "Gutmann College House": [   # two green-roofed brick wings round a garden, joined by a low link, and the tower on Walnut
        {"box": (0.0, 0.272, -0.003, 0.82), "z1": 18, "wall": "brick", "style": "punched", "roof": ("flat", "green"), "units": 0},   # west wing on 40th
        {"box": (0.0, 0.376, 0.596, 0.82), "z1": 18, "wall": "brick", "style": "punched", "roof": ("flat", "green"), "units": 0},   # ... and beside the court
        {"box": (0.77, 1.001, -0.003, 0.82), "z1": 18, "wall": "brick", "style": "punched", "roof": ("flat", "green"), "units": 0},   # east wing
        {"box": (0.691, 1.001, 0.581, 0.82), "z1": 18, "wall": "brick", "style": "punched", "roof": ("flat", "green"), "units": 0},   # ... and beside the court
        {"box": (0.103, 0.265, 0.53, 0.596), "z1": 22, "wall": "metal", "style": "none", "roof": ("flat", "mid"), "units": 0},        # metal stair towers
        {"box": (0.773, 0.916, 0.491, 0.582), "z1": 22, "wall": "metal", "style": "none", "roof": ("flat", "mid"), "units": 0},
        {"box": (0.184, 0.862, 0.818, 1.002), "z1": 48, "wall": "dpanel", "style": "punched", "roof": ("flat", "green"), "units": 2, "bulk": 1},  # the tower
        {"box": (0.42, 0.62, 0.86, 0.96), "z0": 48, "z1": 51.5, "wall": "white", "style": "none", "roof": ("flat", "green"), "units": 0}],
    "Walnut Street West": [{"box": (0.4, 0.67, y0, y1), "z0": 9, "z1": 9.3, "wall": "white", "style": "none", "roof": ("barrel_x", "light"), "k": 0.5}
                           for y0, y1 in ((0.13, 0.31), (0.34, 0.52), (0.55, 0.73))],   # its three barrel skylights
    "Saint Mary's Church": [
        {"box": (0.12, 0.80, 0.60, 0.90), "z0": 0, "z1": 12, "wall": "stone", "style": "tall", "roof": ("gable_x", "slate"), "slope": 1.15, "dormers": True},
        {"box": (0.46, 0.70, 0.46, 0.96), "z0": 0, "z1": 12, "wall": "stone", "style": "tall", "roof": ("gable_y", "slate"), "slope": 1.15},
        {"box": (0.12, 0.27, 0.84, 1.0), "z0": 0, "z1": 22, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "crenel": True, "units": 0},
        {"box": (0.0, 0.92, 0.0, 0.30), "z0": 0, "z1": 9, "wall": "stone", "style": "paired", "roof": ("gable_x", "slate"), "slope": 0.9}],
    "Saints Agatha and James Catholic Church": [
        {"box": (0.0, 0.30, 0.86, 1.0), "z0": 0, "z1": 32, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.70, 1.0, 0.86, 1.0), "z0": 0, "z1": 32, "wall": "stone", "style": "tall", "roof": ("flat", "dark"), "units": 0},
        {"cyl": (0.15, 0.93, 3.4), "z0": 32, "z1": 32.2, "wall": "stone", "style": "none", "roof": ("cone", "slate"), "slope": 2.8, "noclip": True},
        {"cyl": (0.85, 0.93, 3.4), "z0": 32, "z1": 32.2, "wall": "stone", "style": "none", "roof": ("cone", "slate"), "slope": 2.8, "noclip": True},
        {"cyl": (0.5, 0.07, 8.5), "z0": 0, "z1": 14, "wall": "stone", "style": "tall", "roof": ("dome", "slate"), "k": 0.6, "noclip": True}],
    "The Chestnut": [
        {"box": (0.0, 1.0, 0.30, 1.0), "top": 8, "wall": "glass", "style": "fins", "roof": ("flat", "white"), "units": 4, "bulk": 1}],
    "Verizon Building": [
        {"box": (0.12, 0.88, 0.15, 0.88), "top": 3.5, "wall": "stone", "style": "none", "roof": ("flat", "mid"), "units": 2}],
    "40th Street Parking Lot": [
        {"cyl": (1.0, 0.0, 6.0), "z0": 0, "z1": 31, "wall": "glass", "style": "ribbon", "roof": ("flat", "dark"), "noclip": True, "units": 0}],
    "Tanenbaum Hall": [   # six-storey main block behind the low Sansom wing, diamond of pyramid skylights on top
        {"box": (0.0, 1.0, 0.22, 1.0), "z1": 21, "wall": "dbrick", "style": "punched", "roof": ("flat", "dark"), "units": 2},
        {"box": (0.2, 0.41, 0.71, 0.9), "z0": 21, "z1": 24, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.1, 0.24, 0.34, 0.51), "z0": 21, "z1": 23.5, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 0},
        {"box": (0.0, 0.11, 0.88, 1.0), "z0": 21, "z1": 24, "wall": "dbrick", "style": "none", "roof": ("flat", "dark"), "units": 0}] + [
        {"box": (0.47 + 0.11 * i, 0.55 + 0.11 * i, 0.52 + 0.1 * j, 0.6 + 0.1 * j), "z0": 21, "z1": 21.3, "wall": "white", "style": "none",
         "roof": ("hip", "glass"), "cap": 1.2} for i in range(3) for j in range(3)],
    "Golkin Hall": [   # Penn Carey Law on Sansom: white penthouse, raised green-roofed east block, glass entry mid-facade
        {"box": (0.12, 0.40, 0.25, 0.40), "z0": 14, "z1": 14.4, "wall": "white", "style": "none", "roof": ("flat", "glass"), "units": 0, "cornice": False},
        {"box": (0.42, 0.65, 0.30, 1.0), "z0": 14, "z1": 18, "wall": "white", "style": "ribbon", "roof": ("flat", "white"), "units": 1},
        {"box": (0.65, 0.96, 0.20, 1.0), "z1": 18, "wall": "dbrick", "style": "paired", "roof": ("flat", "green"), "units": 0}] + [
        {"box": (a, a + 0.03, 0.5, 0.85), "z0": 18, "z1": 18.3, "wall": "white", "style": "none", "roof": ("barrel_y", "glass"), "k": 0.6}
        for a in (0.74, 0.83)] + [
        {"box": (0.45, 0.58, -0.07, 0.0), "z1": 5, "wall": "glass", "style": "none", "roof": ("flat", "white"), "noclip": True, "units": 0, "cornice": False}],
    "Gittis Hall": [{"box": (0.08, 0.36, 0.2, 0.95), "z1": 16, "wall": "dbrick", "style": "punched", "roof": ("flat", "dark"), "units": 1}],
    "Silverman Hall": [   # tall brick chimney stacks rising out of the slate
        {"box": (x, x + 0.06, y, y + 0.045), "z1": 25, "wall": "brick", "style": "none", "roof": ("flat", "white"), "units": 0, "cornice": False}
        for x, y in ((0.31, 0.81), (0.52, 0.81), (0.43, 0.47), (0.64, 0.47))],
    "English College House": [   # narrow white-roofed bar on the west, stepped up in the middle, with a planted terrace beside it
        {"box": (0.115, 0.497, 0.75, 1.0), "z1": 16, "wall": "dbrick", "style": "punched", "roof": ("flat", "white"), "units": 1},
        {"box": (0.115, 0.497, 0.47, 0.75), "z1": 19, "wall": "dbrick", "style": "punched", "roof": ("flat", "white"), "units": 2},
        {"box": (0.115, 0.497, 0.12, 0.47), "z1": 16, "wall": "dbrick", "style": "punched", "roof": ("flat", "white"), "units": 1},
        {"box": (0.0, 0.115, 0.12, 1.0), "z1": 13, "wall": "dbrick", "style": "punched", "roof": ("flat", "green"), "units": 0},
        {"cyl": (0.77, 0.58, 4.0), "z0": 6, "z1": 8.5, "wall": "glass", "style": "none", "roof": ("cone", "slate"), "slope": 0.8, "noclip": True},  # lantern
        {"cyl": (0.706, 0.026, 3.0), "z1": 9, "wall": "white", "style": "ribbon", "roof": ("flat", "dark"), "noclip": True, "units": 0}],   # round stair
    "King's Court": [   # C of brick blocks round a planted court: tall at Chestnut and 36th, lower along 36th and Sansom
        {"box": (0.0, 1.0, 0.765, 1.0), "z1": 24, "wall": "dbrick", "style": "punched", "roof": ("flat", "dark"), "units": 1},
        {"box": (0.0, 0.466, 0.254, 0.765), "z1": 18, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 1},
        {"box": (0.0, 1.0, 0.0, 0.254), "z1": 16, "wall": "brick", "style": "punched", "roof": ("flat", "dark"), "units": 1}],
    "3401 Walnut": [
        {"cyl": (0.47, 0.0, 3.6), "z1": 22, "wall": "white", "style": "ribbon", "roof": ("flat", "white"), "noclip": True, "units": 0},
        {"cyl": (0.965, 0.0, 3.6), "z1": 22, "wall": "white", "style": "ribbon", "roof": ("flat", "white"), "noclip": True, "units": 0},
        {"box": (0.38, 0.58, 0.0, 0.28), "top": 0.3, "wall": "white", "style": "none", "roof": ("gable_x", "copper"), "shed": True, "slope": 0.35},
        {"box": (0.86, 1.0, 0.0, 0.28), "top": 0.3, "wall": "white", "style": "none", "roof": ("gable_x", "copper"), "shed": True, "slope": 0.35}],
    "Franklin Building": [
        {"box": (a, a + 0.2, b, b + 0.17), "top": 3.2, "wall": "white", "style": "none", "roof": ("flat", "white"), "units": 0}
        for a in (0.27, 0.55) for b in (0.30, 0.56)],
}


ROAD_W = {"primary": 13, "secondary": 11, "tertiary": 9, "residential": 8,
          "unclassified": 7, "service": 4}
DRIVE = ("primary", "secondary", "tertiary", "residential", "unclassified")   # streets the client's cars drive
CAR_HEADINGS = 16
BREEZE_LEVELS = (1, 2)   # how far each breeze frame blows the crowns east, in pixels (their tops twice as far)
PARK_M, LANE_MIN_M, LANE_ROOM_M = 2.2, 2.6, 2.8    # parking strip; narrowest lane; lane width that leaves room to park


def is_oneway(t):
    return t.get("oneway") in ("yes", "true", "1", "-1") or t.get("junction") == "roundabout"


def oriented(pts, t):
    """A way's nodes in the direction its traffic flows (oneway=-1 ways are drawn backwards in OSM)."""
    return pts[::-1] if t.get("oneway") == "-1" else pts


def split_crossings(edges, nodes, on_land):
    """Streets that cross on the same level without sharing an OSM node (like the synthetic west end of
    Chestnut) get a junction where they cross, so cars meet there like at any intersection. Where one passes
    over the other (a bridge), returns the spot instead: [x, y, radius, upper layer], so the client can hide
    cars passing underneath."""
    def hit(p1, p2, p3, p4):
        rx, ry = p2[0] - p1[0], p2[1] - p1[1]; sx, sy = p4[0] - p3[0], p4[1] - p3[1]
        den = rx * sy - ry * sx
        if abs(den) < 1e-9: return None
        u = ((p3[0] - p1[0]) * sy - (p3[1] - p1[1]) * sx) / den
        v = ((p3[0] - p1[0]) * ry - (p3[1] - p1[1]) * rx) / den
        return (u, v) if 0.02 < u < 0.98 and 0.02 < v < 0.98 else None
    cuts = {i: [] for i in range(len(edges))}
    bridges = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            ei, ej = edges[i], edges[j]
            if {ei["a"], ei["b"]} & {ej["a"], ej["b"]}: continue
            for si, (p1, p2) in enumerate(zip(ei["pts"], ei["pts"][1:])):
                for sj, (p3, p4) in enumerate(zip(ej["pts"], ej["pts"][1:])):
                    uv = hit(p1, p2, p3, p4)
                    if not uv: continue
                    x, y = p1[0] + (p2[0] - p1[0]) * uv[0], p1[1] + (p2[1] - p1[1]) * uv[0]
                    if ei["layer"] != ej["layer"]:
                        top = ei if ei["layer"] > ej["layer"] else ej
                        bridges.append([round(x, 1), round(y, 1), round(top["_half"] + 5 * PX_PER_M, 1), top["layer"]])
                        continue
                    nodes.append([round(x, 1), round(y, 1), int(on_land(x, y)), round(max(ei["_half"], ej["_half"]), 1)])
                    k = len(nodes) - 1
                    cuts[i].append((si, uv[0], k)); cuts[j].append((sj, uv[1], k))
    out = []
    for i, e in enumerate(edges):
        base = {k: v for k, v in e.items() if not k.startswith("_") and k not in ("a", "b", "pts")}
        start, pts, a = 0, [e["pts"][0]], e["a"]
        for si, u, k in sorted(cuts[i]):
            pts += e["pts"][start + 1:si + 1]
            pts.append(nodes[k][:2])
            out.append({"a": a, "b": k, **base, "pts": pts})
            start, pts, a = si, [nodes[k][:2]], k
        out.append({"a": a, "b": e["b"], **base, "pts": pts + e["pts"][start + 1:]})
    return out, bridges


def lane_layout(t):
    """How a drivable way's drawn width splits into travel lanes and curb parking, from its lanes tags where it
    has them. Offsets are meters right of the centerline, facing the way's traffic: fwd lanes curb-side first;
    back lanes (two-way streets) curb-side first, as offsets right of the centerline facing *their* traffic;
    park_fwd/park_back the parking strip each direction pulls into, if any; and lines, the paint between strips."""
    W = ROAD_W[t["highway"]] * 0.85
    oneway = is_oneway(t)
    num = lambda k: int(t[k]) if t.get(k, "").isdigit() else None
    if oneway:
        fwd, back = num("lanes") or (2 if t["highway"] in ("primary", "secondary") else 1), 0
    elif num("lanes:forward") and num("lanes:backward"):
        fwd, back = num("lanes:forward"), num("lanes:backward")
    elif (num("lanes") or 0) >= 2:
        fwd, back = (num("lanes") + 1) // 2, num("lanes") // 2
    else:
        fwd, back = 1, 1
    # OSM's lane counts can outgrow the width the map draws a street at; keep every lane wide enough for a car.
    cap = max(1 if oneway else 2, int(W // LANE_MIN_M))
    while fwd + back > cap:
        if back > fwd and back > 1: back -= 1
        elif fwd > 1: fwd -= 1
        else: break
    no_park = any(v in ("no", "no_parking", "no_stopping") for k, v in t.items() if k.startswith("parking"))
    n = fwd + back
    sides = 0 if no_park else next((p for p in (2, 1) if W - p * PARK_M >= n * LANE_ROOM_M), 0)
    lane = (W - sides * PARK_M) / n
    strips = ([("park", PARK_M)] if sides == 2 else []) + [("back", lane)] * back + [("fwd", lane)] * fwd \
        + ([("park", PARK_M)] if sides >= 1 else [])
    out = {"fwd": [], "back": [], "park_fwd": None, "park_back": None, "lines": []}
    x = -W / 2
    for i, (kind, w) in enumerate(strips):
        center = x + w / 2
        if kind == "fwd": out["fwd"].insert(0, center)
        elif kind == "back": out["back"].append(-center)
        elif i == len(strips) - 1: out["park_fwd"] = center
        elif back: out["park_back"] = -center
        x += w
        if i + 1 < len(strips):
            nxt = strips[i + 1][0]
            line = "edge" if "park" in (kind, nxt) else "center" if kind != nxt else "lane"
            out["lines"].append((x, line))
    return out
PATH_W = {"pedestrian": 6, "footway": 2, "path": 2, "steps": 2, "cycleway": 2}


def parse(path):
    root = ET.parse(path).getroot()
    nodes, trees, points = {}, [], {"bench": [], "bicycle_parking": [], "street_lamp": []}
    for n in root.iter("node"):
        ll = (float(n.get("lat")), float(n.get("lon")))
        nodes[n.get("id")] = ll
        tg = {t.get("k"): t.get("v") for t in n.iter("tag")}
        if tg.get("natural") == "tree": trees.append(ll)
        for k in (tg.get("amenity"), tg.get("highway")):
            if k in points: points[k].append(ll)
    ways = {}
    for w in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        ways[w.get("id")] = ([nodes[r.get("ref")] for r in w.iter("nd") if r.get("ref") in nodes], tags)
    rels = []
    for r in root.iter("relation"):
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        rels.append((r.get("id"), [(m.get("ref"), m.get("role")) for m in r.iter("member") if m.get("type") == "way"], tags))
    return nodes, ways, rels, trees, points


def stitch(segments):
    rings, segs = [], [list(s) for s in segments if len(s) > 1]
    while segs:
        ring, changed = segs.pop(0), True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]: ring += s[1:]
                elif s[-1] == ring[-1]: ring += s[::-1][1:]
                elif s[-1] == ring[0]: ring = s[:-1] + ring
                elif s[0] == ring[0]: ring = s[::-1][:-1] + ring
                else: continue
                segs.pop(i); changed = True; break
        if len(ring) >= 4: rings.append(ring)
    return rings


def chance(x, y, salt):
    """A stable 0..1 draw for a spot on the map. Procedural details use this instead of a shared random stream, so
    what lands at one spot never depends on what was drawn before it (an edit stays local)."""
    return zlib.crc32(f"{salt}:{int(x)}:{int(y)}".encode()) / 2 ** 32


def height_m(osm_id, tags):
    if osm_id in HEIGHT_OVERRIDES: return HEIGHT_OVERRIDES[osm_id]
    if tags.get("name") in HEIGHT_BY_NAME: return HEIGHT_BY_NAME[tags["name"]]
    for k in ("height", "building:height"):
        try: return float(tags[k].replace("m", "").strip())
        except (KeyError, ValueError): pass
    try: return float(tags["building:levels"]) * LEVEL_M
    except (KeyError, ValueError): return DEFAULT_M


def main(src, out_dir, list_only=False):
    nodes, ways, rels, trees, points = parse(src)
    lat0 = statistics.mean(p[0] for p in nodes.values())
    lon0 = statistics.mean(p[1] for p in nodes.values())
    kx, ky = 111320 * math.cos(math.radians(lat0)), 111132

    # rotation: Locust Walk -> horizontal
    walk = [p for pts, t in ways.values() if t.get("name") == "Locust Walk" for p in pts]
    w0, w1 = min(walk, key=lambda p: p[1]), max(walk, key=lambda p: p[1])
    ang = math.atan2((w1[0] - w0[0]) * ky, (w1[1] - w0[1]) * kx)
    ca, sa = math.cos(ang), math.sin(ang)

    def R(ll):  # lat/lon -> rotated meters (x east along Locust, y north)
        x, y = (ll[1] - lon0) * kx, (ll[0] - lat0) * ky
        return (x * ca + y * sa, -x * sa + y * ca)

    def inv_R(x, y):              # rotated map meters -> lat/lon
        ex, ny_ = x * ca - y * sa, x * sa + y * ca
        return (ny_ / ky + lat0, ex / kx + lon0)

    def street_coord(name, axis):
        vals = [R(p)[axis] for pts, t in ways.values() if t.get("name") == name and "highway" in t for p in pts]
        return statistics.median(vals)

    yC, yW, yS = (street_coord(n, 1) for n in ("Chestnut Street", "Walnut Street", "Spruce Street"))
    # The export's north edge clips Chestnut west of 38th; extend its westernmost piece straight on.
    ch = [pts for pts, t in ways.values() if t.get("name") == "Chestnut Street" and t.get("highway")]
    if ch:
        west_end = min((p for pts in ch for p in pts), key=lambda p: R(p)[0])
        wx, wy = R(west_end)
        ways["synthetic-chestnut-west"] = ([inv_R(wx - 700, wy), west_end],
                                           {"highway": "primary", "name": "Chestnut Street", "oneway": "yes"})
    x40, x38, x36, x34, x33 = (street_coord(n, 0) for n in ("South 40th Street", "South 38th Street",
                               "South 36th Street", "South 34th Street", "South 33rd Street"))
    crop = [(x40 - 55, yC + 12),
            (x34 - 8, yC + 12), (x34 - 8, yC + 44), (x34 + 87, yC + 44), (x34 + 87, yC + 12),   # notch for Amy Gutmann Hall
            (x33 + 17, yC + 12), (x33 + 17, yW + 5), (x33 + 207, yW + 5),
            (x33 + 207, yS - 113), (x34, yS - 113), (x34, yS - 14), (x36 + 15, yS - 14),
            (x36 + 15, yS - 163), (x38, yS - 163),
            (x38, yS - 14), (x40 - 55, yS - 14)]
    # The notch's corners don't set the canvas's north edge: AGH stands in the headroom kept above Chestnut for
    # tall roofs, so the canvas (and every pixel already on it) stays put.
    NOTCH = {2, 3}
    pad = 18  # meters of off-map border around the island
    west, east = min(p[0] for p in crop) - pad, max(p[0] for p in crop) + pad
    south, north = min(p[1] for p in crop) - pad, max(p[1] for i, p in enumerate(crop) if i not in NOTCH)

    # Land beyond the street-aligned outline, unioned in. These neither push the outline's edges nor grow the canvas,
    # so whatever they reach has to fit in the off-map margin already there (and the rest of the map stays put).
    land_extra = [[(x40 - 92, yS + 8), (x40 - 30, yS + 8), (x40 - 30, yS + 132), (x40 - 92, yS + 132)]]   # Penn Dental, west of 40th

    def inside(x, y, poly=None):
        poly = poly or crop
        hit, j = False, len(poly) - 1
        for i in range(len(poly)):
            (xi, yi), (xj, yj) = poly[i], poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi: hit = not hit
            j = i
        return hit

    # buildings in the crop
    raw = []
    for wid, (pts, t) in ways.items():
        if "way/" + wid in ADD_BUILDINGS: t = dict(t, building="yes")
        if "building" in t and len(pts) >= 4 and pts[0] == pts[-1]:
            if "way/" + wid in NAME_FIX: t = dict(t, name=NAME_FIX["way/" + wid])
            raw.append(("way/" + wid, [pts], t))
    HOLES = {}
    for rid, members, t in rels:
        if "building" in t:
            rings = stitch([ways[r][0] for r, role in members if role in ("outer", "") and r in ways])
            if rings:
                raw.append(("relation/" + rid, rings, t))
                HOLES["relation/" + rid] = stitch([ways[r][0] for r, role in members if role == "inner" and r in ways])
    buildings, via_extra = [], set()
    for osm_id, rings, t in raw:
        cx = statistics.mean(R(p)[0] for p in rings[0]); cy = statistics.mean(R(p)[1] for p in rings[0])
        if t.get("name") in EXCLUDE: continue
        if any(inside(cx, cy, ex) for ex in land_extra):      # extra land first: these mustn't push the outline
            buildings.append((osm_id, rings, t)); via_extra.add(osm_id)
        elif inside(cx, cy):
            buildings.append((osm_id, rings, t))
    if list_only:
        for n in sorted({t.get("name") for _, _, t in buildings if t.get("name")}): print(n)
        return
    # Keep the border made of straight street-aligned edges, but push each edge outward just
    # far enough that every selected building sits on the map with a few meters to spare.
    bb = []
    for oid_, rings, _t in buildings:
        if oid_ in via_extra: continue
        pm = [R(p) for p in rings[0]]
        bb.append((min(p[0] for p in pm), max(p[0] for p in pm), min(p[1] for p in pm), max(p[1] for p in pm),
                   statistics.mean(p[0] for p in pm), statistics.mean(p[1] for p in pm)))
    crop_area = sum(crop[i][0] * crop[(i + 1) % len(crop)][1] - crop[(i + 1) % len(crop)][0] * crop[i][1] for i in range(len(crop)))
    MARGIN_M = 5
    for _it in range(3):
        n = len(crop)
        for i in range(n):
            (x1, y1), (x2, y2) = crop[i], crop[(i + 1) % n]
            dx, dy = x2 - x1, y2 - y1
            ox_, oy_ = (dy, -dx) if crop_area > 0 else (-dy, dx)
            if abs(dy) < 1e-6:
                lo, hi = sorted((x1, x2)); sg = 1 if oy_ > 0 else -1
                over = [(b[3] - y1) if sg > 0 else (y1 - b[2]) for b in bb
                        if b[1] > lo + 0.5 and b[0] < hi - 0.5 and ((b[5] < y1) if sg > 0 else (b[5] > y1))]
                need = max(over + [-1e9]) + MARGIN_M
                if need > 0:
                    crop[i] = (x1, y1 + sg * need); crop[(i + 1) % n] = (x2, y2 + sg * need)
            else:
                lo, hi = sorted((y1, y2)); sg = 1 if ox_ > 0 else -1
                over = [(b[1] - x1) if sg > 0 else (x1 - b[0]) for b in bb
                        if b[3] > lo + 0.5 and b[2] < hi - 0.5 and ((b[4] < x1) if sg > 0 else (b[4] > x1))]
                need = max(over + [-1e9]) + MARGIN_M
                if need > 0:
                    crop[i] = (x1 + sg * need, y1); crop[(i + 1) % n] = (x2 + sg * need, y2)
    # An edge running along a street mustn't slice it lengthwise: push it out past the far curb and a sidewalk.
    # Only streets parallel to the edge count, so the ones running off the map end at the border as before.
    SIDE_M = 6
    along = []          # (x, y, half width, unit direction) every metre along each street
    for pts, t in ways.values():
        hw = t.get("highway")
        if hw not in ROAD_W or hw == "service" or t.get("tunnel") or t.get("layer", "0").startswith("-"): continue
        half = ROAD_W[hw] * 0.85 / 2
        rp = [R(p) for p in pts]
        for (ax, ay), (bx, by) in zip(rp, rp[1:]):
            ln = math.hypot(bx - ax, by - ay)
            for k in range(int(ln) + 1):
                f = k / max(ln, 1e-9)
                along.append((ax + (bx - ax) * f, ay + (by - ay) * f, half, ((bx - ax) / ln, (by - ay) / ln) if ln else (0, 0)))
    n = len(crop)
    bounds = (min(p[0] for p in crop), max(p[0] for p in crop), min(p[1] for p in crop),
              max(p[1] for i, p in enumerate(crop) if i not in NOTCH))
    for i in range(n):
        (x1, y1), (x2, y2) = crop[i], crop[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        ox_, oy_ = (dy, -dx) if crop_area > 0 else (-dy, dx)
        horiz = abs(dy) < 1e-6
        on_bound = (y1 if horiz else x1) in bounds     # these may only reach into the off-map margin around the canvas
        lo, hi = sorted((x1, x2) if horiz else (y1, y2)); c0 = y1 if horiz else x1
        sg = (1 if oy_ > 0 else -1) if horiz else (1 if ox_ > 0 else -1)
        need = 0
        for px_, py_, half, (ux, uy) in along:
            u, v = (px_, py_) if horiz else (py_, px_)
            if not (lo + 1 < u < hi - 1) or abs(ux if horiz else uy) < 0.8: continue
            reach, s = half + SIDE_M, sg * (v - c0)          # s: how far past the edge the centreline runs
            if -reach < s < half + 0.7: need = max(need, s + reach)    # cut, or its sidewalk is (wholly-off streets stay off)
        if on_bound: need = min(need, pad - 4)
        if need > 0.5:                                  # a street just grazing the edge isn't worth a jog
            if horiz: crop[i] = (x1, y1 + sg * need); crop[(i + 1) % n] = (x2, y2 + sg * need)
            else: crop[i] = (x1 + sg * need, y1); crop[(i + 1) % n] = (x2 + sg * need, y2)
            print(f"border edge {i} pushed {need:.1f} m off a street")
    west, east, south, north = bounds[0] - pad, bounds[1] + pad, bounds[2] - pad, bounds[3]   # the canvas ignores that push

    S = PX_PER_M
    tallest = max(height_m(i, t) for i, _, t in buildings)
    top_pad = int(tallest * HEIGHT_K * S) + 10
    W = int((east - west) * S)
    H = int((north - south) * S * GROUND_Y) + top_pad + 8

    def Q(x, y): return ((x - west) * S, (north - y) * S * GROUND_Y + top_pad)
    def P(ll): return Q(*R(ll))
    # The base under the island hangs below its lowest edge: the canvas grows downward to hold it (never upward,
    # so every pixel above stays where it was).
    H0 = H          # the lawn texture is laid out on this height, so growing the canvas can't reshuffle it
    H = max(H, int(max(Q(*p)[1] for poly in [crop] + land_extra for p in poly)) + int(round(BASE_M * HEIGHT_K * S)) + 12)

    img = Image.new("L", (W, H), GROUND)
    d = ImageDraw.Draw(img)

    # ---- ground ----------------------------------------------------------
    for pts, t in ways.values():
        if len(pts) < 3 or pts[0] != pts[-1]: continue
        poly = [P(p) for p in pts]
        if t.get("landuse") == "flowerbed":
            d.polygon(poly, fill=FLOWER)
        elif t.get("landuse") in ("grass", "recreation_ground") or t.get("leisure") in ("park", "garden"):
            d.polygon(poly, fill=GRASS)
        elif t.get("amenity") == "parking" and "building" not in t and t.get("parking") != "multi-storey":
            rc_ = [R(p) for p in pts[:-1]]; mx_, my_ = sum(p[0] for p in rc_) / len(rc_), sum(p[1] for p in rc_) / len(rc_)
            if inside(mx_, my_) or any(inside(mx_, my_, ex) for ex in land_extra):   # an off-map lot leaves no sliver
                d.polygon(poly, fill=PARKING, outline=CURB)
        elif t.get("highway") == "pedestrian" and t.get("area") == "yes":
            d.polygon(poly, fill=PATH)
        elif t.get("leisure") == "track":
            d.polygon(poly, fill=TRACK)
        elif t.get("leisure") == "pitch":
            d.polygon(poly, fill=PITCH)
            d.line(poly, fill=MARK, width=max(1, int(0.3 * S)))
        elif t.get("natural") == "water":
            d.polygon(poly, fill=WATER)

    bnames = {oid: tt.get("name") for oid, _r, tt in buildings}
    for oid, hl in HOLES.items():        # building courtyards are planted
        if bnames.get(oid) in HOLE_FILL: continue
        for h in hl: d.polygon([P(p) for p in h], fill=GROUND if bnames.get(oid) in PAVED_COURTS else GRASS)
    for pts, t in ways.values():   # the stadium bowl reads as running track around the field
        if t.get("leisure") == "stadium" and len(pts) > 3:
            d.polygon([P(p) for p in pts], fill=TRACK)
    for pts, t in ways.values():   # tracks mapped as lines
        if t.get("leisure") == "track" and (len(pts) < 3 or pts[0] != pts[-1] or t.get("area") == "no"):
            d.line([P(p) for p in pts], fill=TRACK, width=int(9 * S), joint="curve")
    football = Image.new("1", (W, H), 0)
    for pts, t in ways.values():
        if t.get("leisure") == "pitch" and len(pts) > 3 and pts[0] == pts[-1] and t.get("sport") != "basketball":
            pl = [P(p) for p in pts]
            d.polygon(pl, fill=PITCH); d.line(pl, fill=MARK, width=max(1, int(0.3 * S)))
            if "american_football" in t.get("sport", "") and len(pts) == 5:
                # end zones and yard lines, measured along the field's long axis
                ImageDraw.Draw(football).polygon(pl, fill=1)
                cm = [R(p) for p in pts[:4]]
                e01 = math.dist(cm[0], cm[1]); e12 = math.dist(cm[1], cm[2])
                c0, c1, c3 = (cm[0], cm[1], cm[3]) if e01 > e12 else (cm[1], cm[2], cm[0])
                u = (c1[0] - c0[0], c1[1] - c0[1]); v = (c3[0] - c0[0], c3[1] - c0[1])
                at = lambda a, b: Q(c0[0] + u[0] * a + v[0] * b, c0[1] + u[1] * a + v[1] * b)
                for a0, a1 in ((0.0, 1 / 12), (11 / 12, 1.0)):
                    d.polygon([at(a0, 0), at(a1, 0), at(a1, 1), at(a0, 1)], fill=ENDZONE)
                for k in range(1, 21):
                    a = 1 / 12 + (10 / 12) * k / 20
                    d.line([at(a, 0.02), at(a, 0.98)], fill=YARD, width=2 if k % 2 == 0 else 1)
                for a in (1 / 12, 11 / 12):
                    d.line([at(a, 0), at(a, 1)], fill=YARD, width=2)
                d.line([at(0, 0), at(1, 0), at(1, 1), at(0, 1), at(0, 0)], fill=YARD, width=2)

    def drawable(t):
        return not t.get("tunnel") and not t.get("layer", "0").startswith("-")

    for layer in ("footway", "pedestrian", "steps"):
        for pts, t in ways.values():
            hw = t.get("highway")
            if hw not in PATH_W or not drawable(t) or t.get("area") == "yes": continue
            if (hw == "pedestrian") != (layer == "pedestrian") or (hw == "steps") != (layer == "steps"): continue
            d.line([P(p) for p in pts], fill={"pedestrian": PATH, "steps": STEPS}.get(hw, GROUND),
                   width=max(1, int(PATH_W[hw] * S * 0.8)), joint="curve")
    for pts, t in ways.values():   # curbs first, so each street gets a lighter edge
        hw = t.get("highway")
        if hw in ROAD_W and drawable(t):
            d.line([P(p) for p in pts], fill=CURB, width=int(ROAD_W[hw] * S * 0.85) + 2 * max(2, int(0.35 * S)), joint="curve")
    # Streets up on bridges, so footbridges' sidewalks along them don't count as passing over them.
    raised_im = Image.new("1", (W, H), 0); rd = ImageDraw.Draw(raised_im)
    for pts, t in ways.values():
        hw = t.get("highway")
        if hw in ROAD_W and drawable(t):
            d.line([P(p) for p in pts], fill=ROAD, width=int(ROAD_W[hw] * S * 0.85), joint="curve")
            if t.get("bridge") == "yes" or t.get("layer", "0") not in ("0", ""):
                rd.line([P(p) for p in pts], fill=1, width=int(ROAD_W[hw] * S * 0.85) + 2 * max(2, int(0.35 * S)), joint="curve")
    raised = np.array(raised_im)
    inc = {}
    for pts, t in ways.values():
        hw = t.get("highway")
        if hw not in ROAD_W or hw == "service" or not drawable(t): continue
        for i, p in enumerate(pts):
            for st in (-1, 1):
                if 0 <= i + st < len(pts): inc.setdefault(p, []).append((pts, i, st, hw))
    XN = {p: max(ROAD_W[e[3]] for e in nb) * S * 0.85 / 2 for p, nb in inc.items() if len(nb) >= 3}

    def walk(pts, i, st, dist, stop_at_junction=False):
        """Point and direction `dist` screen pixels along a way, starting at node i, stepping st.
        With stop_at_junction, give up if another intersection comes first (e.g. the far half of a
        divided street), so no crosswalk lands inside an intersection."""
        x0, y0 = P(pts[i]); j = i
        while 0 <= j + st < len(pts):
            x1, y1 = P(pts[j + st]); L = math.hypot(x1 - x0, y1 - y0)
            if L > 1e-6:
                if dist <= L:
                    return x0 + (x1 - x0) * dist / L, y0 + (y1 - y0) * dist / L, (x1 - x0) / L, (y1 - y0) / L
                dist -= L
            x0, y0 = x1, y1; j += st
            if stop_at_junction and pts[j] in XN: return None
        return None
    # lane paint from each street's lane layout (the same one traffic.json gives the cars): dashed lines between
    # lanes going the same way, a center line between directions (doubled on the big streets), and a solid
    # line along each parking strip. It stops short of intersections, where the crosswalks go.
    for pts, t in ways.values():
        hw = t.get("highway")
        if hw not in DRIVE or not drawable(t): continue
        pts = oriented(pts, t)
        lines = lane_layout(t)["lines"]
        sp = [P(p) for p in pts]
        for k_, ((x1, y1), (x2, y2)) in enumerate(zip(sp, sp[1:])):
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 1: continue
            ux, uy = (x2 - x1) / L, (y2 - y1) / L
            a0 = XN[pts[k_]] + 5 * S if pts[k_] in XN else 0.0
            a1 = L - XN[pts[k_ + 1]] - 5 * S if pts[k_ + 1] in XN else L
            if a1 <= a0: continue
            for off_m, kind in lines:
                offs = [off_m * S - 1, off_m * S + 1] if kind == "center" and hw in ("primary", "secondary") \
                    else [off_m * S]
                dash, gap = {"center": (3 * S, 3 * S), "lane": (2 * S, 4 * S), "edge": (L, 0)}[kind]
                if kind == "center" and len(offs) == 2: dash, gap = L, 0
                for off in offs:
                    k = a0
                    while k < a1:
                        b = min(k + dash, a1)
                        d.line([(x1 + ux * k - uy * off, y1 + uy * k + ux * off),
                                (x1 + ux * b - uy * off, y1 + uy * b + ux * off)], fill=MARK, width=1)
                        k = b + gap if gap else a1

    # crosswalks: one band per approach at every intersection, set back past the cross street,
    # measured along the street itself so short OSM segments near the corner don't matter
    xings = []
    for node, nbrs in inc.items():
        if len(nbrs) < 3: continue
        nx0, ny0 = P(node)
        if not (0 <= nx0 < W and 0 <= ny0 < H): continue
        xings.append((nx0, ny0))
        dirs = []
        for (pts_, i_, st_, hw_) in nbrs:
            w0 = walk(pts_, i_, st_, 2.0)
            dirs.append(None if w0 is None else (w0[2], w0[3]))
        for idx, (pts_, i_, st_, hw_) in enumerate(nbrs):
            if dirs[idx] is None: continue
            ux, uy = dirs[idx]
            hpx_ = ROAD_W[hw_] * S * 0.85 / 2
            other = max([ROAD_W[nbrs[j][3]] * S * 0.85 / 2 for j in range(len(nbrs))
                         if j != idx and dirs[j] is not None and abs(dirs[j][0] * ux + dirs[j][1] * uy) < 0.7] + [0])
            if other == 0: continue
            band = 1.6 * S * (GROUND_Y if abs(uy) > abs(ux) else 1.0)
            if walk(pts_, i_, st_, other + 1.2 * S + 2 * band + 4 * S, stop_at_junction=True) is None: continue
            w1 = walk(pts_, i_, st_, other + 1.2 * S + band)
            if w1 is None: continue
            cx, cy, ux, uy = w1
            px_, py_ = -uy, ux
            k = -hpx_ + 0.7 * S
            while k <= hpx_ - 0.7 * S:
                sx, sy = cx + px_ * k, cy + py_ * k
                d.line([(sx - ux * band, sy - uy * band), (sx + ux * band, sy + uy * band)],
                       fill=CROSS, width=max(2, int(0.5 * S)))
                k += 1.0 * S
    # Footbridges, drawn over the streets they cross. They're painted flat, so the depth test never puts them in
    # front of a car; `overpass` records them for the traffic occluders, so cars drive under instead of over.
    overpass_im = Image.new("1", (W, H), 0); od = ImageDraw.Draw(overpass_im)
    for pts, t in ways.values():
        hw = t.get("highway")
        if hw in PATH_W and (t.get("bridge") == "yes" or t.get("layer", "0") in ("1", "2")):
            sp = [P(p) for p in pts]; w = max(2, int(PATH_W[hw] * S * 0.8))
            d.line(sp, fill=OUTLINE, width=w + 2 * int(S * 0.5))
            d.line(sp, fill=PATH, width=w)
            od.line(sp, fill=1, width=w + 2 * int(S * 0.5))
    # Not where they run beside a raised street (a bridge's own sidewalks), nor just north of one, where its cars'
    # roofs stick up.
    overpass = np.array(overpass_im) & ~binary_dilation(raised, np.ones((31, 9), bool))

    # ground island: the straight crop outline
    fpmask = Image.new("1", (W, H), 0); fd = ImageDraw.Draw(fpmask)
    for _, rings, _t in buildings:
        for ring in rings: fd.polygon([P(p) for p in ring], fill=1)
    F = np.array(fpmask)
    cm = Image.new("1", (W, H), 0)
    for poly in [crop] + land_extra: ImageDraw.Draw(cm).polygon([Q(*p) for p in poly], fill=1)
    land = np.array(cm)                           # straight, street-aligned border
    a = np.array(img)
    pre = a.copy()                                # the ground as drawn everywhere, before the island is cut out
    a[~land] = OFFMAP
    yy0, xx0 = np.mgrid[0:H, 0:W]
    drop = int(round(BASE_M * HEIGHT_K * S))
    # The base: the island's cut edge, a slab BASE_M deep. Only faces turned to the camera show: the south ones,
    # and a sliver of the east ones where the lean carries the base's foot out from under the island.
    depth = np.zeros((H, W), np.int16); src = np.zeros((H, W), np.uint8)
    inner = binary_erosion(land, iterations=3)               # what the island is made of, inside its border line
    for k in range(1, drop + 1):
        dx = int(round(-LEAN_X * k))
        sh = np.zeros_like(land); sh[k:, dx:] = land[:H - k, :W - dx]
        new = sh & ~land & (depth == 0)
        depth[new] = k
        ys_, xs_ = np.nonzero(new)
        sy_ = np.clip(ys_ - k - 3, 0, H - 1); sx_ = np.clip(xs_ - dx, 0, W - 1)
        src[ys_, xs_] = np.where(inner[sy_, sx_], pre[sy_, sx_], GROUND)
    face = depth > 0
    straight = np.zeros_like(land)
    for k in range(1, drop + 1): straight[k:] |= land[:H - k]
    east = face & ~straight
    road_top = np.isin(src, [ROAD, CURB, PARKING, CROSS, MARK])
    green_top = np.isin(src, [GRASS, GRASS_DARK, PITCH, FLOWER])
    # Layers, with wavy boundaries: the surface, a gravel bed under roads or dark topsoil under the rest, red clay,
    # a deeper darker clay, then bedrock.
    wob = np.sin(xx0 / 9.0) * 1.5 + np.sin(xx0 / 3.7 + 1.3) * 0.8
    f_ = (depth + wob) / drop
    a[face] = SOIL_FRONT
    a[face & (f_ > 0.55)] = SOIL_DEEP
    a[face & (f_ > 0.78)] = STONE_SIDE
    a[face & (f_ > 0.78) & (np.abs(f_ - 0.9) < 0.012) & ((xx0 // 5) % 3 != 0)] = OUTLINE      # bedding planes in the rock
    a[face & (depth > 2) & (depth <= 6) & ~road_top] = SOIL_SIDE
    a[face & (depth > 2) & (depth <= 6) & road_top] = GRAVEL
    a[face & (depth > 7) & (f_ < 0.78) & (((xx0 * 7919 + yy0 * 104729) ^ (xx0 * yy0)) % 61 == 0)] = DECK   # pebbles
    a[face & (depth <= 2)] = DECK
    a[face & (depth <= 2) & road_top] = ROAD
    a[face & (depth <= 2) & green_top] = GRASS_DARK
    a[east] = SOIL_SIDE
    a[east & (depth > drop * 0.78)] = STONE_SIDE
    a[face & (depth == drop)] = OUTLINE
    fe = face & ~binary_erosion(face | land)
    a[fe] = OUTLINE
    # Pipe ends under the streets and sidewalks, where the cut runs through them. Each spot's draws are its own (chance), so nothing else moves when one changes.
    feat = Image.new("L", (W, H), 255); fd2 = ImageDraw.Draw(feat)
    full = face & ~east
    tops = np.argwhere(full & (depth == 1))
    for ty, tx in tops:
        if ty + drop >= H or not full[ty + drop - 1, tx]: continue
        sm = src[ty + 1, tx]
        if tx % 7 == 0 and sm not in (GRASS, GRASS_DARK, PITCH, FLOWER) and chance(tx, ty, "pipe") < 0.06:
            rr = random.Random(zlib.crc32(f"pipe:{tx}:{ty}".encode()))
            r_ = rr.choice((3, 4, 4, 5)); yc = ty + int(drop * rr.uniform(0.25, 0.58)); ln = rr.randint(1, 3)
            # solid colours that stand off the clay: pale concrete, green copper, dark iron (never terracotta)
            body, rim, hi = rr.choice(((CONC_SIDE, CONC_FRONT, CONC_FRONT), (COP_DARK, COP_MID, COP_LIT), (OUTLINE, METAL, CONC_FRONT)))
            fd2.ellipse([tx - r_ - 1, yc + ln - r_ + 2, tx + r_ + 1, yc + ln + r_ + 3], fill=SOIL_DEEP)    # its shadow
            for e_ in (1, 0):                        # the barrel coming out of the face, then its open end
                for yo in range(ln + 1):
                    fd2.ellipse([tx - r_ - e_, yo + yc - r_ - e_, tx + r_ + e_, yo + yc + r_ + e_], fill=OUTLINE if e_ else body)
            ye = yc + ln
            fd2.ellipse([tx - r_, ye - r_, tx + r_, ye + r_], fill=rim)
            fd2.ellipse([tx - r_ + 1, ye - r_ + 1, tx + r_ - 1, ye + r_ - 1], fill=OUTLINE)
            fd2.arc([tx - r_, ye - r_, tx + r_, ye + r_], 190, 260, fill=hi)        # light on the rim's upper left
            if rr.random() < 0.35:                     # a drip down the face below it
                for k in range(2, rr.randint(5, 10)): fd2.point((tx, ye + r_ + k), fill=WATER if k % 2 else OUTLINE)
    fa = np.array(feat)
    fm = fa != 255
    a[fm & ~land] = fa[fm & ~land]
    a[land & (edt(land) <= 2)] = OUTLINE
    img = Image.fromarray(a); d = ImageDraw.Draw(img)
    for poly, look in GROUND_PATCH:     # groves are lawn the canopy plants trees in, so they go down before it's laid out
        if look == "grove": d.polygon([Q(*p) for p in poly], fill=GRASS)
    a = np.array(img)
    groundmat = a.copy()
    near_bldg = edt(~F) < 2.5 * S
    # Clipped hedges along the lawn side of each building's south face, in the deep green of the planting beds.
    north_of = np.zeros_like(F)
    for k in range(1, 7): north_of[k:] |= F[:-k]
    yy_h, xx_h = np.mgrid[0:H, 0:W]
    hedge = land & ~F & np.isin(a, [GRASS, GROUND]) & north_of & (edt(~F, sampling=(1 / GROUND_Y, 1)) <= 1.1 * S)
    hedge &= (a == GRASS) | ((xx_h // int(5 * S)) % 3 != 1)       # on paving, broken every few metres for doors and paths
    a2 = a.copy(); a2[hedge] = GRASS_DARK; img.paste(Image.fromarray(a2))
    # Hand-placed ground goes on after groundmat is copied, so the procedural canopy that reads it
    # (and every tree laid out after it) stays exactly where it was.
    looks = {"pavers": PATH, "deck": DECK, "paving": DECK, "planting": GRASS_DARK, "lawn": GRASS, "grove": GRASS, "steps": STEPS, "art": CONC_FRONT, "hole": OUTLINE}
    on_top = ("steps", "deck", "art", "hole")        # these go over the painted ways, beds and paving go under them
    for poly, look in GROUND_PATCH:
        if look not in on_top: d.polygon([Q(*p) for p in poly], fill=looks[look])
    for wid, (wm, look) in WAY_PAINT.items():
        if wm == 0: d.polygon([P(p) for p in ways[wid][0]], fill=looks[look])
    for wid, (wm, look) in WAY_PAINT.items():
        sp = [P(p) for p in ways[wid][0]]; w = int(wm * S * 0.8)
        if look == "deck" and wm: d.line(sp, fill=OUTLINE, width=w + 2 * int(S * 0.5))
    for wid, (wm, look) in WAY_PAINT.items():
        if wm: d.line([P(p) for p in ways[wid][0]], fill=looks[look], width=int(wm * S * 0.8), joint="curve")
    for poly, look in GROUND_PATCH:
        if look in on_top: d.polygon([Q(*p) for p in poly], fill=looks[look], outline=OUTLINE if look == "art" else None)
    # Surface lots: perpendicular stalls along each long side, painted, about two in three holding a parked car.
    # (Street traffic is the client's; cars in lots never move, so they're drawn in.)
    lot_cars = []
    for pts, t in ways.values():
        if t.get("amenity") != "parking" or "building" in t or t.get("parking") == "multi-storey" or len(pts) < 4 or pts[0] != pts[-1]:
            continue
        lot = SPoly([R(p) for p in pts]).buffer(0)
        if lot.area < 120: continue
        inner = lot.buffer(-0.4)
        x0_, y0_, x1_, y1_ = lot.bounds
        ns = (y1_ - y0_) >= (x1_ - x0_)                  # long axis north-south: stalls along the west and east sides
        lo, hi = (y0_, y1_) if ns else (x0_, x1_)
        sides = ((x0_ + 2.9, x1_ - 2.9) if ns else (y0_ + 2.9, y1_ - 2.9))
        if ((x1_ - x0_) if ns else (y1_ - y0_)) < 13: sides = sides[:1]
        for c0 in sides:
            a = lo + 1.2
            while a + 2.6 <= hi - 1.2:
                cx_, cy_ = (c0, a + 1.3) if ns else (a + 1.3, c0)
                corners_ = [Q(c0 + u_, a + v_) if ns else Q(a + v_, c0 + u_) for u_ in (-2.6, 2.6) for v_ in (0, 2.6)]
                on_map = all(0 <= int(qx_) < W and 0 <= int(qy_) < H and land[int(qy_), int(qx_)]   # whole stalls only,
                             for qx_, qy_ in corners_)                                             # where a lot runs off the map
                if on_map and inner.contains(SPoint(cx_, cy_)):
                    for b_ in (a, a + 2.6):
                        ends = ((c0 - 2.6, b_), (c0 + 2.6, b_)) if ns else ((b_, c0 - 2.6), (b_, c0 + 2.6))
                        d.line([Q(*ends[0]), Q(*ends[1])], fill=MARK)
                    sx_, sy_ = Q(cx_, cy_)
                    if chance(sx_, sy_, "lot") < 0.65:
                        spec = (1.0, 0.0, 4.5 * S, 1.9 * S) if ns else (0.0, 1.0, 4.5 * S * GROUND_Y, 1.9 * S)
                        lot_cars.append((sx_, sy_, spec, CAR_PAINTS[int(chance(sx_, sy_, "paint") * len(CAR_PAINTS))]))
                a += 2.6
    clear_im = Image.new("1", (W, H), 0)
    for poly in TREE_CLEAR: ImageDraw.Draw(clear_im).polygon([Q(*p) for p in poly], fill=1)
    tree_clear = np.array(clear_im)

    # building shadows on the ground (sun from the upper left)
    shimg = Image.new("1", (W, H), 0); sd = ImageDraw.Draw(shimg)
    for osm_id, rings, t in buildings:
        hpx = height_m(osm_id, t) * HEIGHT_K * S
        sx, sy = 0.45 * hpx, 0.12 * hpx
        for ring in rings:
            fp = [P(p) for p in ring]
            sd.polygon(fp, fill=1)
            for (x1, y1), (x2, y2) in zip(fp, fp[1:]):
                sd.polygon([(x1, y1), (x2, y2), (x2 + sx, y2 + sy), (x1 + sx, y1 + sy)], fill=1)

    # procedural canopy: jittered grid on lawns, plus street trees along sidewalks
    rng = random.Random(7)
    extra = []
    step = TREE_SPACING_M * S
    gy = 0.0
    while gy < H0:      # H0, not H: the base growing the canvas mustn't add draws ahead of the street trees
        gx = 0.0
        while gx < W:
            x = int(gx + rng.uniform(-0.4, 0.4) * step); y = int(gy + rng.uniform(-0.4, 0.4) * step * GROUND_Y)
            if 0 <= x < W and 0 <= y < H and groundmat[y, x] == GRASS and not near_bldg[y, x] and chance(x, y, "lawn") < 0.6:
                extra.append((x, y))
            gx += step
        gy += step * GROUND_Y
    for pts, t in ways.values():
        if t.get("footway") != "sidewalk": continue
        sp = [P(p) for p in pts]
        for (x1, y1), (x2, y2) in zip(sp, sp[1:]):
            L = math.hypot(x2 - x1, y2 - y1); k = rng.uniform(0, 9 * S)
            while k < L:
                x, y = int(x1 + (x2 - x1) * k / L), int(y1 + (y2 - y1) * k / L)
                if 0 <= x < W and 0 <= y < H and land[y, x] and groundmat[y, x] in (GROUND, GRASS, PATH, CURB) and not near_bldg[y, x]:
                    extra.append((x, y))
                k += rng.uniform(8, 12) * S
    # trees lining the campus walks, on both sides
    for pts, t in ways.values():
        hw = t.get("highway")
        if hw not in ("footway", "pedestrian") or t.get("footway") in ("sidewalk", "crossing") or not drawable(t): continue
        sp = [P(p) for p in pts]; off = (PATH_W[hw] / 2 + 2.5) * S
        for (x1, y1), (x2, y2) in zip(sp, sp[1:]):
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 1: continue
            nx, ny = -(y2 - y1) / L, (x2 - x1) / L
            k = rng.uniform(0, 8 * S)
            while k < L:
                bx, by = x1 + (x2 - x1) * k / L, y1 + (y2 - y1) * k / L
                for sgn in (1, -1):
                    x, y = int(bx + sgn * nx * off), int(by + sgn * ny * off * GROUND_Y)
                    if 0 <= x < W and 0 <= y < H and land[y, x] and groundmat[y, x] in (GROUND, GRASS) \
                            and not near_bldg[y, x] and chance(x, y, "walk") < 0.7:
                        extra.append((x, y))
                k += rng.uniform(8, 11) * S
    for x, y in extra:
        if tree_clear[y, x]: continue
        sd.ellipse([x, y - 1.2 * S, x + 6 * S, y + 1.2 * S], fill=1)

    # ---- street lamps along sidewalks -----------------------------------------
    lamps = [P(ll) for ll in points["street_lamp"]]
    for pts, t in ways.values():
        if t.get("footway") != "sidewalk": continue
        sp = [P(p) for p in pts]
        for (x1, y1), (x2, y2) in zip(sp, sp[1:]):
            L = math.hypot(x2 - x1, y2 - y1); k = rng.uniform(0, 20 * S)
            while k < L:
                x, y = int(x1 + (x2 - x1) * k / L), int(y1 + (y2 - y1) * k / L)
                if 0 <= x < W and 0 <= y < H and land[y, x] and not near_bldg[y, x]:
                    lamps.append((x, y))
                k += 24 * S

    # ---- buildings with a depth buffer -------------------------------------------
    # Every building/tree pixel stores the ground-plane y of the point it shows ("foot y").
    # Larger foot y = closer to the camera, so occlusion is exact regardless of draw order.
    A = np.array(img)
    # Street surface, for the traffic occluders: road plus its paint (not the paint on sports fields).
    road_ground = (A == ROAD) | (np.isin(A, [MARK, CROSS]) & (edt(A != ROAD) <= 3))
    D = np.full((H, W), -1e9, np.float32)
    # Which building window each pixel shows (0 for none), so night lights switch whole windows and only real ones.
    WID = np.zeros((H, W), np.int32)
    window_count = [0]
    rng2 = random.Random(11)
    HPX = HEIGHT_K * S                     # screen pixels per meter of height

    def to_m(sx, sy):                      # screen ground point -> rotated map meters
        return sx / S + west, north - (sy - top_pad) / (S * GROUND_Y)

    def composite(patch, x0, y0, depth, tol=0.5, A=A, D=D, ids=None, WID=WID):
        """Draw patch where it's in front; `ids` (window ids per patch pixel) go into WID, and anything else drawn
        clears WID under it. Pass WID=None when drawing into another scene than the main one."""
        h_, w_ = patch.shape
        xa, ya = max(0, x0), max(0, y0); xb, yb = min(W, x0 + w_), min(H, y0 + h_)
        if xb <= xa or yb <= ya: return None
        p = patch[ya - y0:yb - y0, xa - x0:xb - x0]; dp = depth[ya - y0:yb - y0, xa - x0:xb - x0]
        sub = D[ya:yb, xa:xb]
        ok = (p != 255) & (dp >= sub - tol)
        A[ya:yb, xa:xb][ok] = p[ok]; sub[ok] = dp[ok]
        if WID is not None:
            WID[ya:yb, xa:xb][ok] = ids[ya - y0:yb - y0, xa - x0:xb - x0][ok] if ids is not None else 0
        return ok, xa, ya

    def canvas(pts_):
        xs = [p[0] for p in pts_]; ys = [p[1] for p in pts_]
        x0, y0 = int(math.floor(min(xs))) - 2, int(math.floor(min(ys))) - 2
        w_, h_ = int(math.ceil(max(xs))) - x0 + 3, int(math.ceil(max(ys))) - y0 + 3
        im = Image.new("L", (max(w_, 1), max(h_, 1)), 255)
        return im, ImageDraw.Draw(im), x0, y0

    def box_sprite(sx, sy, foot, w, d_, h, top, side, front=None):
        """A small upright box standing at screen point (sx, sy) (its front-left foot)."""
        front = front if front is not None else side
        fr = [(sx, sy), (sx + w, sy), (sx + w + LEAN_X * h, sy - h), (sx + LEAN_X * h, sy - h)]
        tp = [fr[3], fr[2], (fr[2][0], fr[2][1] - d_), (fr[3][0], fr[3][1] - d_)]
        im, dd, bx, by = canvas(fr + tp)
        dd.polygon([(x - bx, y - by) for x, y in fr], fill=front, outline=OUTLINE)
        dd.polygon([(x - bx, y - by) for x, y in tp], fill=top, outline=OUTLINE)
        p = np.array(im)
        composite(p, bx, by, np.full(p.shape, foot, np.float32))

    # ---------- walls ----------
    def wall(p1, p2, z0, z1, mat, front, style, fpx, opt):
        (x1, y1), (x2, y2) = p1, p2
        dx, dy = x2 - x1, y2 - y1
        den = dx + LEAN_X * dy
        if abs(den) < 0.3: return
        corners = [(x1 + LEAN_X * z0, y1 - z0), (x2 + LEAN_X * z0, y2 - z0), (x2 + LEAN_X * z1, y2 - z1), (x1 + LEAN_X * z1, y1 - z1)]
        im, dd, ox_, oy_ = canvas(corners)
        L_ = lambda pts_: [(x - ox_, y - oy_) for x, y in pts_]
        dd.polygon(L_(corners), fill=mat, outline=OUTLINE)
        L = math.hypot(dx, dy)
        idim = Image.new("I", im.size, 0); idd = ImageDraw.Draw(idim)

        def win(poly, m, lit=True):
            """One window: drawn as glass, and tagged with its own id for the night lights unless nothing behind
            it could be lit (returning 0 then)."""
            dd.polygon(poly, fill=m)
            if not lit: return 0
            window_count[0] += 1
            idd.polygon(poly, fill=window_count[0])
            return window_count[0]

        def quad(a, b, v0, v1):
            ax, ay = x1 + dx * a, y1 + dy * a; bx, by = x1 + dx * b, y1 + dy * b
            return L_([(ax + LEAN_X * v0, ay - v0), (bx + LEAN_X * v0, by - v0), (bx + LEAN_X * v1, by - v1), (ax + LEAN_X * v1, ay - v1)])

        def hline(v, m_, wdt=1):
            dd.line(L_([(x1 + LEAN_X * v, y1 - v), (x2 + LEAN_X * v, y2 - v)]), fill=m_, width=wdt)
        wmat = opt.get("wmat", WINDOW)
        ground_floor = z0 == 0
        if ground_floor and opt.get("base") and fpx >= 6:          # stone base course
            dd.polygon(quad(0, 1, 0, min(fpx, z1 - z0) * 0.98), fill=opt["base"])
            hline(min(fpx, z1 - z0), CORNICE)
        if opt.get("belts") and fpx >= 6:                         # belt course every few floors
            f = 3
            while z0 + f * fpx < z1 - fpx * 0.5:
                hline(z0 + f * fpx, CORNICE); f += 3
        if opt.get("cornice") and L > 2 * S:
            hline(z1 - 2, CORNICE, max(1, int(0.3 * S)))
        if opt.get("ladder") and front:
            q = quad(0.49, 0.49, z0, z1); q2 = quad(0.51, 0.51, z0, z1)
            dd.line([q[0], q[3]], fill=OUTLINE); dd.line([q2[0], q2[3]], fill=OUTLINE)
            for v_ in range(int(z0), int(z1), 3):
                r_ = quad(0.49, 0.51, v_, v_); dd.line([r_[0], r_[1]], fill=OUTLINE)
        if opt.get("piers") and L > 4 * S:                        # pilasters breaking up long walls
            n = int(L / (7 * S))
            for j in range(1, n):
                a = j / n
                dd.polygon(quad(a - 0.25 * S / L, a + 0.25 * S / L, z0, z1), fill=opt["piers"])
        if style == "bays" and fpx >= 6:   # framed bays (Van Pelt): glazed ground floor, brick panel with a
            nb = max(1, round(L / (8.4 * S)))  # grid of small windows, clerestory strip, pale piers floor to roof
            pw = min(0.5 * S / L, 0.08 / nb); pier = CONC_FRONT if front else CONC_SIDE
            g = min(4.5 * HPX, (z1 - z0) * 0.22) if ground_floor else 0
            top = min(2.6 * HPX, (z1 - z0) * 0.14)
            if g: win(quad(0, 1, z0, z0 + g), WINDOW)
            win(quad(0, 1, z1 - top, z1 - 0.5 * HPX), WINDOW)
            for k in range(1, int(L / (1.4 * S))):                     # clerestory mullions
                q = quad(k * 1.4 * S / L, k * 1.4 * S / L, z1 - top, z1 - 0.5 * HPX); dd.line([q[0], q[3]], fill=GLARE)
            pv0, pv1 = z0 + g + 0.8 * HPX, z1 - top - 0.8 * HPX
            rows = max(1, int((pv1 - pv0) / (2.6 * HPX))); cols = 5
            for j in range(nb):
                a0, a1 = j / nb + pw, (j + 1) / nb - pw
                hw_ = min(0.4 * S / L, (a1 - a0) / cols * 0.3)
                for c in range(cols):
                    cx = a0 + (a1 - a0) * (c + 0.5) / cols
                    for r in range(rows):
                        v = pv0 + (pv1 - pv0) * (r + 0.3) / rows
                        win(quad(cx - hw_, cx + hw_, v, v + (pv1 - pv0) / rows * 0.45), WINDOW)
            for j in range(nb + 1):
                a = j / nb
                dd.polygon(quad(max(0, a - pw), min(1, a + pw), z0, z1), fill=pier)
            hline(z1 - 1, pier, 2)
            style = "none"
        if style == "garage" and fpx >= 6:   # parking deck (38th Street Garage): shopfronts under green awnings, then
            g = min(4.5 * HPX, (z1 - z0) * 0.3) if ground_floor else 0     # a deck per floor, each bay's two red
            if g:                                                          # mesh screens framed in cream between brick piers
                win(quad(0, 1, z0 + 0.3 * HPX, z0 + g - 1.1 * HPX), WINDOW)
                for k in range(1, int(L / (3 * S))):
                    q = quad(k * 3 * S / L, k * 3 * S / L, z0 + 0.3 * HPX, z0 + g - 1.1 * HPX); dd.line([q[0], q[3]], fill=OUTLINE)
                dd.polygon(quad(0, 1, z0 + g - 1.1 * HPX, z0 + g - 0.4 * HPX), fill=AWNING)
                hline(z0 + g, CONC_FRONT if front else CONC_SIDE, 2)
            top = 1.2 * HPX
            lv = max(1, round((z1 - top - z0 - g) / (3.1 * HPX))); lh = (z1 - top - z0 - g) / lv
            nb = max(1, round(L / (8.5 * S))); pw = min(0.45 * S / L, 0.1 / nb)
            frame = CONC_FRONT if front else CONC_SIDE
            for j in range(nb):
                a0, a1 = j / nb + pw, (j + 1) / nb - pw
                for half_ in range(2):
                    b0 = a0 + (a1 - a0) * half_ / 2 + 0.15 * S / L; b1 = a0 + (a1 - a0) * (half_ + 1) / 2 - 0.15 * S / L
                    for f_ in range(lv):
                        v0 = z0 + g + f_ * lh + 0.35 * HPX; v1 = z0 + g + (f_ + 1) * lh - 0.35 * HPX
                        dd.polygon(quad(b0, b1, v0, v1), fill=frame)
                        dd.polygon(quad(b0 + 0.25 * S / L, b1 - 0.25 * S / L, v0 + 0.5, v1 - 1), fill=DB_FRONT if front else DB_SIDE)
            hline(z1 - 1, frame, 2)
            style = "none"
        if style == "trestle":               # steel legs and cross-bracing over the dark gap under a cooling tower
            dd.polygon(L_(corners), fill=OUTLINE)
            nb = max(1, round(L / (5 * S)))
            for j in range(nb + 1):
                q = quad(j / nb, j / nb, z0, z1); dd.line([q[0], q[3]], fill=mat, width=2)
                if j < nb:
                    q2 = quad((j + 1) / nb, (j + 1) / nb, z0, z1); dd.line([q[0], q2[3]], fill=mat); dd.line([q[3], q2[0]], fill=mat)
            hline(z1 - 1, mat, 2)
            style = "none"
        if style == "vfins" and fpx >= 6:   # Amy Gutmann Hall: dark glass behind vertical fins, lifted on V columns
            g = min(7 * HPX, (z1 - z0) * 0.3) if ground_floor else 0          # over a glazed ground floor
            if g:
                win(quad(0, 1, z0, z0 + g), WINDOW)
                nb = max(1, round(L / (9 * S)))
                for j in range(nb):
                    am = (j + 0.5) / nb
                    if front and am > 0.33:                                  # brick piers between the Vs toward the east
                        dd.polygon(quad(j / nb - 0.12 / nb, j / nb + 0.12 / nb, z0, z0 + g), fill=WALL_FRONT)
                    foot = quad(am, am, z0, z0)[0]
                    for a in (am - 0.32 / nb, am + 0.32 / nb):
                        dd.line([foot, quad(a, a, z0 + g, z0 + g)[0]], fill=CONC_FRONT, width=2)
                dd.polygon(quad(0, 1, z0 + g, z0 + g + 0.7 * HPX), fill=TAN_FRONT)   # wood soffit
            for k in range(1, int(L / (1.5 * S))):
                q = quad(k * 1.5 * S / L, k * 1.5 * S / L, z0 + g + 0.7 * HPX, z1 - 1)
                dd.line([q[0], q[3]], fill=GLARE if k % 2 else CONC_SIDE)
            style = "none"
        n_win = int(L / (3.5 * S))
        rngw = random.Random(round(x1) * 100003 + round(y1))
        if fpx >= 6 and n_win >= 1 and style != "none":
            half = min(0.7 * S, 0.3 * L / n_win) / L
            f = 0
            while z0 + (f + 1) * fpx <= z1 + 0.5:
                b = z0 + f * fpx
                v0, v1 = b + fpx * 0.28, b + fpx * 0.78
                if f == 0 and ground_floor and opt.get("store"):       # shopfronts
                    win(quad(0.03, 0.97, b + fpx * 0.08, b + fpx * 0.85), WINDOW)
                    for j in range(1, max(2, int(L / (3 * S)))):
                        a = j / max(2, int(L / (3 * S)))
                        dd.line([quad(a, a, b + fpx * 0.08, b + fpx * 0.85)[k] for k in (0, 3)], fill=GLARE)
                    dd.polygon(quad(0.03, 0.97, b + fpx * 0.85, b + fpx * 0.98), fill=AWNING)
                    f += 1; continue
                if style == "ribbon":
                    win(quad(0.06, 0.94, v0 + fpx * 0.08, v1 - fpx * 0.05), wmat)
                    hline(v1 - fpx * 0.05, GLARE)
                elif style == "fins":       # glass with white fins of random lengths
                    if f == 0:
                        ncol = max(2, int(L / (1.5 * S)))
                        for j in range(ncol):
                            a = (j + 0.5) / ncol; v = z0
                            while v < z1:
                                seg = rngw.uniform(2, 10) * HPX
                                if rngw.random() < 0.5:
                                    q = quad(a, a, v, min(v + seg, z1))
                                    dd.line([q[0], q[3]], fill=CONC_FRONT, width=max(2, int(0.5 * S)))
                                v += seg
                elif style == "scatter":
                    for j in range(n_win):
                        tc = (j + 0.5) / n_win
                        win(quad(tc - half, tc + half, v0, v1), wmat)
                        if rngw.random() < 0.12:
                            q = quad(tc - half * 2.2, tc + half * 2.2, b + fpx * 0.12, b + fpx * 0.95)
                            dd.polygon(q, outline=GLARE)
                elif not (style == "arches" and f > 1):
                    for j in range(n_win):
                        tc = (j + 0.5) / n_win
                        if style == "paired":
                            for o in (-0.55, 0.55):
                                c = tc + o * half
                                win(quad(c - half * 0.4, c + half * 0.4, v0, v1), wmat)
                        elif style == "strips":
                            win(quad(tc - half * 0.5, tc + half * 0.5, b + fpx * 0.1, b + fpx * 0.95), wmat)
                        elif style == "arches":
                            if f == 1: break
                            top_ = min(z1 - z0, 2 * fpx) * 0.8
                            # Franklin Field's arcade: open arches into the stands, with no rooms to light at night.
                            q = quad(tc - half * 1.3, tc + half * 1.3, b, b + top_)
                            win(q, wmat, lit=False)
                            arch = [min(q[2][0], q[3][0]), min(q[2][1], q[3][1]) - half * L * 1.3,
                                    max(q[2][0], q[3][0]), max(q[2][1], q[3][1]) + half * L * 1.3]
                            dd.ellipse(arch, fill=wmat)
                        elif style == "tall":     # gothic lancets: tall pointed windows
                            q = quad(tc - half * 0.6, tc + half * 0.6, b + fpx * 0.15, b + fpx * 0.92)
                            win(q, wmat)
                        else:
                            win(quad(tc - half, tc + half, v0, v1), wmat)
                        if opt.get("lintel") and style in ("punched", "paired", "tall"):
                            q = quad(tc - half * 1.15, tc + half * 1.15, v1 + 1, v1 + 1)
                            dd.line([q[0], q[1]], fill=CORNICE)
                        if front and style in ("punched", "paired"):
                            q = quad(tc - half, tc + half, v0, v1)
                            dd.point((q[3][0] + 1, q[3][1] + 1), fill=GLARE)
                f += 1
        if opt.get("door", True) and front and ground_floor and L > 6 * S and not opt.get("store"):
            dw = 0.9 * S / L
            q = quad(0.5 - dw, 0.5 + dw, 0, min(fpx * 0.8, 2.8 * HPX))
            dd.polygon(q, fill=DOOR)
            dd.polygon(quad(0.5 - dw * 1.8, 0.5 + dw * 1.8, min(fpx * 0.8, 2.8 * HPX), min(fpx * 0.8, 2.8 * HPX) + 3), fill=CORNICE)
        patch = np.array(im)
        yy_, xx_ = np.mgrid[oy_:oy_ + patch.shape[0], ox_:ox_ + patch.shape[1]]
        t_ = np.clip((xx_ - x1 - LEAN_X * (y1 - yy_)) / den, 0, 1)
        composite(patch, ox_, oy_, (y1 + t_ * dy).astype(np.float32), ids=np.array(idim))

    def walls_for(fp, z0, z1, mats, style, fpx, opt, invert=False):
        area = sum(fp[i][0] * fp[i + 1][1] - fp[i + 1][0] * fp[i][1] for i in range(len(fp) - 1))
        sign = (1 if area > 0 else -1) * (-1 if invert else 1)
        for i in range(len(fp) - 1):
            (x1, y1), (x2, y2) = fp[i], fp[i + 1]
            nx, ny = sign * (y2 - y1), -sign * (x2 - x1)
            ln = math.hypot(nx, ny)
            if ln < 0.5 or nx * LEAN_X - ny >= -0.02 * ln: continue          # faces away from the camera
            front = ny / ln > abs(nx / ln) * 0.6
            wall(fp[i], fp[i + 1], z0, z1, mats[0] if front else mats[1], front, style, fpx, opt)

    # ---------- flat roofs with everything that sits on them ----------
    def flat_roof(fp, z, mat, area_m2, holes=(), o=None):
        o = o or {}
        roof = [(x + LEAN_X * z, y - z) for x, y in fp]
        im, dd, ox_, oy_ = canvas(roof)
        dd.polygon([(x - ox_, y - oy_) for x, y in roof], fill=1)
        for hole in holes:
            dd.polygon([(x + LEAN_X * z - ox_, y - z - oy_) for x, y in hole], fill=0)
        mk = np.array(im) == 1
        if mk.sum() < 3: return
        patch = np.full(mk.shape, 255, np.uint8); patch[mk] = mat
        e1 = binary_erosion(mk); e2 = binary_erosion(e1)
        yy_, xx_ = np.mgrid[0:mk.shape[0], 0:mk.shape[1]]
        if o.get("stands"):
            dist = edt(mk)
            patch[mk & ((dist.astype(int) // 3) % 2 == 0)] = SEATS
        else:
            if o.get("crenel"):
                patch[e1 & ~e2] = np.where(((xx_ + oy_) // 3) % 2 == 0, PARAPET, OUTLINE)[e1 & ~e2]
            else:
                patch[e1 & ~e2] = PARAPET
            dist = edt(mk)
            if o.get("stalls"):                             # parking deck striping
                lanes_ = ((yy_ + oy_) // int(5.5 * S * GROUND_Y)) % 3
                sm = mk & (dist > 1.5 * S) & ((xx_ + ox_) % int(2.6 * S) == 0) & (lanes_ != 1)
                patch[sm] = UNIT_TOP
            if o.get("grid"):                               # rows of small skylights
                gm = mk & (dist > 2 * S) & ((xx_ + ox_) % int(3 * S) < 3) & ((yy_ + oy_) % int(2.4 * S) < 2)
                patch[gm] = UNIT_TOP
            if o.get("ducts"):                              # white duct runs, as on Stouffer and the Quad
                cand = np.argwhere(dist > 2.5 * S)
                for _ in range(o["ducts"] if len(cand) else 0):
                    cy_, cx_ = cand[rng2.randrange(len(cand))]
                    for _seg in range(rng2.randint(2, 4)):
                        L_ = int(rng2.uniform(4, 16) * S)
                        if rng2.random() < 0.5:
                            xs_ = slice(max(cx_ - L_ // 2, 0), cx_ + L_ // 2); ys_ = slice(cy_, cy_ + 2)
                            cx_ = min(max(cx_ + rng2.choice((-1, 1)) * L_ // 2, 0), mk.shape[1] - 1)
                        else:
                            ys_ = slice(max(cy_ - L_ // 3, 0), cy_ + L_ // 3); xs_ = slice(cx_, cx_ + 2)
                            cy_ = min(max(cy_ + rng2.choice((-1, 1)) * L_ // 3, 0), mk.shape[0] - 1)
                        sub = mk[ys_, xs_] & (dist[ys_, xs_] > 1.2 * S)
                        patch[ys_, xs_][sub] = UNIT_TOP
                        sh = (slice(ys_.start + 2, ys_.stop + 2) if ys_.stop - ys_.start == 2 else ys_)
                        if sh.stop <= mk.shape[0]:
                            sub2 = mk[sh, xs_] & (dist[sh, xs_] > 1.2 * S) & (patch[sh, xs_] != UNIT_TOP)
                            patch[sh, xs_][sub2] = ROOF_SHADOW
        patch[mk & ~e1] = OUTLINE
        composite(patch, ox_, oy_, (np.mgrid[oy_:oy_ + mk.shape[0], 0:mk.shape[1]][0] + z).astype(np.float32))
        if o.get("stands"): return
        if o.get("fans"):          # a cooling tower's fans in a row down its long side
            ys_m, xs_m = np.nonzero(mk); yt, yb, xl, xr = ys_m.min(), ys_m.max(), xs_m.min(), xs_m.max()
            n_ = o["fans"]; tall = (yb - yt) / GROUND_Y > (xr - xl)
            span = ((yb - yt) / GROUND_Y if tall else (xr - xl)) / n_
            r_ = min(span, (xr - xl) if tall else (yb - yt) / GROUND_Y) * 0.4
            for i_ in range(n_):
                fx_ = (xl + xr) / 2 if not tall else (xl + xr) / 2
                fx_ = xl + (xr - xl) * (i_ + 0.5) / n_ if not tall else fx_
                fy_ = yt + (yb - yt) * (i_ + 0.5) / n_ if tall else (yt + yb) / 2
                im2 = Image.new("L", (int(2 * r_) + 4, int(2 * r_ * GROUND_Y) + 8), 255); d2 = ImageDraw.Draw(im2)
                d2.rectangle([0, int(r_ * GROUND_Y), int(2 * r_) + 2, int(r_ * GROUND_Y) + 4], fill=UNIT_SIDE, outline=OUTLINE)
                d2.ellipse([0, 0, int(2 * r_) + 2, int(2 * r_ * GROUND_Y)], fill=UNIT_TOP, outline=OUTLINE)
                d2.ellipse([int(r_ * 0.25), int(r_ * GROUND_Y * 0.25), int(r_ * 1.75) + 2, int(r_ * GROUND_Y * 1.75)], fill=FAN)
                for kk in range(8):                          # its blades
                    an = kk * math.pi / 4
                    d2.line([(r_ + 1, r_ * GROUND_Y), (r_ + 1 + math.cos(an) * r_ * 0.7, r_ * GROUND_Y + math.sin(an) * r_ * 0.7 * GROUND_Y)], fill=UNIT_TOP)
                d2.ellipse([r_ - 1, r_ * GROUND_Y - 1, r_ + 3, r_ * GROUND_Y + 2], fill=UNIT_SIDE)
                p2 = np.array(im2); ux, uy = fx_ + ox_, fy_ + oy_ + r_ * GROUND_Y
                composite(p2, int(ux - r_), int(uy - 2 * r_ * GROUND_Y), np.full(p2.shape, uy + z + 0.2, np.float32))
        inner = np.argwhere(edt(mk) > 3 * S)
        if not len(inner): return
        n_units = o.get("units", min(6, int(area_m2 // 700)))
        for _ in range(n_units):
            cy_, cx_ = inner[rng2.randrange(len(inner))]
            ux, uy = cx_ + ox_, cy_ + oy_
            kind = rng2.random()
            if kind < 0.25 and area_m2 > 900:      # rooftop fan (cooling tower)
                r_ = rng2.uniform(1.5, 2.6) * S
                im2 = Image.new("L", (int(2 * r_) + 4, int(2 * r_ * GROUND_Y) + 8), 255); d2 = ImageDraw.Draw(im2)
                d2.rectangle([0, int(r_ * GROUND_Y), int(2 * r_) + 2, int(r_ * GROUND_Y) + 4], fill=UNIT_SIDE, outline=OUTLINE)
                d2.ellipse([0, 0, int(2 * r_) + 2, int(2 * r_ * GROUND_Y)], fill=UNIT_TOP, outline=OUTLINE)
                d2.ellipse([int(r_ * 0.5), int(r_ * GROUND_Y * 0.5), int(r_ * 1.5) + 2, int(r_ * GROUND_Y * 1.5)], fill=FAN)
                p2 = np.array(im2)
                composite(p2, int(ux - r_), int(uy - 2 * r_ * GROUND_Y), np.full(p2.shape, uy + z + 0.2, np.float32))
            else:
                uw, ud = rng2.uniform(2, 6) * S, rng2.uniform(1.5, 4) * S * GROUND_Y
                uh = rng2.uniform(1.0, 2.6) * HPX
                box_sprite(ux, uy, uy + z + 0.2, uw, ud, uh, o.get("unit_top", UNIT_TOP), UNIT_SIDE)
        for _ in range(o.get("bulk", 0)):            # stair / elevator bulkheads
            cy_, cx_ = inner[rng2.randrange(len(inner))]
            ux, uy = cx_ + ox_, cy_ + oy_
            box_sprite(ux, uy, uy + z + 0.25, rng2.uniform(3, 6) * S, rng2.uniform(3, 5) * S * GROUND_Y,
                       3.2 * HPX, o.get("bulk_top", ROOF), o.get("bulk_side", UNIT_SIDE), o.get("bulk_front", o.get("bulk_side", UNIT_SIDE)))

    # ---------- heightfield roofs: hip, gable, barrel, dome, cone ----------
    def shaped_roof(fp, z, shades, kind="hip", holes=(), dormers=False, dormer_mat=DORMER, chimneys=0,
                    params=None, ridge=True):
        xs = [p[0] for p in fp]; ys = [p[1] for p in fp]
        x0, y0 = int(min(xs)) - 2, int(min(ys)) - 2
        w_, h_ = int(max(xs)) - x0 + 4, int(max(ys)) - y0 + 4
        mk = Image.new("1", (w_, h_), 0); md = ImageDraw.Draw(mk)
        md.polygon([(x - x0, y - y0) for x, y in fp], fill=1)
        for hole in holes: md.polygon([(x - x0, y - y0) for x, y in hole], fill=0)
        mk = np.array(mk)
        if mk.sum() < 4: return
        yy_, xx_ = np.mgrid[0:h_, 0:w_]
        if kind == "hip":
            dist = edt(mk, sampling=(1 / GROUND_Y, 1))
            zf = np.minimum(dist * 0.75, (params or {}).get("cap", 7) * S)
        else:
            xm = (xx_ + x0) / S + west; ym = north - (yy_ + y0 - top_pad) / (S * GROUND_Y)
            p = params
            if kind in ("dome", "cone"):
                rr = np.hypot(xm - p["cx"], ym - p["cy"])
                if kind == "dome": zf = np.sqrt(np.clip(p["r"] ** 2 - rr ** 2, 0, None)) * p.get("k", 1.0) * HPX
                else: zf = np.clip(p["r"] - rr, 0, None) * p.get("slope", 1.8) * HPX
            else:      # gable / barrel along an axis
                across = np.abs(ym - p["cy"]) if p["axis"] == "x" else np.abs(xm - p["cx"])
                hw = p["hw"]
                if kind == "gable": zf = np.clip(hw - across, 0, None) * p.get("slope", 0.75) * HPX
                else: zf = np.sqrt(np.clip(hw ** 2 - across ** 2, 0, None)) * p.get("k", 0.6) * HPX
            zf = np.where(mk, zf, 0)
        gy_, gx_ = np.gradient(gaussian_filter(zf, 1.0))
        lit = -gy_ - 0.5 * gx_                  # slopes rising away from the camera face us
        sl, sm, sd_ = shades
        shade = np.where(lit > 0.25, sl, np.where(lit < -0.25, sd_, sm)).astype(np.uint8)
        if ridge:
            gmag = np.hypot(gx_, gy_)
            plateau = zf >= zf.max() - 0.5 if kind == "hip" and zf.max() >= ((params or {}).get("cap", 7) * S) - 1 else np.zeros_like(mk)
            shade = np.where((gmag < 0.2) & (zf > 0.6 * zf.max()) & (zf > 1.5 * S) & ~plateau, RIDGE, shade).astype(np.uint8)
            shade = np.where(plateau & mk, sm, shade).astype(np.uint8)
        if dormers:
            band = (zf > 0.25 * zf.max()) & (zf < 0.6 * zf.max()) & (lit > 0.4)
            col = ((xx_[0] + x0) % int(4.5 * S)) < int(1.5 * S)
            shade = np.where(band & col[None, :], dormer_mat, shade).astype(np.uint8)
            shade = np.where(band & col[None, :] & ((yy_ % 4) == 0), OUTLINE, shade).astype(np.uint8)
        won = []
        for r in range(h_):
            cols = np.nonzero(mk[r])[0]
            if not len(cols): continue
            zz = zf[r, cols]
            sx = np.round(cols + x0 + LEAN_X * (z + zz)).astype(int)
            sy = np.round(r + y0 - z - zz).astype(int)
            rs = shade[r, cols]
            # Each row is smeared down over the gap to the next one, at least 3 px: a steep slope falling toward
            # us (a turret's cone) drops more than that per row, and the ground behind would show through.
            nk = np.full(len(cols), 3)
            if r + 1 < h_:
                below = mk[r + 1, cols]
                nxt = np.round(r + 1 + y0 - z - zf[r + 1, cols]).astype(int)
                nk[below] = np.clip(nxt[below] - sy[below], 3, 12)
            # Falling eastward, a slope's lean spreads neighbouring pixels apart: fill the skipped columns
            # from the pixel to their left, or whatever stands behind the roof shows through in stripes.
            step = np.r_[sx[1:] - sx[:-1], 1]
            gap = (np.r_[cols[1:] - cols[:-1], 0] == 1) & (step >= 2)
            if gap.any():
                gi = np.nonzero(gap)[0]
                fill = [(gi[step[gi] > j], j) for j in range(1, 4)]
                sx = np.concatenate([sx] + [sx[g] + j for g, j in fill])
                sy = np.concatenate([sy] + [sy[g] for g, _ in fill])
                rs = np.concatenate([rs] + [rs[g] for g, _ in fill])
                nk = np.concatenate([nk] + [nk[g] for g, _ in fill])
            foot = np.float32(r + y0)
            for k in range(nk.max()):
                yk = sy + k
                ok = (sx >= 0) & (sx < W) & (yk >= 0) & (yk < H) & (k < nk)
                sxo, yko, sh_ = sx[ok], yk[ok], rs[ok]
                win = D[yko, sxo] <= foot + 0.5
                A[yko[win], sxo[win]] = sh_[win]; D[yko[win], sxo[win]] = foot
                won.append((yko[win], sxo[win]))
        if not won: return
        ys_ = np.concatenate([a for a, _ in won]); xs_ = np.concatenate([b for _, b in won])
        if not len(ys_): return
        ya, xa = ys_.min(), xs_.min()
        m_ = np.zeros((ys_.max() - ya + 1, xs_.max() - xa + 1), bool); m_[ys_ - ya, xs_ - xa] = True
        ey, ex = np.nonzero(m_ & ~binary_erosion(m_))
        A[ey + ya, ex + xa] = OUTLINE
        if chimneys:
            cand = np.argwhere(mk & (zf > 0.55 * zf.max()) & (zf > 1.5 * S))
            for _ in range(chimneys if len(cand) else 0):
                r, c_ = cand[rng2.randrange(len(cand))]
                sx = c_ + x0 + LEAN_X * (z + zf[r, c_]); sy = r + y0 - z - zf[r, c_]
                box_sprite(int(sx), int(sy) + 2, r + y0 + 0.6, int(1.3 * S), int(0.9 * S * GROUND_Y), int(2.0 * HPX), CORNICE, DB_SIDE, WALL_FRONT)

    def shrink(fp, k):
        cx = sum(p[0] for p in fp[:-1]) / (len(fp) - 1); cy = sum(p[1] for p in fp[:-1]) / (len(fp) - 1)
        return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in fp]

    def part_height(pid, t):
        if pid in PART_HEIGHT: return PART_HEIGHT[pid]
        for k in ("height", "building:height"):
            try: return float(t[k].replace("m", "").strip())
            except (KeyError, ValueError): pass
        try: return float(t["building:levels"]) * LEVEL_M
        except (KeyError, ValueError): return None

    def pt_roof_hipped(pid):
        return ways[pid][1].get("roof:shape") in ("hipped", "pyramidal", "gabled")

    def inside_poly(px, py, poly_):
        hit, j = False, len(poly_) - 1
        for i in range(len(poly_)):
            (xi, yi), (xj, yj) = poly_[i], poly_[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi: hit = not hit
            j = i
        return hit

    MATS = {"brick": (WALL_FRONT, WALL_SIDE), "lbrick": (LB_FRONT, LB_SIDE), "dbrick": (DB_FRONT, DB_SIDE),
            "white": (CONC_FRONT, CONC_SIDE), "stone": (STONE_FRONT, STONE_SIDE), "serp": (SERP_FRONT, SERP_SIDE),
            "glass": (GW_FRONT, GW_SIDE), "dpanel": (DP_FRONT, DP_SIDE), "metal": (METAL, METAL),
            "tan": (TAN_FRONT, TAN_SIDE), "green": (GREEN_FRONT, GREEN_SIDE)}
    TONES = {"white": ROOF_LIGHT, "mid": ROOF, "dark": ROOF_DARK, "green": GREEN_ROOF, "copper": COP_MID,
             "seats": ROOF_LIGHT, "glass": GLASS_MID, "red": TILE_MID, "tan": TAN_FRONT, "pool": WATER,
             "lawn": PODIUM_LAWN, "paving": ROOF, "gravel": GRAVEL}         # the last two for courtyards on podiums
    SHADES = {"slate": (SLOPE_LIT, SLOPE_MID, SLOPE_DARK), "tile": (TILE_LIT, TILE_MID, TILE_DARK),
              "copper": (COP_MID, COP_LIT, COP_DARK), "glass": (GLASS_LIT, GLASS_MID, GLASS_DARK),
              "light": (ROOF_LIGHT, ROOF, ROOF_DARK), "dark": (SLOPE_MID, SLOPE_DARK, RIDGE),
              "metal": (ROOF, SLOPE_LIT, SLOPE_MID)}                 # standing-seam metal, mid grey

    def mass_polys(spec, ring_ll):
        """Turn a massing item (fractions of the building's map-space bbox) into screen polygons."""
        pts_m = [R(p) for p in ring_ll]
        bx0, bx1 = min(p[0] for p in pts_m), max(p[0] for p in pts_m)
        by0, by1 = min(p[1] for p in pts_m), max(p[1] for p in pts_m)
        fx = lambda f: bx0 + (bx1 - bx0) * f; fy = lambda f: by0 + (by1 - by0) * f
        foot = SPoly(pts_m).buffer(0)
        if "box" in spec:
            a, b, c_, d_ = spec["box"]
            g = SPoly([(fx(a), fy(c_)), (fx(b), fy(c_)), (fx(b), fy(d_)), (fx(a), fy(d_))])
            center = (fx((a + b) / 2), fy((c_ + d_) / 2)); half = (abs(fx(b) - fx(a)) / 2, abs(fy(d_) - fy(c_)) / 2)
        else:
            cx_, cy_, r_ = spec["cyl"]
            n_ = spec.get("sides")                 # a regular polygon (e.g. an octagonal hall) instead of a round drum
            g = (SPoly([(fx(cx_) + r_ * math.cos(math.pi * (2 * k + 1) / n_), fy(cy_) + r_ * math.sin(math.pi * (2 * k + 1) / n_))
                        for k in range(n_)]) if n_ else SPoint(fx(cx_), fy(cy_)).buffer(r_, 12))
            center = (fx(cx_), fy(cy_)); half = (r_, r_)
        if spec.get("clip", True):
            g = g.intersection(foot) if not spec.get("noclip") else g
        polys = [g] if g.geom_type == "Polygon" else [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]
        outs = []
        for q in polys:
            if q.area < 2: continue
            outs.append([Q(*p) for p in q.exterior.coords])
        return outs, center, half

    def gable_end(a, b, z0, rise, mat, curve=False):
        """Wall closing a gable (triangle) or barrel vault (half-ellipse, `curve`) between ground points a and b
        (screen), from the eave at z0 up `rise` px."""
        (x1, y1), (x2, y2) = a, b
        dx, dy = x2 - x1, y2 - y1
        den = dx + LEAN_X * dy
        if abs(den) < 0.3: return
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        tri = [(x1 + LEAN_X * z0, y1 - z0), (x2 + LEAN_X * z0, y2 - z0), (mx + LEAN_X * (z0 + rise), my - z0 - rise)]
        if curve:
            arc = [(t, z0 + rise * math.sqrt(max(0.0, 1 - (2 * t - 1) ** 2))) for t in (i / 16 for i in range(17))]
            tri = [(x1 + dx * t + LEAN_X * z, y1 + dy * t - z) for t, z in arc]
        im, dd, ox_, oy_ = canvas(tri)
        dd.polygon([(x - ox_, y - oy_) for x, y in tri], fill=mat, outline=OUTLINE)
        patch = np.array(im)
        yy_, xx_ = np.mgrid[oy_:oy_ + patch.shape[0], ox_:ox_ + patch.shape[1]]
        t_ = np.clip((xx_ - x1 - LEAN_X * (y1 - yy_)) / den, 0, 1)
        composite(patch, ox_, oy_, (y1 + t_ * dy).astype(np.float32))

    def roof_for(fp_scr, ztop, roof, o, center=None, half=None, holes=(), area_m2=0, chim=0, dorm=False, dmat=DORMER):
        shape = roof if isinstance(roof, str) else roof[0]
        mat = None if isinstance(roof, str) else roof[1]
        if shape in ("hip", "pyramid"):
            shaped_roof(fp_scr, ztop, SHADES[mat or "slate"], "hip", holes, dorm, dmat, chim, {"cap": o.get("cap", 7)})
        elif shape in ("gable_x", "gable_y", "barrel_x", "barrel_y"):
            ax = shape[-1]
            hw = half[1] if ax == "x" else half[0]
            if o.get("shed"):          # single slope rising away from the street (ridge on the far edge)
                center = (center[0], center[1] + half[1]) if ax == "x" else (center[0] + half[0], center[1])
                hw = 2 * hw
            shaped_roof(fp_scr, ztop, SHADES[mat or "slate"], shape[:-2], holes, dorm, dmat, chim,
                        {"axis": ax, "cx": center[0], "cy": center[1], "hw": hw, "slope": o.get("slope", 0.75), "k": o.get("k", 0.55)})
            if o.get("ends") and not o.get("shed"):          # opt-in end walls; only the ends facing us show
                (cx_, cy_), (hx_, hy_) = center, half
                barrel = shape.startswith("barrel")
                rise = hw * (o.get("k", 0.55) if barrel else o.get("slope", 0.75)) * HPX; m_ = MATS[o.get("wall", "brick")]
                if ax == "y": gable_end(Q(cx_ - hx_, cy_ - hy_), Q(cx_ + hx_, cy_ - hy_), ztop, rise, m_[0], barrel)   # south end
                else: gable_end(Q(cx_ + hx_, cy_ - hy_), Q(cx_ + hx_, cy_ + hy_), ztop, rise, m_[1], barrel)          # east end
        elif shape in ("dome", "cone"):
            shaped_roof(fp_scr, ztop, SHADES[mat or "copper"], shape, (), False, dmat, 0,
                        {"cx": center[0], "cy": center[1], "r": half[0] * 1.02, "slope": o.get("slope", 1.8), "k": o.get("k", 0.9)},
                        ridge=False)
        else:
            flat_roof(fp_scr, ztop, TONES.get(mat or shape, ROOF), area_m2, holes, o)

    part_list = [(wid, pts, t) for wid, (pts, t) in ways.items()
                 if t.get("building:part") and len(pts) >= 4 and pts[0] == pts[-1]]
    spolys = {}
    for oid_, rings_, _t in buildings:
        try: spolys[oid_] = SPoly([R(p) for p in rings_[0]]).buffer(0)
        except Exception: pass
    ATTACHED = set()
    keys = list(spolys)
    for i_, a_ in enumerate(keys):
        ga = spolys[a_].buffer(0.4)
        for b_ in keys[i_ + 1:]:
            if ga.intersects(spolys[b_]):
                ATTACHED.add(a_); ATTACHED.add(b_)
    roof_cars = []     # (x, y, depth, spec, paint) of cars parked on deck roofs
    DECK_PAINTS = {"white": CAR_WHITE, "red": CAR_RED, "gray": CAR_GRAY, "green": CAR_GREEN, "silver": CAR_SILVER,
                   "black": ROOF_DARK, "orange": TAN_FRONT}

    def deck_paint(plan, ring, fp, hpx):
        """A traced parking deck (see GARAGE_DECK): its stall fields, ramps, markings and parked cars, on the flat roof."""
        pts_m = [R(p) for p in ring]
        bx0, bx1 = min(p[0] for p in pts_m), max(p[0] for p in pts_m); by0, by1 = min(p[1] for p in pts_m), max(p[1] for p in pts_m)
        def at(fx, fy):
            x, y = Q(bx0 + (bx1 - bx0) * fx, by0 + (by1 - by0) * fy); return (x + LEAN_X * hpx, y - hpx)
        rect = lambda a, b, c_, d_: [at(a, c_), at(b, c_), at(b, d_), at(a, d_)]
        roof = [(x + LEAN_X * hpx, y - hpx) for x, y in fp]
        im, dd, ox_, oy_ = canvas(roof)
        off = lambda pts_: [(x - ox_, y - oy_) for x, y in pts_]
        dd.polygon(off(roof), fill=1)
        room = binary_erosion(np.array(im) == 1, iterations=3)       # the parapet stays as flat_roof drew it
        paint = Image.new("L", im.size, 255); pd = ImageDraw.Draw(paint)
        looks = {"tan": DECK_TAN, "shade": ROOF_SHADOW, "ramp": PARKING, "patch": ROOF_LIGHT}
        for look, a, b, c_, d_ in plan["zones"]: pd.polygon(off(rect(a, b, c_, d_)), fill=looks[look])
        h_, w_ = room.shape
        yy_, xx_ = np.mgrid[oy_:oy_ + h_, ox_:ox_ + w_]
        arr = np.array(paint)
        for a, b, c_, d_ in plan["hatch"]:
            m_ = Image.new("1", (w_, h_), 0); ImageDraw.Draw(m_).polygon(off(rect(a, b, c_, d_)), fill=1)
            arr[np.array(m_) & ((xx_ + yy_) % 7 == 0)] = DECK_LINE
        paint = Image.fromarray(arr); pd = ImageDraw.Draw(paint)
        for (ax, ay), (bx, by), (cx_, cy_), n_ in plan["rows"]:
            for k in range(n_ + 1):
                px_, py_ = ax + (bx - ax) * k / n_, ay + (by - ay) * k / n_
                pd.line(off([at(px_, py_), at(px_ + cx_ - ax, py_ + cy_ - ay)]), fill=DECK_LINE)
        arr = np.array(paint); arr[~room] = 255
        composite(arr, ox_, oy_, (yy_ + hpx + 0.1).astype(np.float32))
        for fx, fy, hd, col in plan["cars"]:
            cx_, cy_ = at(fx, fy)
            an = math.radians(hd); vx, vy = math.cos(an), -math.sin(an) * GROUND_Y     # foreshortened like the ground
            ln = math.hypot(vx, vy); wd = math.hypot(math.sin(an), math.cos(an) * GROUND_Y)
            spec = (vx / ln, vy / ln, 4.5 * S * ln, 1.9 * S * wd)
            # in front of the deck under its whole length, not just its middle, or the deck cuts off its near half
            roof_cars.append((cx_, cy_, cy_ + hpx + 4.5 * S * ln / 2 + 3, spec, DECK_PAINTS[col]))
    out = []
    for bi, (osm_id, rings, t) in enumerate(buildings, start=1):
        name = t.get("name")
        B = BLD.get(osm_id) or BLD.get(name, {})       # unnamed buildings can be keyed by "way/123"
        hm = B.get("h", height_m(osm_id, t))
        hsh = zlib.crc32(osm_id.encode())          # stable across runs, unlike hash()
        rng2.seed(hsh)     # rooftop clutter per building, so editing one building can't reshuffle the rest
        wk = B.get("wall") or WALLS.get(name) or ("white" if name in CONCRETE else "serp" if name in SERPENTINE
                                                  else "stone" if name in STONE else ["brick", "brick", "lbrick", "dbrick"][hsh % 4])
        mats = MATS[wk]
        conc = wk in ("white", "glass", "dpanel", "metal")
        style = B.get("style") or WINDOW_STYLE.get(name) or ("ribbon" if wk == "glass" else "punched" if conc
                                                            else ["punched", "punched", "ribbon", "paired", "strips", "punched"][hsh % 6])
        wopt = {"wmat": WIN_LIGHT if wk in ("glass", "dpanel") else WINDOW,
                "cornice": not conc and B.get("cornice", True),
                "lintel": wk in ("brick", "lbrick", "dbrick", "stone", "serp", "green"),
                "base": B.get("base", STONE_FRONT if (wk in ("brick", "lbrick", "dbrick") and hsh % 3 == 0) else None),
                "belts": B.get("belts", wk in ("brick", "lbrick", "stone") and hsh % 4 == 1),
                "piers": B.get("piers"), "store": B.get("store", name in STOREFRONTS)}
        holes_ll = HOLES.get(osm_id, [])
        for ring in rings:
            fp = [P(p) for p in ring]
            area_px = abs(sum(fp[i][0] * fp[i + 1][1] - fp[i + 1][0] * fp[i][1] for i in range(len(fp) - 1))) / 2
            area_m2 = area_px / (S * S * GROUND_Y)
            mine = []
            for pid, pts, pt in part_list:
                pp = [P(p) for p in pts]
                cx = sum(p[0] for p in pp[:-1]) / (len(pp) - 1); cy = sum(p[1] for p in pp[:-1]) / (len(pp) - 1)
                if inside_poly(cx, cy, fp):
                    ph = part_height(pid, pt)
                    pa = abs(sum(pp[i][0] * pp[i + 1][1] - pp[i + 1][0] * pp[i][1] for i in range(len(pp) - 1))) / 2
                    mine.append((pid, pp, ph, pa))
            known = [m_ for m_ in mine if m_[2]]
            base_h = hm
            if known and sum(m_[3] for m_ in mine) > 0.7 * area_px and name not in HEIGHT_BY_NAME and "h" not in B:
                base_h = min(m_[2] for m_ in known)
            upper = []
            if B.get("cuts") and ring is rings[0]:
                rx_ = [R(p)[0] for p in ring]; ry_ = [R(p)[1] for p in ring]
                bx0, bx1, by0, by1 = min(rx_), max(rx_), min(ry_), max(ry_)
                g = SPoly([R(p) for p in ring]).buffer(0)
                for a, b, c_, d_ in B["cuts"]:
                    g = g.difference(SPoly([(bx0 + (bx1 - bx0) * a, by0 + (by1 - by0) * c_), (bx0 + (bx1 - bx0) * b, by0 + (by1 - by0) * c_),
                                            (bx0 + (bx1 - bx0) * b, by0 + (by1 - by0) * d_), (bx0 + (bx1 - bx0) * a, by0 + (by1 - by0) * d_)]))
                ups = [q for q in (g.geoms if hasattr(g, "geoms") else [g]) if q.area > 4]
                upper = [[Q(*p) for p in q.exterior.coords] for q in ups]
                up_holes = [[[Q(*p) for p in r.coords] for r in q.interiors] for q in ups]   # courts inside the outline
                top_h, base_h = base_h, B.get("cut_z", 8)
            hpx = base_h * HPX
            floors = max(1, round(base_h / LEVEL_M)); fpx = hpx / floors
            holes = [[P(p) for p in h] for h in holes_ll] if ring is rings[0] else []
            walls_for(fp, 0, hpx, mats, style, fpx, wopt)
            for h in holes:
                walls_for(h, 0, hpx, mats, style, fpx, dict(wopt, door=False, store=False, base=None), invert=True)
            if name in HOLE_FILL:   # glazed atrium instead of an open court
                for h in holes: flat_roof(h, HOLE_FILL[name] * HPX, GLASS_MID, 0, o={"units": 0})
            # ---- roof of the main outline ----
            in_hip = name in SLATE or name in HIP_EXTRA or name in QUAD or (area_m2 < 350 and base_h <= 16 and not conc and name not in ROOF_TONE and "roof" not in B and osm_id not in ATTACHED)
            chim = B.get("chimneys", (2 + hsh % 3) if (in_hip and wk in ("brick", "lbrick", "dbrick", "stone", "green") and area_m2 > 150) else 0)
            dorm = name in QUAD or name in DORMER_SET or B.get("dormers", False)
            dmat = DORMER_COP if name in QUAD else DORMER
            if upper and "cut_roof" in B:      # courts filled in at cut_z, roofed in their own material
                flat_roof(fp, hpx, TONES[B["cut_roof"]], area_m2, holes, {"units": B.get("cut_units", 0), "bulk": 0})
            elif "roof" in B and not upper:
                rx_ = [R(p)[0] for p in ring]; ry_ = [R(p)[1] for p in ring]
                ctr = ((min(rx_) + max(rx_)) / 2, (min(ry_) + max(ry_)) / 2); hf = ((max(rx_) - min(rx_)) / 2, (max(ry_) - min(ry_)) / 2)
                roof_for(fp, hpx, B["roof"], B, ctr, hf, holes, area_m2, chim, dorm, dmat)
            elif name in COPPER: shaped_roof(fp, hpx, SHADES["copper"], "hip", holes)
            elif name in RED_TILE: shaped_roof(fp, hpx, SHADES["tile"], "hip", holes, dorm or name == "College Hall", DORMER, chim)
            elif in_hip:
                shaped_roof(fp, hpx, SHADES["slate"], "hip", holes, dorm, dmat, chim)
            else:
                tone = ROOF_TONE.get(name)
                rmat = TONES.get(tone) if tone else (GREEN_ROOF if t.get("roof:material") == "grass"
                                                     else [ROOF, ROOF_LIGHT, ROOF_DARK, ROOF_DARK][hsh % 4])
                ro = {"stands": name in STANDS, "grid": B.get("grid", False), "stalls": B.get("stalls", False), "ducts": B.get("ducts", 2 if (hsh % 3 == 0 and area_m2 > 1200) else 0),
                      "bulk": B.get("bulk", 1 if area_m2 > 500 else 0), "crenel": B.get("crenel", False),
                      "bulk_side": mats[1], "bulk_front": mats[0]}
                if "units" in B: ro["units"] = B["units"]
                flat_roof(fp, hpx, ROOF_LIGHT if name in STANDS else rmat, area_m2, holes, ro)
            if upper:     # the floors above the courts
                tz = top_h * HPX
                ufpx = LEVEL_M * HPX if "cut_roof" in B else fpx
                for up, uh in zip(upper, up_holes):
                    uw = dict(wopt, door=False, store=False, base=None)
                    walls_for(up, hpx, tz, mats, style, ufpx, uw)
                    for h in uh: walls_for(h, hpx, tz, mats, style, ufpx, uw, invert=True)
                    a_up = abs(sum(up[i][0] * up[i + 1][1] - up[i + 1][0] * up[i][1] for i in range(len(up) - 1))) / 2 / (S * S * GROUND_Y)
                    rt = B.get("roof", ("flat", "mid"))
                    if isinstance(rt, tuple) and rt[0] == "hip":     # ranges of hipped roof round the courts
                        shaped_roof(up, tz, SHADES[rt[1]], "hip", uh, dorm, dmat, chim, {"cap": B.get("cap", 7)})
                    else:
                        flat_roof(up, tz, TONES.get(rt[1] if isinstance(rt, tuple) else rt, ROOF), a_up, uh,
                                  o={"units": B.get("units", 2), "bulk": 1, "ducts": B.get("ducts", 0), "bulk_side": mats[1], "bulk_front": mats[0]})
            for sk in SKYLIGHTS.get(name, []):
                shaped_roof([Q(*p) for p in sk + sk[:1]], hpx, SHADES["glass"], "hip")
            if name in STRIPS:
                rx_ = [R(p)[0] for p in ring]; ry_ = [R(p)[1] for p in ring]
                bx0, bx1, by0, by1 = min(rx_), max(rx_), min(ry_), max(ry_)
                for (fx0, fx1), (fy0, fy1), kind in STRIPS[name]:
                    sq = [(bx0 + (bx1 - bx0) * fx0, by0 + (by1 - by0) * fy0), (bx0 + (bx1 - bx0) * fx1, by0 + (by1 - by0) * fy0),
                          (bx0 + (bx1 - bx0) * fx1, by0 + (by1 - by0) * fy1), (bx0 + (bx1 - bx0) * fx0, by0 + (by1 - by0) * fy1)]
                    sqp = SPoly(sq).intersection(SPoly([R(p) for p in ring]).buffer(0))
                    if sqp.is_empty or sqp.geom_type != "Polygon": continue
                    sq = [Q(*p) for p in sqp.exterior.coords]
                    if kind == "glass": shaped_roof(sq, hpx, SHADES["glass"], "hip")
                    elif kind == "green": flat_roof(sq, hpx + 0.3 * HPX, GREEN_ROOF, 0, o={"units": 0})
                    elif kind == "pool": flat_roof(sq, hpx + 0.1 * HPX, WATER, 0, o={"units": 0})
                    else: flat_roof(sq, hpx + 0.6 * HPX, COP_MID, 0, o={"units": 0})
            # ---- OSM building:parts that rise out of the roof ----
            for pid, pp, ph, pa in sorted(mine, key=lambda m_: m_[2] or 0):
                if not ph or ph <= base_h + 0.5: continue
                z0, z1 = hpx, ph * HPX
                fl = max(1, round((ph - base_h) / LEVEL_M))
                walls_for(pp, z0, z1, mats, style, (z1 - z0) / fl, dict(wopt, door=False, store=False, base=None))
                if pid in CAPS:
                    flat_roof(pp, z1, ROOF_DARK, 0, o={"units": 0})
                    zc = z1
                    for k, dz in CAPS[pid]:
                        ring_ = shrink(pp, k)
                        walls_for(ring_, zc, zc + dz * HPX, (METAL, METAL), "none", 1, {"door": False})
                        zc += dz * HPX
                        flat_roof(ring_, zc, ROOF, 0, o={"units": 0})
                    flat_roof(shrink(pp, 0.5), zc + 0.1, ROOF, 0, o={"units": 3})     # the open deck inside the tiers
                elif pt_roof_hipped(pid):
                    shaped_roof(pp, z1, SHADES["slate"], "hip")
                else:
                    tone = ROOF_TONE.get(name)
                    flat_roof(pp, z1, TONES.get(tone, ROOF), pa / (S * S * GROUND_Y), o={"bulk": 1, "bulk_side": mats[1], "bulk_front": mats[0]})
            if "deck" in B and ring is rings[0]: deck_paint(B["deck"], ring, fp, hpx)
            # ---- hand-modeled massing from the aerial views ----
            if ring is rings[0]:
                for spec in MASS.get(osm_id) or MASS.get(name, []):
                    polys, ctr, hf = mass_polys(spec, ring)
                    z0m = spec.get("z0", base_h if "top" in spec else 0)
                    z1m = spec.get("z1", base_h + spec.get("top", 0))
                    smats = MATS[spec.get("wall", wk)]
                    sstyle = spec.get("style", style if spec.get("wall", wk) == wk else ("ribbon" if spec.get("wall") == "glass" else "punched"))
                    for poly_ in polys:
                        fl = max(1, round((z1m - z0m) / LEVEL_M))
                        sw = dict(wopt, door=False, store=False, base=None,
                                  wmat=WIN_LIGHT if spec.get("wall", wk) in ("glass", "dpanel") else WINDOW,
                                  cornice=spec.get("cornice", wopt["cornice"]), piers=spec.get("piers", wopt["piers"]), ladder=spec.get("ladder"))
                        walls_for(poly_, z0m * HPX, z1m * HPX, smats, sstyle, (z1m - z0m) * HPX / fl, sw)
                        a_m2 = abs(sum(poly_[i][0] * poly_[i + 1][1] - poly_[i + 1][0] * poly_[i][1] for i in range(len(poly_) - 1))) / 2 / (S * S * GROUND_Y)
                        ro = dict(spec)
                        ro.setdefault("units", 0 if a_m2 < 300 else 2)
                        ro.setdefault("bulk_side", smats[1]); ro.setdefault("bulk_front", smats[0])
                        roof_for(poly_, z1m * HPX, spec.get("roof", "mid"), ro, ctr, hf, (), a_m2,
                                 spec.get("chimneys", 0), spec.get("dormers", False), DORMER)
            out.append({"id": osm_id, "name": name, "type": t.get("building"),
                        "height_m": round(max([hm] + [m_[2] for m_ in known]), 1), "floors": floors,
                        "footprint_px": [[round(x), round(y)] for x, y in fp],
                        "roof_px": [[round(x + LEAN_X * hpx), round(y - hpx)] for x, y in fp]})

    # ---- trees, lamps, benches, cars go through the same depth test ---------------
    def tree_sprite(r, kind, seed, sway=0):
        """`sway` pushes the crown that many pixels downwind (its top twice as far) and leaves the trunk, for
        the breeze overlay."""
        R2 = int(r * 1.35) + 3; trunk = int(2.2 * S)
        im = Image.new("L", (2 * R2 + 1, 2 * R2 + trunk + 2 + int(r)), 255); dd = ImageDraw.Draw(im)
        cx, cy = R2, R2 + int(r)
        if kind == "conifer":     # columnar evergreen, as along Hill Square: three stacked cones seen from a little
            hgt = r * 3.2; base = cy + R2 * 0.6   # above, lit from the upper left, each skirt overlapping the tier below
            dd.line([(cx, base), (cx, base + trunk)], fill=OUTLINE, width=max(2, int(S * 0.5)))
            bands = ((-1.0, -0.55, TREE_LIT), (-0.55, -0.1, TREE), (-0.1, 0.35, EVERGREEN), (0.35, 0.75, TREE_SHADE), (0.75, 1.0, PINE_DEEP))

            def hem(tx, bot, wdt, u):            # point on the near half of the cone's base ellipse; u runs -1 (left) to 1
                return tx + wdt * u, bot + wdt * 0.32 * math.sqrt(max(0.0, 1 - u * u))

            for k in (2, 1, 0):                  # bottom tier first, so each higher skirt lies over the one below
                top = base - hgt * (1 - k * 0.22); bot = base - hgt * 0.3 * (2 - k)
                wdt = r * (0.55 + 0.25 * k); tx = cx + sway * (2 - k)
                us = [-1 + 2 * i / 16 for i in range(17)]
                if k < 2:                         # the skirt's shadow on the tier below
                    dd.polygon([(tx, top + 2)] + [(x, y + 2) for x, y in (hem(tx, bot, wdt, u) for u in us)], fill=PINE_DEEP)
                for u0, u1, mat in bands:
                    pts = [hem(tx, bot, wdt, u0 + (u1 - u0) * i / 6) for i in range(7)]
                    dd.polygon([(tx, top)] + pts, fill=mat)
                    n_teeth = max(1, int(wdt * (u1 - u0) / 3))       # ragged branch tips along the hem
                    for i in range(n_teeth):
                        x, y = hem(tx, bot, wdt, u0 + (u1 - u0) * (i + 0.5) / n_teeth)
                        dd.polygon([(x - 1.5, y - 1), (x + 1.5, y - 1), (x, y + 2)], fill=mat)
            arr = np.array(im); mk = arr != 255
            arr[mk & ~binary_erosion(mk)] = OUTLINE
            return arr, R2, cy + int(R2 * 0.6) + trunk
        dd.line([(cx, cy), (cx, cy + R2 * 0.6 + trunk)], fill=OUTLINE, width=max(2, int(S * 0.6)))
        blobs = [(0, 0, r), (-0.45 * r, 0.2 * r, 0.7 * r), (0.45 * r, 0.25 * r, 0.7 * r), (0.1 * r, -0.4 * r, 0.65 * r)]
        blobs = [(bx + sway * (2 if by < 0 else 1), by, br) for bx, by, br in blobs]
        for bx, by, br in blobs: dd.ellipse([cx + bx - br - 1, cy + by - br - 1, cx + bx + br + 1, cy + by + br + 1], fill=OUTLINE)
        for bx, by, br in blobs: dd.ellipse([cx + bx - br, cy + by - br, cx + bx + br, cy + by + br], fill=PINE_DEEP if kind == "dark" else TREE_SHADE)
        body, litm = {"fall": (FALL, FALL_LIT), "dark": (TREE_SHADE, TREE)}.get(kind, (TREE, TREE_LIT))
        for bx, by, br in blobs:
            dd.ellipse([cx + bx - br, cy + by - br, cx + bx + br * 0.6, cy + by + br * 0.6], fill=body)
        for bx, by, br in blobs[:1] + blobs[3:]:
            dd.ellipse([cx + bx - br * 0.75, cy + by - br * 0.8, cx + bx - br * 0.05, cy + by - br * 0.1], fill=litm)
        rl = random.Random(seed)
        for _ in range(3):        # a few leaf clusters catching the light
            a = rl.random() * math.tau; rr = r * rl.uniform(0.2, 0.6)
            px_, py_ = cx + math.cos(a) * rr - r * 0.2 + 2 * sway, cy + math.sin(a) * rr * 0.7 - r * 0.25
            dd.ellipse([px_ - 2, py_ - 1, px_ + 2, py_ + 1], fill=litm)
        return np.array(im), R2, cy + int(R2 * 0.6) + trunk

    def lamp_sprite():
        hh = int(4.5 * HPX)
        im = Image.new("L", (7, hh + 4), 255); dd = ImageDraw.Draw(im)
        dd.line([(3, 3), (3, hh + 3)], fill=OUTLINE, width=1)
        dd.rectangle([1, 0, 5, 3], fill=OUTLINE); dd.rectangle([2, 1, 4, 2], fill=LAMP)
        return np.array(im), 3, hh + 3

    def bench_sprite():
        w_ = int(1.8 * S)
        im = Image.new("L", (w_ + 2, 5), 255); dd = ImageDraw.Draw(im)
        dd.rectangle([0, 0, w_ + 1, 4], fill=OUTLINE); dd.rectangle([1, 1, w_, 2], fill=BENCH)
        return np.array(im), w_ // 2, 4

    def car_sprite(spec, col):
        ux, uy, Lc, Wc = spec
        px_, py_ = -uy, ux
        Hc = 1.45 * HPX
        def rect(l, w, dz, sh=0.0):
            return [(ux * a + px_ * b + LEAN_X * dz, uy * a + py_ * b - dz)
                    for a, b in ((-l / 2 + sh, -w / 2), (l / 2 + sh, -w / 2), (l / 2 + sh, w / 2), (-l / 2 + sh, w / 2))]
        base = rect(Lc, Wc, 0); top = rect(Lc, Wc, Hc * 0.55)
        cab0 = rect(Lc * 0.62, Wc * 0.92, Hc * 0.55, -Lc * 0.04); cab1 = rect(Lc * 0.5, Wc * 0.8, Hc, -Lc * 0.04)
        allp = base + top + cab0 + cab1
        x0 = min(p[0] for p in allp) - 2; y0 = min(p[1] for p in allp) - 2
        im = Image.new("L", (int(max(p[0] for p in allp) - x0) + 3, int(max(p[1] for p in allp) - y0) + 3), 255)
        dd = ImageDraw.Draw(im)
        sh = lambda pts_: [(x - x0, y - y0) for x, y in pts_]
        dd.polygon(sh(base), fill=OUTLINE)
        for i in range(4):          # body sides
            dd.polygon(sh([base[i], base[(i + 1) % 4], top[(i + 1) % 4], top[i]]), fill=col, outline=OUTLINE)
        dd.polygon(sh(top), fill=col, outline=OUTLINE)
        for i in range(4):          # glass all round the cabin
            dd.polygon(sh([cab0[i], cab0[(i + 1) % 4], cab1[(i + 1) % 4], cab1[i]]), fill=WINDOW)
        dd.polygon(sh(cab1), fill=col, outline=OUTLINE)     # painted roof, never black
        dd.line(sh([cab1[0], cab1[3]]), fill=GLARE)
        return np.array(im), int(-x0), int(-y0)

    sprites = []
    for px, py in [P(ll) for ll in trees] + extra:
        if 0 <= int(px) < W and 0 <= int(py) < H and land[int(py), int(px)]:
            rt = random.Random(zlib.crc32(f"tree:{int(px)}:{int(py)}".encode()))   # each tree's own draws
            k = rt.random()
            kind = ("fall" if k < FALL_SHARE else "conifer" if k < FALL_SHARE + CONIFER_SHARE
                    else "dark" if k < FALL_SHARE + CONIFER_SHARE + DARK_SHARE else "leaf")
            sprites.append((py, "tree", px, rt.uniform(0.8, 1.2) * 3.2 * S, (kind, rt.getrandbits(32))))
    for px, py in lamps:
        if 0 <= int(px) < W and 0 <= int(py) < H and land[int(py), int(px)]: sprites.append((py, "lamp", px, 1, 0))
    for px, py in [P(ll) for ll in points["bench"]] + [P(ll) for ll in points["bicycle_parking"]]:
        if 0 <= int(px) < W and 0 <= int(py) < H and land[int(py), int(px)]: sprites.append((py, "bench", px, 1, 0))
    for px, py, spec, col in lot_cars:
        if 0 <= int(px) < W and 0 <= int(py) < H and land[int(py), int(px)]: sprites.append((py, "car", px, spec, col))
    for px, py, dep, spec, col in roof_cars: sprites.append((dep, "rcar", px, spec, (col, py)))   # sorted by depth, drawn at py
    # Street cars aren't baked in: the client drives them along the street graph written to traffic.json below.
    lamp_spr = lamp_sprite(); bench_spr = bench_sprite()

    # Roots of some of the trees standing near the cut, poking out of its face toward the viewer: dark brown,
    # long and thin, twisting as they go and forking, each from a small hole with its shadow on the clay
    # behind it, the odd one hanging past the bottom of the base.
    rim = full & (depth == 1)
    root_im = Image.new("L", (W, H), 255); rd_ = ImageDraw.Draw(root_im)

    def root(x_, y_, ang, ln, wd, rr, forks):
        pts = [(x_, y_)]
        for k in range(int(ln)):
            ang += rr.uniform(-0.35, 0.35); ang = min(max(ang, 0.4), math.pi - 0.4)     # wanders, but heads down
            x_ += math.cos(ang); y_ += math.sin(ang) * 0.8; pts.append((x_, y_))
        n_ = len(pts); wid = [max(1, round(wd * (1 - k / n_) ** 0.5)) for k in range(n_)]   # slow taper
        for k in range(n_ - 1):                              # shadow on the clay, down and to the right
            rd_.line([(pts[k][0] + 1, pts[k][1] + 2), (pts[k + 1][0] + 1, pts[k + 1][1] + 2)], fill=SOIL_DEEP, width=wid[k])
        for k in range(n_ - 1):
            rd_.line([pts[k], pts[k + 1]], fill=OUTLINE, width=wid[k])
        for k in range(n_ - 1):                              # a dull brown sheen along the top of the thick part
            if wid[k] >= 2: rd_.point((pts[k][0], pts[k][1] - (wid[k] - 1) / 2), fill=DB_SIDE)
        for _ in range(forks):
            k0 = rr.randrange(n_ // 4, max(n_ // 4 + 1, n_ * 3 // 4))
            root(pts[k0][0], pts[k0][1], ang + rr.choice((-1, 1)) * rr.uniform(0.5, 1.0), ln * rr.uniform(0.3, 0.6),
                 max(1, wd - 1), rr, 0)

    for py, kind, px, r, extra_ in sprites:
        if kind != "tree": continue
        x_i = int(px)
        hit = next((y_ for y_ in range(int(py), min(H, int(py) + int(6 * S * GROUND_Y))) if rim[y_, x_i]), None)
        if hit is None: continue
        rr = random.Random(zlib.crc32(f"root:{x_i}:{int(py)}".encode()))
        if rr.random() > 0.35: continue                      # most trees show nothing
        for _ in range(rr.choice((1, 1, 2))):
            sx_ = int(x_i + rr.uniform(-r * 0.5, r * 0.5)); sy_ = hit + int(drop * rr.uniform(0.2, 0.45))
            if not (0 <= sx_ < W and sy_ < H and full[sy_, sx_]): continue
            rd_.ellipse([sx_ - 2, sy_ - 1, sx_ + 2, sy_ + 2], fill=OUTLINE)            # the hole it comes out of
            dangle = rr.random() < 0.3
            root(sx_, sy_, math.pi / 2 + rr.uniform(-0.7, 0.7), drop * (0.9 if dangle else rr.uniform(0.35, 0.6)) + rr.randint(0, 6),
                 rr.choice((2, 3)), rr, rr.choice((1, 1, 2)))
    ra = np.array(root_im); rm_ = (ra != 255) & ~land & ~fm          # the pipes stay in front
    A[rm_] = ra[rm_]


    def draw_sprites(A, D, sway=0, WID=WID):
        """Composite every sprite, returning each tree's box as (x, y, w, h)."""
        boxes = []
        for py, kind, px, r, extra_ in sorted(sprites, key=lambda s_: s_[0]):
            if kind == "tree":
                spr, ax, ay = tree_sprite(r, *extra_, sway=sway)
                if tree_clear[int(py), int(px)]: continue
                boxes.append((int(px) - ax, int(py) - ay, spr.shape[1], spr.shape[0]))
            elif kind == "lamp": spr, ax, ay = lamp_spr
            elif kind == "car": spr, ax, ay = car_sprite(r, extra_)
            elif kind == "rcar":                     # on a deck roof: drawn up there, but behind what the roof is behind
                spr, ax, ay = car_sprite(r, extra_[0])
                composite(spr, int(px) - ax, int(extra_[1]) - ay, np.full(spr.shape, py, np.float32), tol=0.0, A=A, D=D, WID=WID)
                continue
            else: spr, ax, ay = bench_spr
            composite(spr, int(px) - ax, int(py) - ay, np.full(spr.shape, py, np.float32), tol=0.0, A=A, D=D, WID=WID)
        return boxes

    # The breeze frames: the same scene with every crown blown 1 and then 2 pixels east (tops twice as far), drawn
    # from before any sprites.
    breezes = [(A.copy(), D.copy()) for _ in BREEZE_LEVELS]
    tree_boxes = draw_sprites(A, D)
    for (A_b, D_b), sway in zip(breezes, BREEZE_LEVELS):
        draw_sprites(A_b, D_b, sway=sway, WID=None)
    img = Image.fromarray(A)

    # ---- materials -> 3 colors --------------------------------------------
    def colorize(m):
        """Material ids -> palette indices."""
        yy, xx = np.mgrid[0:H, 0:W]
        ck = (xx + yy) % 2 == 0
        dots = (xx % 2 == 0) & (yy % 2 == 0)
        c = np.zeros_like(m)
        def put(mat, val): c[m == mat] = np.broadcast_to(val, m.shape)[m == mat]
        # lawn: base green, with low-frequency patches of a lighter (cream-dithered) and darker (black-dithered) green
        nz = np.array(Image.fromarray((np.random.RandomState(3).rand(H0 // 40 + 2, W // 40 + 2) * 255).astype(np.uint8))
                      .resize((W, H0), Image.BICUBIC)) / 255.0
        nz = np.pad(nz, ((0, H - H0), (0, 0)), mode="edge")
        light_g = np.where(((xx % 4 == 0) & (yy % 2 == 0)) | (((xx + 2) % 4 == 0) & (yy % 2 == 1)), 0, 3)
        dark_g = np.where(((xx % 4 == 1) & (yy % 2 == 0)) | (((xx + 2) % 4 == 1) & (yy % 2 == 1)), 2, 3)
        base_g = np.where(((xx % 6 == 0) & (yy % 4 == 0)) | (((xx + 3) % 6 == 0) & (yy % 4 == 2)), 0, 3)
        put(GRASS, np.where(nz > 0.62, light_g, np.where(nz < 0.24, np.where(ck, 3, 2), np.where(nz < 0.38, dark_g, base_g))))
        put(FLOWER, np.where(ck, 1, np.where(dots, 0, 3)))
        put(CURB, np.where(ck, 2, 0))
        put(PARKING, np.where(dots, 0, 2))   # asphalt: dark grey, lighter than the streets
        put(STEPS, np.where(yy % 3 == 0, 2, 0))
        put(SLOPE_LIT, np.where(ck, 2, 0)); put(SLOPE_MID, np.where(dots, 0, 2)); put(SLOPE_DARK, np.where((xx % 4 == 0) & (yy % 2 == 0), 0, 2))
        put(RIDGE, 2); put(DORMER, 0)
        put(GREEN_ROOF, np.where(dots, 0, 3))
        put(FALL, np.where(dots, 1, 3)); put(FALL_LIT, np.where(dots, 1, np.where(ck, 0, 3)))
        put(CORNICE, 0); put(DOOR, 2); put(PARAPET, 0)
        put(UNIT_TOP, 0); put(UNIT_SIDE, np.where(ck, 2, 0))
        put(LAMP, 0); put(BENCH, 1); put(METAL, np.where(ck, 2, 0))
        put(SERP_FRONT, np.where(dots, 0, 3)); put(SERP_SIDE, np.where(ck, 2, 3))
        put(STONE_FRONT, np.where((xx % 3 == 0) & (yy % 2 == 0), 2, 0)); put(STONE_SIDE, np.where(ck, 2, 0))
        put(TILE_LIT, 1); put(TILE_MID, np.where(dots, 2, 1)); put(TILE_DARK, np.where(ck, 2, 1))
        put(COP_LIT, np.where(ck, 0, 3)); put(COP_MID, 3); put(COP_DARK, np.where(ck, 2, 3))
        put(ROOF_LIGHT, np.where((xx % 3 == 0) & (yy % 3 == 0), 2, 0)); put(ROOF_DARK, np.where(dots, 0, 2))
        put(SEATS, np.where(dots, 0, 2))
        put(LB_FRONT, np.where(((xx % 4 == 0) & (yy % 2 == 0)), 0, 1)); put(LB_SIDE, np.where(dots, 2, 1))
        put(TAN_FRONT, np.where(ck, 0, 1)); put(TAN_SIDE, np.where(dots, 2, np.where(ck, 0, 1)))
        put(AWNING, np.where(xx % 4 < 2, 3, 0)); put(ROOF_SHADOW, np.where(ck, 2, 0)); put(FAN, 2)
        put(EVERGREEN, np.where(dots, 2, 3))
        put(CAR_WHITE, 0); put(CAR_RED, 1); put(CAR_GRAY, np.where(ck, 0, 2)); put(CAR_GREEN, 3)
        put(CAR_SILVER, np.where(dots, 2, 0)); put(CAR_DARKRED, np.where(ck, 1, 2))
        put(ENDZONE, np.where(dots, 0, 1)); put(YARD, 0)
        put(DB_FRONT, np.where(dots, 2, 1)); put(DB_SIDE, np.where(dots, 1, 2))
        put(GW_FRONT, np.where(xx % 3 == 0, 0, 2)); put(GW_SIDE, np.where((xx % 3 == 0) & (yy % 2 == 0), 0, 2))
        put(DP_FRONT, np.where((xx % 4 == 0) & (yy % 2 == 0), 0, 2)); put(DP_SIDE, 2)
        put(WIN_LIGHT, np.where(dots, 2, 0)); put(DORMER_COP, np.where(ck, 0, 3))
        put(GLASS_LIT, np.where(xx % 2 == 0, 0, 2)); put(GLASS_MID, np.where(xx % 3 == 0, 0, 2)); put(GLASS_DARK, np.where(xx % 4 == 0, 0, 2))
        put(PITCH, np.where(((xx // (4 * int(S))) % 2 == 0), np.where(dots, 0, 3), 3))
        put(TRACK, np.where((xx + yy) % 7 == 0, 0, 1)); put(CROSS, 0)
        put(ROAD, 2); put(MARK, 0); put(DECK, np.where(dots, 2, 0)); put(PODIUM_LAWN, base_g); put(GRAVEL, np.where(ck, 0, np.where((xx % 4 == 1) & (yy % 2 == 1), 1, 2))); put(PINE_DEEP, np.where(dots, 3, 2))
        # brick pavers: red with cream mortar in a running bond
        put(PATH, np.where((yy % 3 == 0) | (((xx + ((yy // 3) % 2) * 2) % 4) == 0), 0, 1))
        put(WATER, np.where((yy % 4 == 0) & (xx % 5 < 2), 0, np.where(ck, 3, 2)))
        put(ROOF, np.where(dots, 2, 0))
        put(SLATE_ROOF, np.where(ck, 2, 0))
        put(WALL_FRONT, np.where((yy % 3 == 0) & (((xx + ((yy // 3) % 2) * 2) % 4) == 0), 2, 1))
        put(WALL_SIDE, np.where(ck, 2, 1))
        put(CONC_FRONT, 0); put(CONC_SIDE, np.where(dots, 2, 0))
        put(OUTLINE, 2); put(TREE, 3); put(WINDOW, 2); put(GLARE, 0)
        put(TREE_LIT, np.where(ck, 3, 0)); put(TREE_SHADE, np.where(ck, 3, 2)); put(GRASS_DARK, np.where(ck, 3, 2))
        put(OFFMAP, np.where((xx + yy) % 8 == 0, 2, 0))
        put(GREEN_FRONT, np.where(ck, 3, 2)); put(GREEN_SIDE, np.where(dots, 3, 2))
        put(DECK_TAN, np.where((xx % 4 == 0) & (yy % 2 == 0), 1, 0)); put(DECK_LINE, np.where(ck, 1, 2))
        put(SOIL_FRONT, np.where((xx % 4 == 0) & (yy % 2 == 0), 2, 1)); put(SOIL_DEEP, np.where(ck, 2, 1)); put(SOIL_SIDE, np.where(dots, 1, 2))
        put(GROUND, np.where(((xx % 9 == 0) & (yy % 7 == 0)) | ((xx % 9 == 4) & (yy % 7 == 3)), 2, 0))
        fb = np.array(football)
        if fb.any():   # lane lines around the football field
            dm = edt(~fb, sampling=(1 / GROUND_Y, 1)) / S
            lanes = (m == TRACK) & (dm > 1.5) & (dm < 11) & ((dm % 1.25) < 0.22)
            c[lanes] = 0
        # shadows darken whatever ground they fall on (never buildings, trees or the border)
        sh = np.array(shimg) & land
        on_ground = sh & np.isin(m, [GROUND, GRASS, PATH, CROSS, MARK, CURB, FLOWER, STEPS, DECK]) & ck
        c[on_ground] = 2
        return c

    c = colorize(np.array(img))
    c_breezes = [colorize(A_b) for A_b, _ in breezes]
    os.makedirs(out_dir, exist_ok=True)
    Image.fromarray(np.array(PALETTE, dtype=np.uint8)[c]).save(os.path.join(out_dir, "campus.png"), optimize=True)
    # Every overlay below is drawn pixel-for-pixel over campus.png, so each one's JSON records which campus.png it
    # was rendered with; a test fails if any of them goes stale after a re-render that didn't copy them all.
    with open(os.path.join(out_dir, "campus.png"), "rb") as f:
        stamp = hashlib.sha1(f.read()).hexdigest()[:12]
    # Each breeze overlay (breeze1.png, breeze2.png: one per lean level) holds only the pixels that change when the
    # crowns lean that far, and breeze.json the box of every tree with any, so the client can lean each tree to
    # whatever level the wind gives it.
    moved_any = np.zeros((H, W), bool)
    for level, c_b in enumerate(c_breezes, 1):
        moved = c_b != c
        moved_any |= moved
        rgba = np.zeros((H, W, 4), np.uint8)
        rgba[moved, :3] = np.array(PALETTE, dtype=np.uint8)[c_b[moved]]
        rgba[moved, 3] = 255
        Image.fromarray(rgba, "RGBA").save(os.path.join(out_dir, f"breeze{level}.png"), optimize=True)
    breeze_boxes = []
    for x, y, w_, h_ in tree_boxes:
        xa, ya, xb, yb = max(0, x), max(0, y), min(W, x + w_), min(H, y + h_)
        if xb > xa and yb > ya and moved_any[ya:yb, xa:xb].any():
            breeze_boxes.append([xa, ya, xb - xa, yb - ya])
    with open(os.path.join(out_dir, "breeze.json"), "w") as f:
        json.dump({"map": stamp, "levels": len(BREEZE_LEVELS), "trees": breeze_boxes}, f)

    # ---- night lights ---------------------------------------------------------
    def write_lights(out_dir):
        """Every building window that can light up at night, in windows.bin: one record per window as wall()
        drew it (a punched window, one floor of a ribbon or glass wall, a shopfront), holding its box (x, y, w, h
        as little-endian uint16s) then its visible pixels as a row-major bit mask (np.packbits). Records come in a
        random order, which is the order the client switches them. Parked cars and anything else glazed aren't
        building windows, so they never light up. lights.json holds the lamp head of every street lamp that isn't
        hidden behind something."""
        # A window is a patch of glass as it shows, if wall() tagged any of it: where two walls overlap (building
        # parts drawn over each other) one window's glass can carry pieces of several ids, or none where other
        # glass was drawn over it, and lit by id it would come on in pieces.
        glass = np.isin(A, [WINDOW, WIN_LIGHT]) & land
        patches, n_ = label(glass)
        tagged = np.zeros(n_ + 1, bool)
        tagged[patches[glass & (WID > 0)]] = True
        tagged[0] = False
        ids = np.where(tagged[patches], patches, 0)
        split = sum(len(np.unique(WID[s][(patches[s] == k) & (WID[s] > 0)])) > 1
                    or ((patches[s] == k) & (WID[s] == 0)).any()
                    for k, s in enumerate(find_objects(ids), 1) if s is not None)
        print(f"{int(tagged.sum())} windows, {split} of them pieced together from more than one wall")
        records = []
        for k, s in enumerate(find_objects(ids), 1):
            if s is None: continue
            mask = ids[s] == k
            if mask.sum() < 2: continue
            records.append(struct.pack("<4H", s[1].start, s[0].start, mask.shape[1], mask.shape[0])
                           + np.packbits(mask, axis=None).tobytes())
        order = np.random.RandomState(7).permutation(len(records))
        with open(os.path.join(out_dir, "windows.bin"), "wb") as f:
            for i in order: f.write(records[i])
        # A lamp only glows if its head and most of its post are in view, so no light floats over a roof or a
        # tree that hides the post. The lamp sprite's pixels all carry its foot y as depth.
        hh = int(4.5 * HPX)
        heads = []
        for px, py in lamps:
            hx, hy = int(px), int(py) - hh - 2
            if not (0 <= hx < W and 0 <= hy < H and land[int(py), hx] and A[hy, hx] == LAMP): continue
            foot = np.float32(py)
            post = D[max(0, hy + 3):int(py), hx]
            if D[hy, hx] == foot and post.size and (post == foot).mean() >= 0.6:
                heads.append([hx, hy])
        with open(os.path.join(out_dir, "lights.json"), "w") as f:
            json.dump({"map": stamp, "lamps": heads}, f)

    # ---- traffic: the street graph cars drive on, their sprites, and what stands in front of the streets ----
    def write_traffic(out_dir):
        """traffic.json holds the drivable streets as a graph (in map pixels, each edge pointing the way its
        forward traffic flows, with the lane_layout offsets in pixels; no back lanes means one-way), the island
        outline cars are clipped to, and the layout of cars.png: one row per heading, one column per paint. occluders.png holds every pixel of a building, tree, or lamp standing in front of
        a street, which the client draws back over the cars."""
        drive = [(oriented(pts, t), t) for pts, t in ways.values()
                 if t.get("highway") in DRIVE and drawable(t) and len(pts) >= 2]
        uses = {}
        for pts, _t in drive:
            for p in set(pts): uses[p] = uses.get(p, 0) + 1

        def on_land(x, y):
            return 0 <= int(x) < W and 0 <= int(y) < H and bool(land[int(y), int(x)])

        node_ids, nodes_out, edges_out = {}, [], []
        def node(p):
            if p not in node_ids:
                x, y = P(p)
                node_ids[p] = len(nodes_out)
                nodes_out.append([round(x, 1), round(y, 1), int(on_land(x, y)), round(XN.get(p, 0.0), 1)])
            return node_ids[p]
        for pts, t in drive:
            lay = lane_layout(t)
            px = lambda v: None if v is None else round(v * S, 1)
            lanes = {"fwd": [px(v) for v in lay["fwd"]], "back": [px(v) for v in lay["back"]],
                     "park_fwd": px(lay["park_fwd"]), "park_back": px(lay["park_back"])}
            cut = [0] + [i for i in range(1, len(pts) - 1) if uses[pts[i]] > 1] + [len(pts) - 1]
            for i0, i1 in zip(cut, cut[1:]):
                sp = [P(p) for p in pts[i0:i1 + 1]]
                samples = [(x1 + (x2 - x1) * k / 8, y1 + (y2 - y1) * k / 8) for (x1, y1), (x2, y2) in zip(sp, sp[1:])
                           for k in range(9)]
                if not any(on_land(x, y) for x, y in samples): continue
                edges_out.append({"a": node(pts[i0]), "b": node(pts[i1]), **lanes,
                                  "pts": [[round(x, 1), round(y, 1)] for x, y in sp],
                                  "layer": int(t.get("layer", "1" if t.get("bridge") == "yes" else "0")),
                                  "_half": ROAD_W[t["highway"]] * S * 0.85 / 2})
        edges_out, bridges = split_crossings(edges_out, nodes_out, on_land)

        # One sprite per heading (counterclockwise from east, in ground angles) and paint.
        def car_paint(m):
            yy_, xx_ = np.mgrid[0:m.shape[0], 0:m.shape[1]]
            ck_ = (xx_ + yy_) % 2 == 0; dots_ = (xx_ % 2 == 0) & (yy_ % 2 == 0)
            out_ = np.full(m.shape, -1)
            for mat, val in {OUTLINE: 2, WINDOW: 2, GLARE: 0, CAR_WHITE: 0, CAR_RED: 1, CAR_GREEN: 3,
                             CAR_GRAY: np.where(ck_, 0, 2), CAR_SILVER: np.where(dots_, 2, 0),
                             CAR_DARKRED: np.where(ck_, 1, 2)}.items():
                out_[m == mat] = np.broadcast_to(val, m.shape)[m == mat]
            return out_
        rows = []
        for k in range(CAR_HEADINGS):
            th = k * math.tau / CAR_HEADINGS
            dx, dy = math.cos(th), -math.sin(th) * GROUND_Y
            ux, uy = dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)
            spec = (ux, uy, 4.5 * S / math.hypot(ux, uy / GROUND_Y), 1.9 * S)
            rows.append([car_sprite(spec, col) for col in CAR_PAINTS])
        cw = max(s.shape[1] for row in rows for s, _, _ in row) + 2
        chh = max(s.shape[0] for row in rows for s, _, _ in row) + 2
        sheet = np.full((chh * CAR_HEADINGS, cw * len(CAR_PAINTS)), -1)
        anchors = []
        for k, row in enumerate(rows):
            for j, (spr, ax, ay) in enumerate(row):
                ox_, oy_ = (cw - spr.shape[1]) // 2, (chh - spr.shape[0]) // 2
                sheet[k * chh + oy_:k * chh + oy_ + spr.shape[0], j * cw + ox_:j * cw + ox_ + spr.shape[1]] = car_paint(spr)
            anchors.append([(cw - row[0][0].shape[1]) // 2 + row[0][1], (chh - row[0][0].shape[0]) // 2 + row[0][2]])
        rgba_ = np.zeros((*sheet.shape, 4), np.uint8)
        rgba_[sheet >= 0, :3] = np.array(PALETTE, dtype=np.uint8)[sheet[sheet >= 0]]
        rgba_[sheet >= 0, 3] = 255
        Image.fromarray(rgba_, "RGBA").save(os.path.join(out_dir, "cars.png"), optimize=True)

        # A car's pixels sit on the road and a few rows above it (its height). Something drawn there stands in
        # front of the car when its foot is farther down the screen than the car's: anything over the road
        # surface itself, and over the rows just north of the road only if its foot is well south of them.
        rel = D - np.arange(H, dtype=np.float32)[:, None]
        north = np.zeros_like(road_ground)
        for k in range(1, 9): north[:-k] |= road_ground[k:]
        north &= ~road_ground
        occ = land & ((road_ground & (rel > 3)) | (north & (rel > 8)) | overpass)
        rgba_ = np.zeros((H, W, 4), np.uint8)
        rgba_[occ, :3] = np.array(PALETTE, dtype=np.uint8)[c[occ]]
        rgba_[occ, 3] = 255
        Image.fromarray(rgba_, "RGBA").save(os.path.join(out_dir, "occluders.png"), optimize=True)

        write_lights(out_dir)
        with open(os.path.join(out_dir, "traffic.json"), "w") as f:
            json.dump({"map": stamp, "px_per_m": S, "ground_y": GROUND_Y,
                       "land": [[round(x, 1), round(y, 1)] for x, y in (Q(*p) for p in crop)],
                       "nodes": nodes_out, "edges": edges_out, "bridges": bridges,
                       "cars": {"cell": [cw, chh], "headings": CAR_HEADINGS, "paints": len(CAR_PAINTS),
                                "anchors": anchors}}, f)
    write_traffic(out_dir)
    ref = Image.fromarray(np.array(PALETTE, dtype=np.uint8)[c]).resize((W // 2, H // 2), Image.LANCZOS)
    rd = ImageDraw.Draw(ref)
    for b in out:
        if not b["name"]: continue
        xs_ = [p[0] for p in b["footprint_px"]]; ys_ = [p[1] for p in b["footprint_px"]]
        tx, ty = sum(xs_) / len(xs_) / 2, sum(ys_) / len(ys_) / 2
        rd.text((tx, ty), b["name"], fill=(255, 255, 255), anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
    ref.save(os.path.join(out_dir, "campus_labeled.png"))
    with open(os.path.join(out_dir, "buildings.json"), "w") as f:
        json.dump({"width": W, "height": H, "px_per_m": S, "rotation_deg": round(math.degrees(ang), 2),
                   "attribution": "(c) OpenStreetMap contributors", "buildings": out}, f)
    print(f"{W}x{H}px, rotated {math.degrees(ang):.1f} deg, {len(out)} shapes, "
          f"{sum(1 for b in out if b['name'])} named")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0], args[1] if len(args) > 1 else ".", "--list" in sys.argv)
