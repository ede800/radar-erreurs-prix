"""Règles de détection : erreur de virgule, filtre déchets, arbitrage, votes."""

from datetime import datetime, timedelta, timezone

import pytest

from scraper.glitch_engine import (
    analyze,
    classify_seller,
    find_brand,
    is_candidate,
    is_decimal_error,
    is_fat_finger,
    real_discount_ratio,
    resale_margin,
    should_alert,
    vote_velocity,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def make(**fields):
    deal = {
        "id": "dealabs:1", "title": "Produit", "merchant": "Fnac", "price": 100.0,
        "description": "", "published": (NOW - timedelta(hours=1)).isoformat(),
    }
    deal.update(fields)
    return deal


# --- Arbitrage inter-marchés ------------------------------------------------------

def test_real_discount_ratio():
    assert real_discount_ratio(1000, 400) == pytest.approx(0.6)
    assert real_discount_ratio(None, 400) is None
    assert real_discount_ratio(0, 400) is None


def test_resale_margin_deducts_fees():
    assert resale_margin(1000, 400) == 450.0          # 1000 × 0,85 − 400


def test_certified_glitch_on_known_brand():
    a = analyze(make(title="Sony WH-1000XM5", price=120.0, market_median=380.0), NOW)
    assert a.certified
    assert a.score >= 90
    assert a.level == "critical"
    assert "✅ CERTIFIED PRICE GLITCH" in a.reasons
    assert a.real_discount == 68
    assert a.savings == 260.0
    assert "📉 -68% sous le prix marché" in a.reasons


def test_below_threshold_is_not_certified():
    a = analyze(make(title="Sony WH-1000XM5", price=200.0, market_median=380.0), NOW)   # −47 %
    assert not a.certified
    assert a.score < 90


def test_strikethrough_price_alone_is_never_certified():
    # Produit hors catalogue : seul le prix barré est connu.
    a = analyze(make(title="Sony SRS-XB100 enceinte", price=100.0, original_price=1000.0), NOW)
    assert not a.certified and not a.verified
    assert "Prix barré non vérifié" in a.reasons
    assert a.score < 80


def test_inflated_strikethrough_uses_market_price():
    a = analyze(make(title="Samsung Galaxy Buds", price=90.0, original_price=400.0, next_best_price=110.0), NOW)
    assert a.market_price == 110.0
    assert a.real_discount == 18
    assert "Prix barré gonflé" in a.reasons
    assert not a.certified


# --- Erreur de virgule / fat finger ------------------------------------------------

@pytest.mark.parametrize("price,reference,expected", [
    (79.0, 790.0, True),
    (19.99, 199.99, True),
    (8.9, 899.0, True),        # ≈ 1 %
    (300.0, 790.0, False),
    (79.0, None, False),
    (800.0, 790.0, False),
])
def test_decimal_error(price, reference, expected):
    assert is_decimal_error(price, reference) is expected


def test_fat_finger_by_category():
    assert is_fat_finger(89.0, "Carte graphique Asus RTX 5070")         # 890 € plausible pour un GPU
    assert not is_fat_finger(650.0, "Carte graphique Asus RTX 5070")
    assert not is_fat_finger(8.0, "Carte graphique Asus RTX 5070")      # 80 € : hors fourchette
    assert not is_fat_finger(19.0, "Chaise de jardin")                  # famille inconnue


def test_fat_finger_without_market_data():
    a = analyze(make(title="Carte graphique MSI RTX 5080", price=99.0, merchant="LDLC"), NOW)
    assert a.decimal_error
    assert "⚡ Erreur de virgule" in a.reasons


def test_fat_finger_ignored_when_market_price_is_ordinary():
    a = analyze(make(title="Philips Hue Lightstrip für 55 Zoll TV", price=102.0, next_best_price=163.0,
                     merchant="Amazon"), NOW)
    assert not a.decimal_error


# --- Filtre déchets / dropshipping -------------------------------------------------

@pytest.mark.parametrize("title,merchant,reason", [
    ("Coque silicone iPhone-like ultra fine", "Amazon", "Accessoire générique"),
    ("Câble USB-C 2 m tressé", "Fnac", "Accessoire générique"),
    ("Lampe LED 1000 lumens", "AliExpress", "Dropshipping sans marque"),
    ("Montre connectée sport 2026", "Amazon", "Ni marque reconnue ni vendeur réputé"),
    ("Clé Steam Elden Ring", "Instant Gaming", "Clé / code numérique"),
    ("Windows 11 Pro key lifetime", "Amazon", "Clé / code numérique"),
    ("Ghostbusters Remastered", "Xbox Store", "Clé / code numérique"),
    ("Minecraft Minecoins 3500 (Dématérialisé)", "Media Markt", "Clé / code numérique"),
])
def test_trash_is_rejected(title, merchant, reason):
    a = analyze(make(title=title, merchant=merchant, price=5.0, original_price=50.0), NOW)
    assert a.level == "rejected"
    assert a.rejected == reason
    assert a.score == 0


def test_noname_inflated_strikethrough_rejected():
    # « produit noname barré de 120 € à 12 € » chez une enseigne réputée.
    a = analyze(make(title="Robot cuiseur multifonction", merchant="Cdiscount", price=12.0,
                     original_price=120.0, description="Vendu et expédié par Cdiscount"), NOW)
    assert a.rejected == "Prix barré gonflé"


def test_unverified_third_party_rejected():
    a = analyze(make(title="Samsung Galaxy S25 (Vendeur MegaDeals24)", price=99.0, original_price=899.0), NOW)
    assert a.seller == "third_party"
    assert a.rejected == "Vendeur tiers non vérifié"


def test_brand_on_marketplace_is_kept():
    a = analyze(make(title="Lego Star Wars 75313", merchant="Amazon", price=79.0, next_best_price=699.0), NOW)
    assert a.rejected is None
    assert a.certified


def test_case_for_iphone_is_not_an_apple_product():
    a = analyze(make(title="Care by PanzerGlass Kickstand Case passend für Apple iPhone 16", merchant="Talk-Point",
                     price=2.79, next_best_price=12.99), NOW)
    assert a.brand is None
    assert a.rejected == "Accessoire générique"


def test_branded_accessory_and_cheap_items_are_never_certified():
    pads = analyze(make(title="Beats 360 Polster-Set (Ohrmuscheln + Kopfbügel)", merchant="Amazon",
                        price=12.47, next_best_price=63.95), NOW)
    assert pads.brand == "Beats" and not pads.rejected
    assert not pads.certified and pads.score < 80
    cheap = analyze(make(title="Sony SRS-XB100", price=4.0, next_best_price=39.0), NOW)
    assert not cheap.certified


# --- Marques et vendeurs -----------------------------------------------------------

@pytest.mark.parametrize("title,brand", [
    ("Apple MacBook Air M4", "Apple"),
    ("iPhone 17 Pro 256 Go", "Apple"),
    ("Console PS5 Slim", "Sony"),
    ("Aspirateur De'Longhi", "De'Longhi"),
    ("TV OLED LG C5 65\"", "LG"),
    ("Chaussettes rigolotes", None),
    ("Blog de maison", None),
])
def test_find_brand(title, brand):
    assert find_brand(title) == brand


@pytest.mark.parametrize("merchant,title,description,expected", [
    ("Amazon.fr", "Sony TV", "Vendu et expédié par Amazon", "direct"),
    ("Amazon.de", "Sony TV", "Verkauf und Versand durch Amazon.", "direct"),
    ("Amazon", "Sony TV", "", "marketplace"),
    ("Amazon", "Sony TV", "Attention, vendeur tiers", "third_party"),
    ("Fnac", "Dyson V12 (Vendeur Darty)", "", "retailer"),
    ("Fnac", "Dyson V12 (Vendeur Inconnu42)", "", "third_party"),
    ("Boulanger", "Sony TV", "", "retailer"),
    ("AliExpress", "Sony TV", "", "dropship"),
    (None, "Sony TV", "", "unknown"),
])
def test_classify_seller(merchant, title, description, expected):
    assert classify_seller(merchant, title, description) == expected


def test_official_seller_badge():
    a = analyze(make(title="Sony TV Bravia", merchant="Amazon.fr", description="Vendu et expédié par Amazon"), NOW)
    assert "🏪 Vendeur officiel" in a.reasons


# --- Votes, fraîcheur, mémoire de prix ------------------------------------------------

def test_vote_velocity():
    published = (NOW - timedelta(hours=2)).isoformat()
    assert vote_velocity(400, published, NOW) == 200
    assert vote_velocity(None, published, NOW) is None
    assert vote_velocity(100, (NOW - timedelta(minutes=5)).isoformat(), NOW) == 200   # lissé à 30 min


def test_fast_votes_raise_score():
    base = make(title="Sony TV Bravia", price=500.0, next_best_price=700.0)
    slow = analyze(dict(base, temperature=10), NOW)
    fast = analyze(dict(base, temperature=500), NOW)
    assert fast.score > slow.score
    assert "📈 Votes rapides" in fast.reasons


def test_negative_votes_cap_score():
    a = analyze(make(title="Sony WH-1000XM5", price=120.0, market_median=380.0, temperature=-50), NOW)
    assert a.score <= 60
    assert "Votes négatifs" in a.reasons


def test_fresh_badge():
    a = analyze(make(title="Sony TV", published=(NOW - timedelta(seconds=40)).isoformat()), NOW)
    assert "🔥 Posté il y a < 2 min" in a.reasons


def test_flash_drop_from_history():
    base = make(title="Sony TV Bravia", price=300.0, next_best_price=500.0)
    a = analyze(dict(base, history_low=600.0), NOW)
    b = analyze(base, NOW)
    assert "💥 Chute éclair" in a.reasons
    assert a.score > b.score


def test_expired_keeps_true_score_but_is_not_alerted():
    deal = make(title="Sony WH-1000XM5", price=120.0, market_median=380.0, expired=True)
    a = analyze(deal, NOW)
    assert a.score >= 90                       # score intrinsèque intact
    assert a.level == "critical"
    assert "[EXPIRÉ]" in a.reasons
    assert not a.active
    deal.update(a.as_fields())
    assert not should_alert(deal)


def test_age_fields_and_no_age_penalty():
    old = make(title="Zotac Gaming GeForce RTX 5070 Twin Edge OC", merchant="MediaMarkt", price=165.0,
               published=(NOW - timedelta(days=3)).isoformat())
    recent = dict(old, published=(NOW - timedelta(minutes=10)).isoformat())
    a, b = analyze(old, NOW), analyze(recent, NOW)
    assert a.score == b.score
    assert a.age_minutes == 3 * 24 * 60 and not a.is_fresh
    assert b.age_minutes == 10 and b.is_fresh


def test_alerts_skip_stale_deals():
    assert should_alert({"score": 95, "age_minutes": 60})
    assert not should_alert({"score": 95, "age_minutes": 3 * 24 * 60})
    assert not should_alert({"score": 95, "live_corrected": True})


def test_should_alert_threshold_is_exclusive():
    assert should_alert({"score": 81})
    assert not should_alert({"score": 80})
    assert not should_alert({"score": 95, "rejected": "Accessoire générique"})


# --- Flux « nouveautés » --------------------------------------------------------------

def test_fresh_candidates():
    glitch = make(stream="fresh", glitch_keyword=True, title="Chaussettes")
    extreme = make(stream="fresh", glitch_keyword=False, title="Asus ROG Ally", price=99.0, next_best_price=599.0)
    ordinary = make(stream="fresh", glitch_keyword=False, title="Asus ROG Ally", price=550.0, next_best_price=599.0)
    noname = make(stream="fresh", glitch_keyword=False, title="Chaussettes", price=1.0, next_best_price=20.0)
    assert is_candidate(glitch, NOW)
    assert is_candidate(extreme, NOW)
    assert not is_candidate(ordinary, NOW)
    assert not is_candidate(noname, NOW)
