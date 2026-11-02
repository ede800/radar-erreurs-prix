"""Catalogue de prix de référence : prix marché *minimum réaliste* des produits très demandés.

Sert de base de comparaison quand aucun prix marché externe n'est disponible (annonce postée
il y a quelques secondes, « meilleur prix ailleurs » non renseigné, annonce ancienne…).
Une RTX 5070 à 165 € face à une base de 620 € = −73 % → erreur de prix certifiée.

Trois niveaux, dans cet ordre :
  1. **stockage** par capacité (SSD / NVMe / microSD / SD / SSD portable de marque) ;
  2. **PC et portables complets** : base du GPU cité + 450 € ;
  3. **produits nommés** (plus de 110 entrées), puis **TV / écrans OLED** par diagonale.

Prix en euros, planchers neufs constatés fin 2026 : à mettre à jour à la main. Les entrées
spécifiques précèdent les génériques (5070 Ti avant 5070, 16 Pro Max avant 16 Pro).

Garde-fous : jamais d'accessoire (« coque pour AirPods Pro 2 »), de jeu (« Elden Ring PS5 »),
de forfait, d'abonnement ou de location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Conversion approximative des prix plancher pour les autres devises (prix UK ≈ 0,87 × prix €).
CURRENCY_FACTORS = {"EUR": 1.0, None: 1.0, "GBP": 0.87}
REFURBISHED_FACTOR = 0.7
PREBUILT_GPU_PREMIUM = 450          # PC / portable complet = GPU + 450 €

_ACCESSORY = (
    r"coques?|cases?|h[üu]llen?|[ée]tuis?|housses?|sleeve|cover|protection|protège|schutz|"
    r"verre tremp[ée]|panzerglas|film|folie|c[âa]bles?|kabel|chargeurs?|ladeger[äa]t|charger|"
    r"supports?|stand|halterung|skins?|stickers?|bracelets?|armband|straps?|adaptateurs?|adapter|"
    r"docks?|docking|ersatz|replacement|remplacement|filtres?|filter|brosses?|b[üu]rsten?|"
    r"akku|batter(?:y|ie|ies)|polster|ear ?pads?|coussinets?|embouts?|tips|ohrpolster|"
    r"manettes?|controllers?|dualsense|joy-?con|t[ée]l[ée]commande|remote|"
    r"refroidissement|cooler|ventirad|backplate|waterblock|riser|"
    r"attachments?|aufsatz|head ?strap|grip|"
    r"jeux?|spiel|game|games|"
    r"compatible|kompatibel|passend|"
    r"location|leasing|abonnement|abo|forfait|vertrag|contract|tarif|sim only|mit vertrag"
)
ACCESSORY_RE = re.compile(rf"\b(?:{_ACCESSORY})\b", re.IGNORECASE)
COMPLETE_PC_RE = re.compile(
    r"\b(?:pc|laptop|notebook|portable|ordinateur|desktop|rechner|komplett|tour|aurora|alienware|"
    r"gaming-pc|mini-pc|barebone|zenbook|vivobook|legion|omen|predator|nitro|razer blade|"
    r"rog strix [gs]\d+|rog zephyrus|tuf gaming [af]\d+|victus|katana|stealth|raider|titan|"
    r"ideapad|yoga|thinkpad|inspiron|xps|aspire|swift|galaxy book)\b",
    re.IGNORECASE,
)
LAPTOP_RE = re.compile(
    r"\b(?:laptop|notebook|portable|zenbook|vivobook|zephyrus|razer blade|victus|katana|stealth|"
    r"ideapad|yoga|thinkpad|inspiron|xps|aspire|swift|galaxy book|legion (?:pro )?\d|tuf gaming [af]\d+)\b",
    re.IGNORECASE,
)
PREBUILT_EXCLUDE_RE = re.compile(
    r"\b(?:supports?|stand|halterung|c[âa]bles?|kabel|riser|backplate|waterblock|housses?|sacoches?|sleeve|"
    r"coques?|skins?|stickers?|chargeurs?|charger|netzteil|alimentation|bo[iî]tier|geh[äa]use|case|"
    r"ventirad|cooler|pour|f[üu]r|for|compatible|kompatibel|passend|jeux?|game|location|leasing|forfait|vertrag)\b",
    re.IGNORECASE,
)
REFURBISHED_RE = re.compile(
    r"reconditionn[ée]|refurbished|occasion|generalüberholt|gebraucht|used|seconde main|grade [abc]\b",
    re.IGNORECASE,
)

# Une console doit être nommée comme telle : « Elden Ring PS5 » est un jeu, pas une PS5.
_CONSOLE = (r"\b(?:console|konsole|pack|bundle|slim|digital|digitale|standard|lecteur|laufwerk|disc|"
            r"\d\s?(?:to|tb|go|gb))\b|^\s*(?:sony\s+|nintendo\s+|microsoft\s+|console\s+)?"
            r"(?:ps5|playstation\s?5|switch|xbox)")

# --- Stockage par capacité ------------------------------------------------------------------
STORAGE_KIND_RE = re.compile(
    r"\b(?:ssd|nvme|m\.2|microsd(?:xc|hc)?|micro sd|sdxc|sdhc|carte sd|carte m[ée]moire|sd card|"
    r"memory card|speicherkarte|portable ssd|ssd externe|externe ssd)\b",
    re.IGNORECASE,
)
STORAGE_BRAND_RE = re.compile(
    r"\b(?:samsung|sandisk|wd|western digital|crucial|lexar|kingston|seagate|sabrent|pny|corsair|"
    r"teamgroup|adata|solidigm|transcend|verbatim)\b",
    re.IGNORECASE,
)
STORAGE_EXCLUDE_RE = re.compile(
    r"\b(?:hdd|disque dur|festplatte|hard drive|bo[iî]tier|geh[äa]use|enclosure|lecteur de cartes?|"
    r"card reader|kartenleser|c[âa]bles?|kabel|nas|serveur)\b",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?(to|tb|go|gb)\b", re.IGNORECASE)
STORAGE_PRICES = [(4096, 230), (2048, 120), (1024, 65), (512, 35), (256, 20)]   # Go → €

# --- Dalles OLED par diagonale ----------------------------------------------------------------
_SIZE_RE = re.compile(r"\b(\d{2})(?:[.,]\d)?\s?(?:\"|''|”|″|pouces|zoll|inch|inches|-inch|-zoll|in\b)", re.IGNORECASE)
_OLED_RE = re.compile(r"\b(?:q?d?-?oled|woled)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Reference:
    name: str
    family: str
    price: float
    pattern: str
    component: bool = False           # GPU / CPU : ne s'applique pas tel quel à un PC complet
    requires: str | None = None       # motif supplémentaire obligatoire (ex. « console »)


def R(name, family, price, pattern, **kw) -> Reference:
    return Reference(name, family, price, pattern, **kw)


GPU, CPU = "GPU", "Processeur"
CATALOG: list[Reference] = [
    # --- GPU -------------------------------------------------------------------------------
    R("RTX 5090", GPU, 2200, r"rtx\s?5090", component=True),
    R("RTX 5080", GPU, 1150, r"rtx\s?5080", component=True),
    R("RTX 5070 Ti", GPU, 880, r"rtx\s?5070\s?ti\b", component=True),
    R("RTX 5070", GPU, 620, r"rtx\s?5070(?!\s?ti)\b", component=True),
    R("RTX 5060 Ti", GPU, 400, r"rtx\s?5060\s?ti\b", component=True),
    R("RTX 5060", GPU, 300, r"rtx\s?5060(?!\s?ti)\b", component=True),
    R("RTX 4090", GPU, 1800, r"rtx\s?4090", component=True),
    R("RTX 4080", GPU, 950, r"rtx\s?4080", component=True),
    R("RTX 4070 Ti", GPU, 750, r"rtx\s?4070\s?ti\b", component=True),
    R("RTX 4070", GPU, 550, r"rtx\s?4070(?!\s?ti)\b", component=True),
    R("RTX 4060 Ti", GPU, 380, r"rtx\s?4060\s?ti\b", component=True),
    R("RTX 4060", GPU, 280, r"rtx\s?4060(?!\s?ti)\b", component=True),
    R("RX 9070 XT", GPU, 680, r"\brx\s?9070\s?xt\b", component=True),
    R("RX 9070", GPU, 600, r"\brx\s?9070(?!\s?xt)\b", component=True),
    R("RX 7900 XTX", GPU, 850, r"\brx\s?7900\s?xtx\b", component=True),
    R("RX 7800 XT", GPU, 450, r"\brx\s?7800\s?xt\b", component=True),
    # --- CPU -------------------------------------------------------------------------------
    R("Ryzen 9 9950X3D", CPU, 650, r"\b9950\s?x3d\b", component=True),
    R("Ryzen 7 9800X3D", CPU, 450, r"\b9800\s?x3d\b", component=True),
    R("Ryzen 9 9950X", CPU, 550, r"\b9950x\b(?!3d)", component=True),
    R("Core Ultra 9 285K", CPU, 550, r"\bultra\s?9\s?285k\b", component=True),
    # --- PC & portables nommés ------------------------------------------------------------------
    R("ThinkPad X1 Carbon", "Ordinateur portable", 1300, r"\bthinkpad\s?x1\s?carbon\b"),
    R("ThinkPad T16", "Ordinateur portable", 1000, r"\bthinkpad\s?t16\b"),
    R("ThinkPad T14", "Ordinateur portable", 950, r"\bthinkpad\s?t14s?\b"),
    R("Razer Blade", "Ordinateur portable", 1800, r"\brazer\s?blade\b"),
    R("Alienware Aurora", "PC fixe", 1400, r"\balienware\s?aurora\b"),
    R("Mac mini M4", "PC fixe", 650, r"\bmac\s?mini\b"),
    R("Mac Studio", "PC fixe", 2000, r"\bmac\s?studio\b"),
    R("iMac", "PC fixe", 1300, r"\bimac\b"),
    R("MacBook Pro", "Ordinateur portable", 1600, r"\bmacbook\s?pro\b"),
    R("MacBook Air M3/M4", "Ordinateur portable", 1050, r"\bmacbook\s?air\b.*\bm[345]\b"),
    R("MacBook Air M2", "Ordinateur portable", 850, r"\bmacbook\s?air\b.*\bm2\b"),
    # --- Consoles, PC portables de jeu & VR -------------------------------------------------------
    R("PS5 Pro", "Console", 750, r"\b(?:ps5|playstation\s?5)\s?pro\b", requires=_CONSOLE),
    R("PS5", "Console", 450, r"\b(?:ps5|playstation\s?5)(?:\s?(?:slim|standard|digital|digitale|édition standard))?\b(?!\s?pro)", requires=_CONSOLE),
    R("PlayStation VR2", "VR", 350, r"\b(?:ps\s?vr\s?2|playstation\s?vr\s?2)\b"),
    R("PlayStation Portal", "Console", 200, r"\b(?:playstation|ps)\s?portal\b"),
    R("Xbox Series X", "Console", 450, r"\bxbox\s?series\s?x\b", requires=_CONSOLE),
    R("Xbox Series S", "Console", 280, r"\bxbox\s?series\s?s\b", requires=_CONSOLE),
    R("Nintendo Switch 2", "Console", 420, r"\bswitch\s?2\b", requires=_CONSOLE),
    R("Nintendo Switch OLED", "Console", 290, r"\bswitch\b.*\boled\b|\boled\b.*\bswitch\b", requires=_CONSOLE),
    R("Nintendo Switch Lite", "Console", 180, r"\bswitch\s?lite\b", requires=_CONSOLE),
    R("Steam Deck OLED", "Console", 520, r"\bsteam\s?deck\b.*\boled\b|\boled\b.*\bsteam\s?deck\b"),
    R("Steam Deck", "Console", 380, r"\bsteam\s?deck\b"),
    R("ROG Ally X", "Console", 750, r"\brog\s?ally\s?x\b"),
    R("ROG Ally", "Console", 450, r"\brog\s?ally\b(?!\s?x)"),
    R("Legion Go", "Console", 600, r"\blegion\s?go\b"),
    R("Meta Quest 3S", "VR", 280, r"\bquest\s?3s\b"),
    R("Meta Quest 3", "VR", 480, r"\bquest\s?3\b(?!s)"),
    # --- Apple ------------------------------------------------------------------------------
    R("iPhone 17 Pro Max", "Smartphone", 1300, r"\biphone\s?17\s?pro\s?max\b"),
    R("iPhone 17 Pro", "Smartphone", 1150, r"\biphone\s?17\s?pro\b(?!\s?max)"),
    R("iPhone Air", "Smartphone", 1000, r"\biphone\s?air\b"),
    R("iPhone 17", "Smartphone", 850, r"\biphone\s?17\b(?!\s?(?:pro|plus|e)\b)"),
    R("iPhone 16 Pro Max", "Smartphone", 1300, r"\biphone\s?16\s?pro\s?max\b"),
    R("iPhone 16 Pro", "Smartphone", 1050, r"\biphone\s?16\s?pro\b(?!\s?max)"),
    R("iPhone 16 Plus", "Smartphone", 850, r"\biphone\s?16\s?plus\b"),
    R("iPhone 16e", "Smartphone", 550, r"\biphone\s?16\s?e\b"),
    R("iPhone 16", "Smartphone", 820, r"\biphone\s?16\b(?!\s?(?:pro|plus|e)\b)"),
    R("iPhone 15 Pro", "Smartphone", 900, r"\biphone\s?15\s?pro\b"),
    R("iPhone 15", "Smartphone", 680, r"\biphone\s?15\b(?!\s?(?:pro|plus)\b)"),
    R("iPhone 14", "Smartphone", 550, r"\biphone\s?14\b(?!\s?(?:pro|plus)\b)"),
    R("iPad Pro", "Tablette", 1000, r"\bipad\s?pro\b"),
    R("iPad Air", "Tablette", 550, r"\bipad\s?air\b"),
    R("iPad mini", "Tablette", 450, r"\bipad\s?mini\b"),
    R("iPad", "Tablette", 330, r"\bipad\b(?!\s?(?:pro|air|mini)\b)"),
    R("AirPods Max", "Audio", 450, r"\bairpods\s?max\b"),
    R("AirPods Pro 3", "Audio", 230, r"\bairpods\s?pro\s?3\b"),
    R("AirPods Pro 2", "Audio", 210, r"\bairpods\s?pro\s?2\b|\bairpods\s?pro\b.*\b2(?:e|nd|\.)?\s?(?:g[ée]n|gen)"),
    R("AirPods 4", "Audio", 120, r"\bairpods\s?4\b"),
    R("Apple Watch Ultra", "Montre connectée", 780, r"\bapple\s?watch\s?ultra\b|\bwatch\s?ultra\s?\d"),
    R("Apple Watch Series", "Montre connectée", 380, r"\bapple\s?watch\s?(?:series\s?)?(?:10|11)\b"),
    R("Apple Watch SE", "Montre connectée", 230, r"\bapple\s?watch\s?se\b"),
    # --- Samsung & Google -------------------------------------------------------------------------
    R("Galaxy S25 Ultra", "Smartphone", 1000, r"\bgalaxy\s?s2[56]\s?ultra\b"),
    R("Galaxy S25+", "Smartphone", 800, r"\bgalaxy\s?s2[56]\s?(?:\+|plus)"),
    R("Galaxy S25", "Smartphone", 650, r"\bgalaxy\s?s2[56]\b(?!\s?(?:\+|plus|ultra|fe|edge))"),
    R("Galaxy Z Fold", "Smartphone", 1400, r"\b(?:galaxy\s?)?z\s?fold\s?[67]\b"),
    R("Galaxy Z Flip", "Smartphone", 800, r"\b(?:galaxy\s?)?z\s?flip\s?[67]\b"),
    R("Galaxy Tab S10", "Tablette", 700, r"\bgalaxy\s?tab\s?s1[01]\b"),
    R("Galaxy Watch", "Montre connectée", 220, r"\bgalaxy\s?watch\s?[78]\b"),
    R("Galaxy Buds 3 Pro", "Audio", 160, r"\bgalaxy\s?buds\s?3\s?pro\b"),
    R("Pixel Pro", "Smartphone", 800, r"\bpixel\s?(?:9|10)\s?pro\b"),
    R("Pixel", "Smartphone", 600, r"\bpixel\s?(?:9|10)\b(?!\s?(?:pro|a)\b)"),
    # --- Audio ------------------------------------------------------------------------------------
    R("Sony WH-1000XM6", "Audio", 380, r"\bwh-?1000\s?xm6\b"),
    R("Sony WH-1000XM5", "Audio", 290, r"\bwh-?1000\s?xm5\b"),
    R("Sony WF-1000XM5", "Audio", 200, r"\bwf-?1000\s?xm5\b"),
    R("Bose QC Ultra Earbuds", "Audio", 230, r"\bquiet\s?comfort\s?ultra\s?(?:earbuds|ecouteurs|écouteurs)|\bqc\s?ultra\s?earbuds\b"),
    R("Bose QC Ultra Headphones", "Audio", 350, r"\bquiet\s?comfort\s?ultra\b|\bqc\s?ultra\b"),
    R("Sonos Arc Ultra", "Audio", 850, r"\bsonos\s?arc\s?ultra\b"),
    R("Sonos Era 300", "Audio", 400, r"\bsonos\s?era\s?300\b"),
    R("Sonos Era 100", "Audio", 200, r"\bsonos\s?era\s?100\b"),
    # --- Maison -----------------------------------------------------------------------------------
    R("Dyson Gen5", "Aspirateur", 650, r"\bdyson\b.*\bgen\s?5\b"),
    R("Dyson V15", "Aspirateur", 550, r"\bdyson\b.*\bv15\b|\bv15\b.*\bdyson\b"),
    R("Dyson V12", "Aspirateur", 400, r"\bdyson\b.*\bv12\b|\bv12\b.*\bdyson\b"),
    R("Dyson V8", "Aspirateur", 250, r"\bdyson\b.*\bv8\b"),
    R("Dyson Airwrap", "Coiffure", 450, r"\bairwrap\b"),
    R("Dyson Supersonic", "Coiffure", 330, r"\bsupersonic\b"),
    R("Roborock Saros 10", "Aspirateur", 1000, r"\broborock\b.*\bsaros\s?10\b"),
    R("Roborock S8", "Aspirateur", 550, r"\broborock\b.*\bs8\b"),
    R("Ecovacs Deebot X8", "Aspirateur", 800, r"\bdeebot\s?x8\b"),
    R("KitchenAid Artisan", "Cuisine", 380, r"\bkitchenaid\b.*\bartisan\b"),
    R("Thermomix", "Cuisine", 1100, r"\bthermomix\b"),
    R("De'Longhi Dinamica Plus", "Café", 650, r"\bdinamica\s?plus\b"),
    R("De'Longhi Magnifica Evo", "Café", 400, r"\bmagnifica\s?evo\b"),
    R("Philips Hue Starter Kit", "Maison connectée", 110, r"\bhue\b.*\bstarter\s?(?:kit|set|pack)\b"),
    # --- Photo, drones, montres --------------------------------------------------------------------
    R("Garmin Fenix 7/8", "Montre connectée", 500, r"\bf[ée]nix\s?[78]\b"),
    R("Garmin Epix Pro", "Montre connectée", 650, r"\bepix\s?pro\b"),
    R("Garmin Forerunner 965", "Montre connectée", 450, r"\bforerunner\s?965\b"),
    R("GoPro Hero 12/13", "Photo", 320, r"\bgopro\b.*\bhero\s?1[23]\b|\bhero\s?1[23]\s?black\b"),
    R("DJI Mini 3/4 Pro", "Drone", 600, r"\bdji\s?mini\s?[345]\s?pro\b"),
    R("DJI Air 3S", "Drone", 1000, r"\bdji\s?air\s?3s\b"),
    R("DJI Avata 2", "Drone", 450, r"\bavata\s?2\b"),
    R("DJI Osmo Pocket 3", "Photo", 480, r"\bosmo\s?pocket\s?3\b"),
    R("Insta360 X4/X5", "Photo", 450, r"\binsta360\s?x[45]\b"),
    R("Sony A7 IV", "Photo", 1900, r"\b(?:alpha\s?7|a7)\s?(?:iv|4)\b"),
    R("Canon EOS R6 Mark II", "Photo", 1900, r"\beos\s?r6\s?(?:mark\s?ii|mk\s?ii|ii)\b"),
    R("Nikon Z6 III", "Photo", 2000, r"\bz\s?6\s?iii\b"),
    R("Fujifilm X100VI", "Photo", 1600, r"\bx100\s?vi\b"),
    # --- Lego -------------------------------------------------------------------------------------
    R("Lego Millennium Falcon UCS 75192", "Jouet", 650, r"\b75192\b"),
    R("Lego Titanic 10294", "Jouet", 550, r"\b10294\b"),
    R("Lego Rivendell 10316", "Jouet", 420, r"\b10316\b"),
    R("Lego Technic Ferrari Daytona 42143", "Jouet", 380, r"\b42143\b"),
]
_COMPILED = [(ref, re.compile(ref.pattern, re.IGNORECASE),
              re.compile(ref.requires, re.IGNORECASE) if ref.requires else None) for ref in CATALOG]


@dataclass(frozen=True)
class CatalogMatch:
    name: str
    family: str
    price: float          # prix plancher dans la devise de l'annonce
    rule: str = "product"  # product | storage | prebuilt | oled


def _capacity_gb(title: str) -> float | None:
    sizes = []
    for number, unit in _CAPACITY_RE.findall(title):
        value = float(number.replace(",", "."))
        sizes.append(value * 1024 if unit.lower() in ("to", "tb") else value)
    return max(sizes) if sizes else None


def _storage(title: str) -> tuple[str, str, float] | None:
    if not (STORAGE_KIND_RE.search(title) and STORAGE_BRAND_RE.search(title)) or STORAGE_EXCLUDE_RE.search(title):
        return None
    capacity = _capacity_gb(title)
    if not capacity:
        return None
    for size, price in STORAGE_PRICES:
        if capacity >= size * 0.95:
            label = f"{size // 1024} To" if size >= 1024 else f"{size} Go"
            return f"Stockage {label}", "Stockage", price
    return None


def _prebuilt(title: str) -> tuple[str, str, float] | None:
    if not COMPLETE_PC_RE.search(title):
        return None
    for ref, regex, _ in _COMPILED:
        if ref.family == GPU and regex.search(title):
            family = "Ordinateur portable" if LAPTOP_RE.search(title) else "PC fixe"
            return f"{family} {ref.name}", family, ref.price + PREBUILT_GPU_PREMIUM
    return None


def _oled(title: str) -> tuple[str, str, float] | None:
    if not _OLED_RE.search(title):
        return None
    sizes = [int(s) for s in _SIZE_RE.findall(title)]
    size = max(sizes) if sizes else None
    if not size or size < 27:
        return None
    if size <= 34:
        return f"Écran OLED {size}\"", "Écran", 600
    if size <= 55:
        return f"TV OLED {size}\"", "TV", 850
    return f"TV OLED {size}\"", "TV", 1200


def lookup(title: str, currency: str | None = "EUR") -> CatalogMatch | None:
    """Prix de référence du produit du titre, ou ``None`` si inconnu / accessoire / non comparable."""
    if not title or currency not in CURRENCY_FACTORS:
        return None
    found: tuple[str, str, float] | None = None
    rule = "product"
    # Le stockage a ses propres exclusions (« microSD + adaptateur » reste une carte microSD).
    storage = _storage(title)
    # Un PC complet liste ses composants (« refroidissement liquide », « SSD 8 To ») : règle dédiée
    # avant le filtre accessoires, avec ses propres exclusions.
    prebuilt = None if storage or PREBUILT_EXCLUDE_RE.search(title) else _prebuilt(title)
    if storage:
        found, rule = storage, "storage"
    elif prebuilt:
        found, rule = prebuilt, "prebuilt"
    elif ACCESSORY_RE.search(title):
        return None
    else:
        for ref, regex, requires in _COMPILED:
            if not regex.search(title):
                continue
            if requires and not requires.search(title):
                return None
            if ref.component and COMPLETE_PC_RE.search(title):
                continue                    # PC sans GPU identifiable : pas la base d'un composant
            found = (ref.name, ref.family, ref.price)
            break
        if not found:
            oled = _oled(title)
            if oled:
                found, rule = oled, "oled"
    if not found:
        return None
    name, family, price = found
    price *= CURRENCY_FACTORS[currency]
    if REFURBISHED_RE.search(title):
        price *= REFURBISHED_FACTOR
    return CatalogMatch(name, family, round(price, 2), rule)


def catalog_size() -> int:
    return len(CATALOG)
