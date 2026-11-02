"""Moteur de détection des vraies erreurs de prix (score 0-100).

Règles :
  - Arbitrage inter-marchés : real_discount = (médiane marché - prix) / médiane marché.
    >= 55 % sur une marque connue -> score >= 90, badge « CERTIFIED PRICE GLITCH ».
  - Erreur de virgule / fat finger : prix ~ 10 % (ou 1 %) de la médiane marché, ou
    prix x 10 dans la fourchette habituelle de la famille de produits (GPU à 79 € ...).
  - Filtre déchets / dropshipping : accessoires sans marque, coques, câbles, clés
    numériques, vendeurs tiers non vérifiés, plateformes de dropshipping, prix barrés gonflés.
  - Mémoire de prix (history_low, fourni par history.py) : chute éclair.
  - Votes communautaires : température et vitesse de vote (°/h).

Le prix barré du marchand n'est jamais considéré comme une preuve : il ne sert que de
référence « non vérifiée », pondérée à 60 %.

Module pur (aucun accès réseau ni disque).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .reference_catalog import _CONSOLE as CONSOLE_NAMING
from .reference_catalog import lookup as catalog_lookup

# --- Référentiels ---------------------------------------------------------------

BRANDS = [
    "Apple", "Sony", "Samsung", "Asus", "LG", "Lenovo", "HP", "Dell", "Acer", "MSI",
    "Microsoft", "Nintendo", "Xiaomi", "Google", "Philips", "Bose", "JBL", "Dyson",
    "Bosch", "Siemens", "Nvidia", "AMD", "Intel", "Logitech", "Razer", "Canon", "Nikon",
    "Panasonic", "Garmin", "DJI", "Huawei", "OnePlus", "Oppo", "Seagate",
    "Western Digital", "WD", "SanDisk", "Kingston", "Corsair", "Gigabyte", "Lego",
    "Playmobil", "Makita", "DeWalt", "Tefal", "Moulinex", "Rowenta", "Krups",
    "De'Longhi", "DeLonghi", "KitchenAid", "Nespresso", "Oral-B", "Nike", "Adidas",
    "Miele", "Whirlpool", "Electrolux", "AEG", "TCL", "Hisense", "Sonos", "Sennheiser",
    "Beats", "Alienware", "Ecovacs", "Roborock", "iRobot", "Hasbro", "Ravensburger",
    "Tineco", "Shark", "Ninja", "Bang & Olufsen", "Marshall", "Fujifilm", "GoPro",
    "Ubisoft", "Electronic Arts", "Bandai", "Thermomix", "Vorwerk", "Karcher", "Kärcher",
    "Netgear", "TP-Link", "Synology", "Withings", "Fitbit", "Jabra", "Dreame", "Babyliss",
    "Insta360", "Valve", "Lexar", "Crucial", "Sabrent", "Steelseries", "HyperX", "Zotac", "Sapphire",
]
# Noms de produits qui impliquent une marque.
PRODUCT_LINES = {
    "iphone": "Apple", "ipad": "Apple", "macbook": "Apple", "airpods": "Apple",
    "imac": "Apple", "apple watch": "Apple", "playstation": "Sony", "ps5": "Sony",
    "xbox": "Microsoft", "surface": "Microsoft", "galaxy": "Samsung", "pixel": "Google",
    "geforce": "Nvidia", "rtx": "Nvidia", "radeon": "AMD", "ryzen": "AMD",
    "nintendo switch": "Nintendo", "thinkpad": "Lenovo", "rog": "Asus", "zenbook": "Asus",
    "bravia": "Sony", "oled evo": "LG", "mac mini": "Apple", "mac studio": "Apple",
    "steam deck": "Valve", "meta quest": "Meta", "quest 3": "Meta", "quest 3s": "Meta",
    "legion go": "Lenovo", "ps vr2": "Sony", "switch 2": "Nintendo", "switch oled": "Nintendo",
    "switch lite": "Nintendo",
}
_BRAND_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(b) for b in sorted(BRANDS, key=len, reverse=True)) + r")(?![\w-])",
    re.IGNORECASE,
)
_LINE_RE = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, PRODUCT_LINES)) + r")(?![\w-])", re.IGNORECASE)

# Enseignes qui vendent elles-mêmes leurs produits.
RETAILERS = [
    "fnac", "darty", "boulanger", "leclerc", "carrefour", "auchan", "media markt",
    "mediamarkt", "saturn", "otto", "argos", "currys", "walmart", "ldlc", "materiel.net",
    "apple", "samsung", "decathlon", "ikea", "lidl", "aldi", "alternate", "cyberport",
    "notebooksbilliger", "john lewis", "very", "ao.com", "leroy merlin", "castorama",
    "galaxus", "digitec", "coolblue", "lego", "nike", "adidas", "intermarché", "conforama",
    "but", "e.leclerc", "electro depot", "top achat", "grosbill", "rue du commerce",
    "tesco", "sainsbury", "asda", "game", "smyths", "dell", "lenovo", "hp store",
]
# Marketplaces mêlant vente directe et vendeurs tiers.
MARKETPLACES = ["amazon", "cdiscount", "ebay", "rakuten", "kaufland", "manomano", "allegro"]
# Plateformes où le dropshipping de produits génériques est la norme.
DROPSHIP = ["aliexpress", "temu", "wish", "shein", "banggood", "gearbest", "joom", "dhgate"]

_SELLER = r"([a-z0-9][\w.&' -]{1,30}?)(?=[\s.,;:!)\]]|$)"
DIRECT_SELLER_RES = [
    re.compile(p + _SELLER, re.IGNORECASE) for p in (
        r"(?:vendu et exp[ée]di[ée]|exp[ée]di[ée] et vendu) par\s+",
        r"(?:verkauf und versand|versand und verkauf) (?:durch|von|über)\s+",
        r"verkauft und versendet (?:durch|von)\s+",
        r"(?:dispatched|shipped) (?:from )?and sold by\s+",
        r"sold and (?:dispatched|shipped|fulfilled) by\s+",
    )
]
THIRD_PARTY_RE = re.compile(
    r"vendeur tiers|vendeur partenaire|marketplace|drittanbieter|dritthändler|"
    r"third[- ]party seller|\bvendeur\s+(?!officiel)[\w.&' -]+\)",
    re.IGNORECASE,
)
_TITLE_SELLER_RE = re.compile(r"\((?:vendeur|verkäufer|seller)\s+([^)]+)\)", re.IGNORECASE)

ACCESSORY_RE = re.compile(
    r"\b(?:coques?|[ée]tuis?|housses?|h[üu]lle|case|cover|protection [ée]cran|"
    r"verre tremp[ée]|panzerglas|screen protector|c[âa]ble|kabel|cable|chargeur|"
    r"ladeger[äa]t|charger|adaptateur|adapter|support t[ée]l[ée]phone|bracelet|"
    r"polster|ohrpolster|ear ?pads?|coussinets?|ersatz|replacement|kickstand|"
    r"cartouches?|druckerpatronen?|toner|ink)\b",
    re.IGNORECASE,
)

# --- Seuils ---------------------------------------------------------------------

CRITICAL_SCORE = 80
HOT_SCORE = 50
CERTIFIED_SCORE = 90
CERTIFIED_DISCOUNT = 0.55        # ≥ 55 % sous la médiane du marché sur une marque connue
RESALE_FEES = 0.15               # commissions + frais de port estimés à la revente
FRESH_MINUTES = 2                # badge « posté il y a < 2 min »
FRESH_ACTIVE_MINUTES = 30        # is_fresh
ALERT_MAX_AGE_MINUTES = 24 * 60  # pas d'alerte pour une annonce de plus de 24 h
FLASH_DROP_RATIO = 0.6           # prix ≤ 60 % du plus bas déjà vu pour ce produit
DECIMAL_RATIOS = (0.1, 0.01)        # prix ≈ 10 % ou 1 % du prix de référence
DECIMAL_TOLERANCE = 0.15            # ± 15 % autour du ratio
INFLATED_FACTOR = 1.8               # prix barré > 1,8 × meilleur prix ailleurs
NONAME_INFLATED_DISCOUNT = 80       # « 120 € → 12 € » sur un produit sans marque
CERTIFIED_MIN_MARKET = 50           # pas de certification pour un article de moins de 50 €
SPARE_PART_MAX_PRICE = 50           # pièce / accessoire ≤ 50 € : rejet ; au-delà, pas de bonus de marque


# --- Briques élémentaires ---------------------------------------------------------

_COMPAT_RE = re.compile(r"\b(?:pour|f[üu]r|for|compatible|kompatibel|passend|fits|avec|mit)\b", re.IGNORECASE)


def find_brand(text: str, accessory: bool = False) -> str | None:
    """Marque du produit. Pour un accessoire, seule la partie avant « pour / für / compatible… »
    compte, et un nom de produit (« iPhone ») n'implique pas la marque : une coque « pour iPhone »
    n'est pas un produit Apple."""
    if accessory:
        text = _COMPAT_RE.split(text, maxsplit=1)[0]
    match = _BRAND_RE.search(text)
    if match:
        found = match.group(1).lower()
        return next(b for b in BRANDS if b.lower() == found)
    if accessory:
        return None
    match = _LINE_RE.search(text)
    return PRODUCT_LINES[match.group(1).lower()] if match else None


