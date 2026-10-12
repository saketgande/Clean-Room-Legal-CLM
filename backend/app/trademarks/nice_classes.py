"""
The Nice Classification — WIPO's standard 45-class system for sorting
goods/services in a trademark filing. This is static reference data (the
class list itself barely changes; the last major edition update was 2023)
so it's hardcoded rather than fetched from anywhere live. Headings here are
short paraphrases for UI/prompt use, not the full legal class text.
"""

NICE_CLASSES: list[dict[str, str]] = [
    {"class": "1", "heading": "Chemicals for industrial, scientific, and agricultural use"},
    {"class": "2", "heading": "Paints, varnishes, lacquers, and coatings"},
    {"class": "3", "heading": "Cosmetics and cleaning preparations"},
    {"class": "4", "heading": "Industrial oils, lubricants, and fuels"},
    {"class": "5", "heading": "Pharmaceuticals and medical preparations"},
    {"class": "6", "heading": "Common metals and their alloys, metal hardware"},
    {"class": "7", "heading": "Machines and machine tools"},
    {"class": "8", "heading": "Hand tools and implements"},
    {"class": "9", "heading": "Scientific, electrical apparatus, and software"},
    {"class": "10", "heading": "Medical and surgical instruments"},
    {"class": "11", "heading": "Lighting, heating, and cooling apparatus"},
    {"class": "12", "heading": "Vehicles and transport apparatus"},
    {"class": "13", "heading": "Firearms and fireworks"},
    {"class": "14", "heading": "Precious metals, jewelry, and watches"},
    {"class": "15", "heading": "Musical instruments"},
    {"class": "16", "heading": "Paper goods and printed matter"},
    {"class": "17", "heading": "Rubber, plastics, and insulating materials"},
    {"class": "18", "heading": "Leather goods and luggage"},
    {"class": "19", "heading": "Non-metallic building materials"},
    {"class": "20", "heading": "Furniture and furnishings"},
    {"class": "21", "heading": "Household and kitchen utensils"},
    {"class": "22", "heading": "Ropes, nets, tents, and sacks"},
    {"class": "23", "heading": "Yarns and threads"},
    {"class": "24", "heading": "Textiles and fabric goods"},
    {"class": "25", "heading": "Clothing, footwear, and headwear"},
    {"class": "26", "heading": "Lace, ribbons, buttons, and sewing goods"},
    {"class": "27", "heading": "Carpets and floor coverings"},
    {"class": "28", "heading": "Games, toys, and sporting goods"},
    {"class": "29", "heading": "Meat, fish, and processed foods"},
    {"class": "30", "heading": "Coffee, flour, bread, and confectionery"},
    {"class": "31", "heading": "Fresh produce, seeds, and live animals"},
    {"class": "32", "heading": "Beers and non-alcoholic beverages"},
    {"class": "33", "heading": "Wines and spirits"},
    {"class": "34", "heading": "Tobacco and smokers' articles"},
    {"class": "35", "heading": "Advertising and business management"},
    {"class": "36", "heading": "Insurance and financial services"},
    {"class": "37", "heading": "Construction and repair services"},
    {"class": "38", "heading": "Telecommunications"},
    {"class": "39", "heading": "Transport and storage services"},
    {"class": "40", "heading": "Material treatment services"},
    {"class": "41", "heading": "Education and entertainment services"},
    {"class": "42", "heading": "Scientific/tech services and software design"},
    {"class": "43", "heading": "Restaurant and hotel services"},
    {"class": "44", "heading": "Medical, veterinary, and beauty services"},
    {"class": "45", "heading": "Legal and personal services"},
]

NICE_CLASS_BY_NUMBER: dict[str, str] = {c["class"]: c["heading"] for c in NICE_CLASSES}
VALID_NICE_CLASSES: set[str] = set(NICE_CLASS_BY_NUMBER.keys())
