"""Catalogue de prix de référence : prix marché *minimum réaliste* des produits très demandés.

Sert de base de comparaison quand aucun prix marché externe n'est disponible (annonce postée
il y a quelques secondes, « meilleur prix ailleurs » non renseigné, annonce ancienne…).
Une RTX 5070 à 165 € face à une base de 620 € = −73 % → erreur de prix certifiée.

Les prix sont en euros, prix plancher neuf constatés fin 2026 (à mettre à jour). Les
entrées les plus spécifiques doivent précéder les plus génériques (5070 Ti avant 5070).

Garde-fous : on ne rapproche jamais un accessoire (« coque pour AirPods Pro 2 »), un jeu
(« … pour PS5 »), un PC complet contenant un GPU, un forfait mobile ou une location.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Conversion approximative des prix plancher pour les autres devises (prix UK ≈ 0,87 × prix €).
CURRENCY_FACTORS = {"EUR": 1.0, None: 1.0, "GBP": 0.87}
REFURBISHED_FACTOR = 0.7

_ACCESSORY = (
    r"coques?|cases?|h[üu]llen?|[ée]tuis?|housses?|sleeve|cover|protection|protège|schutz|"
    r"verre tremp[ée]|panzerglas|film|folie|c[âa]bles?|kabel|chargeurs?|ladeger[äa]t|charger|"
    r"supports?|stand|halterung|skins?|stickers?|bracelets?|armband|strap|adaptateurs?|adapter|"
    r"docks?|station|ersatz|replacement|remplacement|filtres?|filter|brosses?|b[üu]rsten?|"
    r"akku|batter(?:y|ie)|polster|ear ?pads?|coussinets?|embouts?|tips|ohrpolster|"
    r"manettes?|controllers?|dualsense|joy-?con|t[ée]l[ée]commande|remote|"
    r"refroidissement|cooler|ventirad|backplate|waterblock|riser|"
    r"attachments?|aufsatz|head ?strap|lens|grip|"
    r"jeux?|spiel|game|games|"
    r"compatible|kompatibel|passend|"
    r"location|leasing|abonnement|abo|forfait|vertrag|contract|tarif|sim only|mit vertrag"
)
ACCESSORY_RE = re.compile(rf"\b(?:{_ACCESSORY})\b", re.IGNORECASE)
COMPLETE_PC_RE = re.compile(
    r"\b(?:pc|laptop|notebook|portable|ordinateur|desktop|rechner|komplett|tour|aurora|"
    r"gaming-pc|mini-pc|barebone|zenbook|vivobook|legion|omen|predator|nitro|rog strix [gs]\d|tuf gaming [af]\d)\b",
    re.IGNORECASE,
)
REFURBISHED_RE = re.compile(
    r"reconditionn[ée]|refurbished|occasion|generalüberholt|gebraucht|used|seconde main|grade [abc]\b",
    re.IGNORECASE,
)


# Une console doit être nommée comme telle : « Elden Ring PS5 » est un jeu, pas une PS5.
_CONSOLE = (r"\b(?:console|konsole|pack|bundle|slim|digital|digitale|standard|lecteur|laufwerk|disc|"
            r"\d\s?(?:to|tb|go|gb))\b|^\s*(?:sony\s+|nintendo\s+|console\s+)?(?:ps5|playstation\s?5|switch)")


@dataclass(frozen=True)
class Reference:
    name: str
    family: str
    price: float
    pattern: str
    gpu: bool = False
    requires: str | None = None      # motif supplémentaire obligatoire (ex. « console »)

    @property
    def regex(self) -> re.Pattern:
        return re.compile(self.pattern, re.IGNORECASE)


CATALOG: list[Reference] = [
    # --- GPU ---------------------------------------------------------------------------
    Reference("RTX 5090", "GPU", 2200, r"rtx\s?5090", gpu=True),
    Reference("RTX 5080", "GPU", 1150, r"rtx\s?5080", gpu=True),
    Reference("RTX 5070 Ti", "GPU", 880, r"rtx\s?5070\s?ti\b", gpu=True),
    Reference("RTX 5070", "GPU", 620, r"rtx\s?5070(?!\s?ti)\b", gpu=True),
    Reference("RTX 4080", "GPU", 950, r"rtx\s?4080", gpu=True),
    Reference("RTX 4070", "GPU", 550, r"rtx\s?4070", gpu=True),
    Reference("RX 9070 XT", "GPU", 680, r"\brx\s?9070\s?xt\b", gpu=True),
    # --- Consoles & VR -------------------------------------------------------------------
    Reference("PS5 Pro", "Console", 750, r"\b(?:ps5|playstation\s?5)\s?pro\b", requires=_CONSOLE),
    Reference("PS5", "Console", 450, r"\b(?:ps5|playstation\s?5)(?:\s?(?:slim|standard|digital|digitale|édition standard))?\b(?!\s?pro)", requires=_CONSOLE),
    Reference("Nintendo Switch 2", "Console", 420, r"\bswitch\s?2\b", requires=_CONSOLE),
    Reference("Nintendo Switch OLED", "Console", 290, r"\bswitch\b.*\boled\b|\boled\b.*\bswitch\b", requires=_CONSOLE),
    Reference("Meta Quest 3", "VR", 480, r"\bquest\s?3\b(?!s)"),
    # --- Apple -----------------------------------------------------------------------------
    Reference("iPhone 16 Pro Max", "Smartphone", 1300, r"\biphone\s?16\s?pro\s?max\b"),
    Reference("iPhone 16 Pro", "Smartphone", 1050, r"\biphone\s?16\s?pro\b(?!\s?max)"),
    Reference("iPhone 16", "Smartphone", 820, r"\biphone\s?16\b(?!\s?(?:pro|plus|e)\b)"),
    Reference("iPhone 15", "Smartphone", 680, r"\biphone\s?15\b(?!\s?(?:pro|plus)\b)"),
    Reference("MacBook Pro", "Ordinateur portable", 1600, r"\bmacbook\s?pro\b"),
    Reference("MacBook Air M3/M4", "Ordinateur portable", 1050, r"\bmacbook\s?air\b.*\bm[34]\b"),
    Reference("AirPods Pro 2", "Audio", 210, r"\bairpods\s?pro\s?2\b|\bairpods\s?pro\b.*\b2(?:e|nd|\.)?\s?(?:g[ée]n|gen)"),
    Reference("iPad Pro", "Tablette", 1000, r"\bipad\s?pro\b"),
    Reference("Apple Watch Ultra", "Montre connectée", 780, r"\bapple\s?watch\s?ultra\b|\bwatch\s?ultra\s?\d"),
    # --- Maison & audio --------------------------------------------------------------------
    Reference("Dyson V15", "Aspirateur", 550, r"\bdyson\b.*\bv15\b|\bv15\b.*\bdyson\b"),
    Reference("Dyson Airwrap", "Coiffure", 450, r"\bairwrap\b"),
    Reference("Sony WH-1000XM5", "Audio", 290, r"\bwh-?1000\s?xm5\b"),
    Reference("Roborock S8", "Aspirateur", 550, r"\broborock\b.*\bs8\b"),
    Reference("TV OLED 77\"", "TV", 2000, r"\boled\b.*\b77\s?(?:\"|''|pouces|zoll|inch|in\b|”)|\b77\s?(?:\"|''|pouces|zoll|inch|”).*\boled\b"),
    Reference("TV OLED 65\"", "TV", 1300, r"\boled\b.*\b65\s?(?:\"|''|pouces|zoll|inch|in\b|”)|\b65\s?(?:\"|''|pouces|zoll|inch|”).*\boled\b"),
    Reference("TV OLED 55\"", "TV", 950, r"\boled\b.*\b55\s?(?:\"|''|pouces|zoll|inch|in\b|”)|\b55\s?(?:\"|''|pouces|zoll|inch|”).*\boled\b"),
]
_COMPILED = [(ref, ref.regex) for ref in CATALOG]


@dataclass(frozen=True)
class CatalogMatch:
    name: str
    family: str
    price: float          # prix plancher dans la devise de l'annonce


def lookup(title: str, currency: str | None = "EUR") -> CatalogMatch | None:
    """Prix de référence du produit du titre, ou ``None`` si inconnu / accessoire / non comparable."""
    if not title or currency not in CURRENCY_FACTORS:
        return None
    if ACCESSORY_RE.search(title):
        return None
    for ref, regex in _COMPILED:
        if not regex.search(title):
            continue
        if ref.requires and not re.search(ref.requires, title, re.IGNORECASE):
            return None
        if ref.gpu and COMPLETE_PC_RE.search(title):
            return None                         # PC complet : le prix du GPU ne s'applique pas
        price = ref.price * CURRENCY_FACTORS[currency]
        if REFURBISHED_RE.search(title):
            price *= REFURBISHED_FACTOR
        return CatalogMatch(ref.name, ref.family, round(price, 2))
    return None