def _matches(name: str, needles: list[str]) -> bool:
    name = name.lower()
    return any(re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", name) for n in needles)


def classify_seller(merchant: str | None, title: str, description: str) -> str:
    """Retourne « direct », « retailer », « marketplace », « third_party », « dropship » ou « unknown »."""
    merchant = merchant or ""
    text = f"{title} {description}"
    if _matches(merchant, DROPSHIP):
        return "dropship"
    for regex in DIRECT_SELLER_RES:
        for match in regex.finditer(text):
            seller = match.group(1)
            if _matches(seller, RETAILERS + MARKETPLACES) or (merchant and merchant.lower().split(".")[0] in seller.lower()):
                return "direct"
    title_seller = _TITLE_SELLER_RE.search(title)
    if title_seller:
        # « (Vendeur Darty) » sur la Fnac : tiers, mais enseigne reconnue.
        return "retailer" if _matches(title_seller.group(1), RETAILERS) else "third_party"
    if THIRD_PARTY_RE.search(text):
        return "third_party"
    if _matches(merchant, RETAILERS):
        return "retailer"
    if _matches(merchant, MARKETPLACES):
        return "marketplace"
    return "unknown"


def is_decimal_error(price: float | None, reference: float | None) -> bool:
    """GPU à 79 € au lieu de 790 € : le prix vaut ≈ 10 % (ou 1 %) de la référence."""
    if not price or not reference or price >= reference:
        return False
    ratio = price / reference
    return any(abs(ratio - r) <= r * DECIMAL_TOLERANCE for r in DECIMAL_RATIOS)


def hours_since(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max((now - then).total_seconds() / 3600, 0.0)


def vote_velocity(temperature: float | None, published: str | None, now: datetime) -> float | None:
    """Degrés gagnés par heure depuis la publication (au moins 30 min pour lisser)."""
    age = hours_since(published, now)
    if temperature is None or age is None:
        return None
    return temperature / max(age, 0.5)


def _discount_points(discount: int | None) -> float:
    if discount is None:
        return 0.0
    return min(max((discount - 30) / 60 * 40, 0.0), 40.0)


def _vote_points(temperature: float | None, velocity: float | None) -> int:
    if temperature is None:
        return 0
    if temperature < 0:
        return -15
    speed = next((p for v, p in ((200, 20), (100, 16), (50, 12), (20, 8), (5, 4)) if (velocity or 0) >= v), 0)
    heat = next((p for t, p in ((1000, 16), (500, 12), (200, 8), (100, 4)) if temperature >= t), 0)
    return max(speed, heat)


DIGITAL_RE = re.compile(
    r"\b(?:cl[ée]s? (?:steam|cd|activation|windows|office)|cd[- ]?keys?|game keys?|"
    r"steam keys?|activation keys?|product keys?|lizenzschl[üu]ssel|key (?:steam|global)|"
    r"(?:windows|office) \d+ (?:pro |home |professional )?(?:key|cl[ée]|lizenz)|"
    r"gift ?cards?|cartes? cadeaux?|gutscheine?)\b",
    re.IGNORECASE,
)
# Contenu dématérialisé (jeux en téléchargement, monnaies virtuelles, abonnements).
DIGITAL_CONTENT_RE = re.compile(
    r"d[ée]mat[ée]rialis[ée]|\bdigital(?:e)? (?:code|version|edition|download)|download[- ]?code|"
    r"code de t[ée]l[ée]chargement|\bdlc\b|\be-?shop\b|\bps store\b|playstation store|xbox store|"
    r"microsoft store|\bsteam\b(?!\s?deck)|epic games|game ?pass|minecoins|v-?bucks|\bgog\b|ubisoft connect",
    re.IGNORECASE,
)
DIGITAL_MERCHANTS = [
    "xbox store", "microsoft store", "playstation store", "ps store", "eshop", "steam", "epic games",
    "gog", "ubisoft store", "ea app", "instant gaming", "eneba", "g2a", "kinguin", "cdkeys", "gamivo",
    "humble", "fanatical", "battle.net", "google play", "app store",
]
# Noms ambigus (la boutique physique existe aussi) : seulement s'ils sont le nom exact du marchand.
DIGITAL_MERCHANTS_EXACT = {"xbox", "playstation", "nintendo", "bandai namco", "ubisoft", "microsoft"}

# Abonnements, forfaits, contrats, services : on ne chasse que des biens physiques.
SUBSCRIPTION_RE = re.compile(
    r"\bforfaits?\b|\btarifs?\b|tarifwelt|\bvertrag\b|vertr[äa]ge|laufzeit|\bsim[- ]?only\b|\bsim[- ]?karte\b|"
    r"\besim\b|\babonnements?\b|\babo\b|\bsubscription\b|monatlich|\bmtl\.|"
    r"(?:€|eur|£)\s?/\s?(?:mois|monat|month|mo)\b|/\s?(?:mois|monat|month)\b|"
    r"\bpar mois\b|\bpro monat\b|\bper month\b|\ba month\b|\b\d+\s?(?:monate|mois|months)\b|"
    r"\ballnet\b|\bflatrate\b|\bdatenvolumen\b|\bairtime\b|\bunlimited data\b|"
    r"\bbox internet\b|\boffre (?:fibre|adsl|box)\b|\bglasfaser\b|\bdsl\b|\bbroadband\b|"
    r"\bvpn\b|\bstromtarif|\bökostrom|\bgastarif|\bfournisseur d'?(?:électricité|énergie|gaz)\b|"
    r"\bénergie verte\b|\benergy tariff\b|\bleasing\b|\blocation longue\b|\bloa\b|\blld\b|"
    r"\bcompte bancaire\b|\bgirokonto\b|\bbank account\b|\bkreditkarte\b|\bcarte de crédit\b|"
    r"\bcredit card\b|\bprime bancaire\b|\bassurance\b|\bversicherung\b|\binsurance\b|"
    r"\bo2\b|\bvodafone\b|\btelekom\b|\bmagentamobil\b|\bsfr\b|\bbouygues\b|\bfree mobile\b|\bsosh\b",
    re.IGNORECASE,
)
SUBSCRIPTION_MERCHANT_RE = re.compile(
    r"tarif|mobilfunk|\bo2\b|vodafone|telekom|\b1&1\b|congstar|\borange\b|\bsfr\b|bouygues|"
    r"free mobile|\bsosh\b|red by sfr|b&you|\bee\b|\bthree\b|giffgaff|lebara|lycamobile|\bvoxi\b|"
    r"nordvpn|surfshark|expressvpn|cyberghost|\bengie\b|totalenergies|\bedf\b|\be\.on\b|octopus energy|"
    r"\bboursobank\b|\bfortuneo\b|\bhello bank\b|\bn26\b|\brevolut\b|\bcheck24\b|\bverivox\b",
    re.IGNORECASE,
)
# Pièces détachées et consommables : jamais le produit lui-même, donc jamais de bonus de marque.
SPARE_PART_RE = re.compile(
    r"\bcoussinets?\b|\bear ?pads?\b|\bear ?cushions?\b|\bohrpolster\b|\bpolster(?:-?set)?\b|"
    r"\bersatzteile?\b|\bersatz(?:-?\w+)?\b|\breplacement\b|\bremplacement\b|\bpi[èe]ces? d[ée]tach[ée]es?\b|"
    r"\bspare parts?\b|\bbracelets? de montre\b|\bwatch ?bands?\b|\buhrenarmband\b|\barmband\b|\bstraps?\b|"
    r"\b(?:watch|montre|uhr)\s?band\b|\bembouts?\b|\bear ?tips\b|\bfiltres?\b|\bfilter\b|\bbrosses?\b|"
    r"\bcartouches?\b|\bdruckerpatronen?\b|\btintenpatronen?\b|\btoner\b|\bink cartridges?\b|"
    r"\bsacs? aspirateur\b|\bstaubsaugerbeutel\b|\bbracelets?\b|\bcoques?\b|\bh[üu]llen?\b|"
    r"\bschutzfolien?\b|\bschutzglas\b|\bverre tremp[ée]\b|\bfilm de protection\b|\bscreen protectors?\b",
    re.IGNORECASE,
)

# Fourchettes de prix habituelles par famille de produits (détection « prix × 10 »).
CATEGORY_PRICE_RANGES = [
    ("GPU", r"rtx ?\d{4}|radeon rx|carte graphique|grafikkarte|graphics card|geforce", 250, 2500),
    ("PC fixe", r"pc de bureau|pc gamer|gaming pc|desktop pc|\bimac\b", 500, 5000),
    ("Ordinateur portable", r"pc portable|ordinateur portable|laptop|notebook|macbook|zenbook|thinkpad", 350, 4000),
    ("TV", r"\btv\b|t[ée]l[ée]viseur|fernseher|\boled\b|\bqled\b|television", 250, 4000),
    ("Smartphone", r"iphone|galaxy s\d|galaxy z|smartphone|pixel \d", 200, 2000),
    ("Console", r"\bps5\b|playstation 5|xbox series|switch 2|nintendo switch|steam deck", 250, 800),
    ("Tablette", r"\bipad\b|tablette|\btablet\b", 150, 2000),
    ("Aspirateur", r"aspirateur|staubsauger|vacuum|robot laveur|saugroboter", 150, 1200),
    ("Écran", r"[ée]cran pc|moniteur|\bmonitor\b", 120, 2000),
    ("Photo", r"appareil photo|hybride|\bkamera\b|\bcamera\b|objectif", 300, 4000),
    ("Audio premium", r"airpods|casque|kopfh[öo]rer|headphones|wh-1000|quietcomfort", 80, 500),
    ("Montre connectée", r"apple watch|montre connect[ée]e|smartwatch|galaxy watch", 150, 1000),
    ("Café", r"machine [àa] caf[ée]|expresso|kaffeevollautomat|espresso", 200, 1500),
]
_CATEGORY_RES = [(name, re.compile(rx, re.IGNORECASE), lo, hi) for name, rx, lo, hi in CATEGORY_PRICE_RANGES]


def product_family(title: str) -> tuple[str, float, float] | None:
    for name, regex, low, high in _CATEGORY_RES:
        if regex.search(title):
            return name, low, high
    return None


# Jeux, éditions, DLC : « Cyberpunk 2077 sur PS5 » à 35 € n'est pas une PS5 à 350 €.
GAME_RE = re.compile(
    r"\b(?:jeux?|games?|spiel|edition|[ée]dition|ultimate|deluxe|collection|kollektion|kollection|"
    r"remaster(?:ed)?|remake|goty|dlc|season pass|steelbook|amiibo|figurine|"
    r"(?:sur|on|auf|f[üu]r|pour|for)\s+(?:la\s+|die\s+)?(?:ps[45]|playstation\s?[45]|xbox|nintendo|switch))\b",
    re.IGNORECASE,
)


def is_fat_finger(price: float | None, title: str) -> bool:
    """« 89 € au lieu de 890 € » : prix × 10 tombe dans la fourchette habituelle de la famille
    alors que le prix lui-même est très en dessous du bas de fourchette."""
    family = product_family(title)
    if not price or not family or GAME_RE.search(title):
        return False
    if family[0] == "Console" and not re.search(CONSOLE_NAMING, title, re.IGNORECASE):
        return False                        # « EA Sports FC 26 PS5 » : un jeu, pas une console
    _, low, high = family
    return low <= price * 10 <= high and price < low * 0.5


@dataclass
class Analysis:
    score: int
    level: str
    reasons: list[str] = field(default_factory=list)
    rejected: str | None = None
    brand: str | None = None
    seller: str = "unknown"
    market_price: float | None = None
    real_discount: int | None = None
    savings: float | None = None
    resale_margin: float | None = None
    certified: bool = False
    decimal_error: bool = False
    family: str | None = None
    verified: bool = False
    market_source: str | None = None        # pepper / keepa / jsonld / catalog
    catalog_name: str | None = None
    age_minutes: int | None = None
    is_fresh: bool = False
    active: bool = True
    baseline_price: float | None = None     # prix marché / base catalogue, même sans remise
    breakdown: list[dict] = field(default_factory=list)   # détail des points du score

    def as_fields(self) -> dict:
        return {
            "score": self.score,
            "level": self.level,
            "reasons": self.reasons,
            "rejected": self.rejected,
            "brand": self.brand,
            "seller": self.seller,
            "market_price": self.market_price,
            "market_source": self.market_source,
            "baseline_price": self.baseline_price,
            "score_breakdown": self.breakdown,
            "catalog_name": self.catalog_name,
            "real_discount": self.real_discount,
            "savings": self.savings,
            "resale_margin": self.resale_margin,
            "certified": self.certified,
            "decimal_error": self.decimal_error,
            "family": self.family,
            "price_verified": self.verified,
            "age_minutes": self.age_minutes,
            "is_fresh": self.is_fresh,
            "active": self.active,
        }


def real_discount_ratio(market_median: float | None, price: float | None) -> float | None:
    """(médiane marché − prix) / médiane marché."""
    if not market_median or price is None or market_median <= 0:
        return None
    return (market_median - price) / market_median


def resale_margin(market_median: float | None, price: float | None) -> float | None:
    """Marge de revente estimée : médiane marché moins frais de revente, moins prix payé."""
    if not market_median or price is None:
        return None
    return round(market_median * (1 - RESALE_FEES) - price, 2)


def certified_score(ratio: float) -> int:
    """90 à −55 %, puis +1 point par 2 % de remise supplémentaire (−73 % → 99), plafonné à 100."""
    return int(min(100, CERTIFIED_SCORE + 50 * (ratio - CERTIFIED_DISCOUNT)))


def _external_source(deal: dict) -> str | None:
    sources = deal.get("market_sources") or []
    if sources:
        return "+".join(sources)
    return "pepper" if deal.get("next_best_price") else None


def analyze(deal: dict, now: datetime | None = None) -> Analysis:
    now = now or datetime.now(timezone.utc)
    title = deal.get("title") or ""
    description = deal.get("description") or ""
    price = deal.get("price")
    barred = deal.get("original_price")
    temperature = deal.get("temperature")

    # Prix marché : sources externes d'abord, catalogue de référence en dernier recours —
    # quel que soit l'âge de l'annonce.
    external = deal.get("market_median") or deal.get("next_best_price")
    catalog = None if external else catalog_lookup(title, deal.get("currency") or "EUR")
    market = external or (catalog.price if catalog else None)
    market_source = _external_source(deal) if external else ("catalog" if catalog else None)

    accessory = bool(ACCESSORY_RE.search(title))
    brand = (find_brand(title, accessory) or find_brand(deal.get("merchant") or "", accessory)
             or find_brand(deal.get("live_brand") or "", accessory))
    seller = classify_seller(deal.get("merchant"), title, description)
    if deal.get("amazon_seller") == "amazon":
        seller = "direct"
    family = product_family(title)
    family_name = catalog.family if catalog else (family[0] if family else None)

    published = deal.get("published") or deal.get("first_seen")
    age = hours_since(published, now)
    age_minutes = int(age * 60) if age is not None else None
    is_fresh = age_minutes is not None and age_minutes < FRESH_ACTIVE_MINUTES
    active = not deal.get("expired") and not deal.get("live_corrected")

    # Le prix barré du marchand n'est jamais une preuve : seul un prix marché « vérifie ».
    verified = bool(market and price is not None and market > price)
    externally_verified = verified and bool(external)
    inflated = bool(barred and market and barred > INFLATED_FACTOR * market)
    reference = market if verified else (barred if barred and price is not None and barred > price else None)
    ratio = real_discount_ratio(reference, price)
    discount = round(100 * ratio) if ratio is not None else None
    barred_discount = round(100 * (1 - price / barred)) if barred and price is not None and barred > price else None
    savings = round(market - price, 2) if verified else None
    margin = resale_margin(market, price) if verified else None
    market_price = market if verified else None
    catalog_name = catalog.name if catalog else None

    def rejected(reason: str) -> Analysis:
        return Analysis(0, "rejected", [reason], reason, brand, seller, market_price, discount,
                        savings, margin, family=family_name, verified=verified, market_source=market_source,
                        catalog_name=catalog_name, age_minutes=age_minutes, is_fresh=is_fresh, active=active,
                        baseline_price=market, breakdown=[{"label": f"Rejetée : {reason}", "points": 0}])

    # Filtre strict : biens physiques avec un prix exploitable uniquement.
    merchant_name = deal.get("merchant") or ""
    if price is None or price <= 0:
        return rejected("Prix absent ou nul")
    if SUBSCRIPTION_RE.search(title) or SUBSCRIPTION_MERCHANT_RE.search(merchant_name):
        return rejected("Abonnement / forfait / service")
    spare_part = bool(SPARE_PART_RE.search(title))
    physical = catalog_lookup(title, "EUR") is not None     # Steam Deck vendu sur Steam : physique
    if (DIGITAL_RE.search(title) or DIGITAL_CONTENT_RE.search(title)
            or (not physical and (_matches(merchant_name, DIGITAL_MERCHANTS)
                                  or merchant_name.strip().lower() in DIGITAL_MERCHANTS_EXACT))):
        return rejected("Clé / code numérique")
    if seller == "dropship" and not brand:
        return rejected("Dropshipping sans marque")
    if not brand and accessory:
        return rejected("Accessoire générique")
    if spare_part and price <= SPARE_PART_MAX_PRICE:
        return rejected("Pièce détachée / consommable")
    if not brand and seller not in ("direct", "retailer"):
        return rejected("Ni marque reconnue ni vendeur réputé")
    # « RTX 5070 à 165 € chez un vendeur tiers » est l'appât d'arnaque classique : le catalogue
    # ne suffit pas, il faut un prix marché externe.
    if seller == "third_party" and not externally_verified:
        return rejected("Vendeur tiers non vérifié")
    if not brand and (inflated or (not verified and (barred_discount or 0) >= NONAME_INFLATED_DISCOUNT)):
        return rejected("Prix barré gonflé")

    reasons: list[str] = []
    breakdown: list[dict] = []
    points = 0.0

    def add(label: str, value: float) -> None:
        nonlocal points
        if value:
            points += value
            breakdown.append({"label": label, "points": round(value, 1)})

    # « Prix × 10 » par famille : seulement si aucun prix marché ne montre un prix ordinaire
    # (un ruban LED « pour TV 55" » à 102 € n'est pas une TV à 1 020 €).
    market_ratio = price / market if verified and price is not None else None
    fat_finger = is_fat_finger(price, title) and (market_ratio is None or market_ratio <= 0.3)
    decimal = is_decimal_error(price, reference) or fat_finger
    # Certification réservée aux vrais produits de valeur (pas aux accessoires / pièces / consommables).
    certified = bool(brand and verified and not accessory and not spare_part and market >= CERTIFIED_MIN_MARKET
                     and ratio is not None and ratio >= CERTIFIED_DISCOUNT)

    if discount is not None:
        basis = {"catalog": "base catalogue"}.get(market_source or "", "prix marché") if verified else "prix barré (×0,6)"
        add(f"Remise de {discount} % vs {basis}", _discount_points(discount) * (1.0 if verified else 0.6))
    if certified:
        reasons.append("✅ CERTIFIED PRICE GLITCH")
    if decimal:
        add("Erreur de virgule / fat finger", 20)
        reasons.append("⚡ Erreur de virgule")
    if verified and discount is not None and discount > 0:
        reasons.append(f"📉 -{discount}% sous le prix marché")
    elif reference and not verified:
        reasons.append("Prix barré non vérifié")
    if catalog and verified:
        reasons.append(f"📚 Base catalogue : {catalog.name}")
    if deal.get("merchant_url"):
        reasons.append("🔗 URL marchande résolue")
    if inflated:
        reasons.append("Prix barré gonflé")

    if seller == "direct":
        add("Vendeur officiel (vendu et expédié par l'enseigne)", 10)
        reasons.append("🏪 Vendeur officiel")
    elif seller == "retailer":
        add("Enseigne réputée", 7)
        reasons.append("🏪 Enseigne réputée")
    elif seller == "marketplace":
        add("Marketplace (vendeur non précisé)", 3)
    elif seller == "third_party":
        add("Vendeur tiers", -10)
        reasons.append("Vendeur tiers")
    if brand and not spare_part:
        add(f"Marque reconnue ({brand})", 15)
        reasons.append(f"Marque reconnue ({brand})")
    elif spare_part:
        reasons.append("Pièce / accessoire (> 50 €)")
    if deal.get("glitch_keyword"):
        add("Signalée comme erreur de prix", 5)

    history_low = deal.get("history_low")
    if history_low and price is not None and price <= FLASH_DROP_RATIO * history_low:
        add("Chute éclair sous le plus bas historique", 10)
        reasons.append("💥 Chute éclair")

    velocity = vote_velocity(temperature, published, now)
    vote = _vote_points(temperature, velocity)
    if temperature is not None:
        speed = f", {velocity:.0f}°/h" if velocity is not None else ""
        add(f"Votes communautaires ({temperature:.0f}°{speed})", vote)
    if vote < 0:
        reasons.append("Votes négatifs")
    elif velocity is not None and velocity >= 50:
        reasons.append("📈 Votes rapides")
    if age_minutes is not None and age_minutes < FRESH_MINUTES:
        reasons.append(f"🔥 Posté il y a < {FRESH_MINUTES} min")

    if certified and certified_score(ratio) > points:
        add(f"Plancher CERTIFIED (−{discount} % sur une marque connue)", certified_score(ratio) - points)
    if vote < 0 and points > 60:
        add("Plafond : la communauté conteste l'erreur", 60 - points)
    if points > 100:
        add("Plafond à 100", 100 - points)

    # L'expiration ne modifie pas le score intrinsèque : elle est signalée à part.
    if deal.get("expired"):
        reasons.append("[EXPIRÉ]")
    if deal.get("live_corrected"):
        reasons.append("⚠️ Prix déjà corrigé chez le marchand ?")
    elif deal.get("live_available") is False:
        reasons.append("Rupture de stock")

    score = int(round(min(max(points, 0), 100)))
    if score >= CRITICAL_SCORE:
        level = "critical"
    elif score >= HOT_SCORE or (discount or 0) >= 50:
        level = "hot"
    else:
        level = "normal"
    return Analysis(score, level, reasons, None, brand, seller, market_price, discount, savings,
                    margin, certified, decimal, family_name, verified, market_source, catalog_name,
                    age_minutes, is_fresh, active, market, breakdown)


def is_candidate(deal: dict, now: datetime | None = None) -> bool:
    """Une annonce du flux « nouveautés » mérite-t-elle d'être gardée ?

    Oui si elle vient du groupe erreur de prix, contient un mot-clé d'erreur, ou présente
    un ratio de remise extrême / une erreur de virgule sur une marque connue."""
    if deal.get("stream", "glitch") == "glitch" or deal.get("glitch_keyword"):
        return True
    analysis = analyze(deal, now)
    if analysis.rejected or not analysis.brand:
        return False
    return (analysis.real_discount or 0) >= 50 or analysis.decimal_error


def qualify(deal: dict, now: datetime | None = None) -> dict:
    """Ajoute les champs d'analyse à l'annonce (en place) et la renvoie."""
    deal.update(analyze(deal, now).as_fields())
    return deal


def should_alert(deal: dict, threshold: int = CRITICAL_SCORE, max_age_minutes: int = ALERT_MAX_AGE_MINUTES) -> bool:
    """Alerte : score > seuil, annonce active, et assez récente pour être encore exploitable."""
    age = deal.get("age_minutes")
    return (
        deal.get("score", 0) > threshold
        and not deal.get("rejected")
        and not deal.get("expired")
        and not deal.get("live_corrected")
        and (age is None or age <= max_age_minutes)
    )
