"""Marchands FR/UE, détail du score, pièces détachées et historique de prix."""

from datetime import datetime, timedelta, timezone

import pytest

from scraper.glitch_engine import analyze
from scraper.history import History
from scraper.sources.french_retailers import identify_merchant

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def make(**fields):
    deal = {"id": "dealabs:1", "title": "x", "merchant": "Fnac", "price": 100.0, "currency": "EUR",
            "description": "", "published": (NOW - timedelta(hours=1)).isoformat()}
    deal.update(fields)
    return deal


@pytest.mark.parametrize("deal,name,country,priority", [
    ({"merchant_host": "www.amazon.fr"}, "Amazon.fr", "FR", True),
    ({"merchant": "Amazon", "link_host": "www.amazon.de"}, "Amazon.de", "DE", True),
    ({"merchant": "Amazon"}, "Amazon.fr", "FR", True),
    ({"merchant": "Materiel.net"}, "Materiel.net", "FR", True),
    ({"merchant": "Rue du Commerce"}, "Rue du Commerce", "FR", True),
    ({"merchant": "E.Leclerc"}, "E.Leclerc", "FR", True),
    ({"merchant_url": "https://www.ldlc.com/fiche/PB001.html"}, "LDLC", "FR", True),
    ({"merchant": "Media Markt"}, "MediaMarkt", "DE", True),
    ({"merchant": "Currys"}, "Currys", "UK", False),
])
def test_identify_merchant(deal, name, country, priority):
    info = identify_merchant(deal)
    assert (info["merchant_name"], info["merchant_country"], info["priority_merchant"]) == (name, country, priority)


def test_unknown_merchant_keeps_pepper_name():
    assert identify_merchant({"merchant": "Petit Shop"}) == {
        "merchant_name": "Petit Shop", "merchant_country": None, "merchant_kind": None, "priority_merchant": False}


def test_media_markt_tarifwelt_is_not_media_markt():
    assert identify_merchant({"merchant": "Media Markt Tarifwelt"})["merchant_name"] == "Media Markt Tarifwelt"


def test_score_breakdown_sums_to_score():
    a = analyze(make(title="Zotac Gaming GeForce RTX 5070 Twin Edge OC", merchant="MediaMarkt", price=165.0,
                     temperature=120, glitch_keyword=True), NOW)
    assert a.score == round(sum(item["points"] for item in a.breakdown))
    labels = [item["label"] for item in a.breakdown]
    assert any(label.startswith("Remise de 73 % vs base catalogue") for label in labels)
    assert "Marque reconnue (Zotac)" in labels


def test_rejected_breakdown_explains_reason():
    a = analyze(make(title="Forfait 5G 200 Go à 9,99 €/mois", merchant="Bouygues Telecom"), NOW)
    assert a.breakdown == [{"label": "Rejetée : Abonnement / forfait / service", "points": 0}]


def test_telecom_operator_in_title_is_rejected():
    a = analyze(make(title="Preisfehler O2 Netz 80GB 5G", merchant="Media Markt", price=639.75), NOW)
    assert a.rejected == "Abonnement / forfait / service"


def test_expensive_spare_part_kept_without_brand_boost():
    a = analyze(make(title="Dyson V15 batterie de remplacement Click-in", merchant="Darty", price=89.0,
                     next_best_price=129.0), NOW)
    assert a.rejected is None
    assert not a.certified
    assert all(not item["label"].startswith("Marque") for item in a.breakdown)
    assert "Pièce / accessoire (> 50 €)" in a.reasons


def test_cheap_spare_part_rejected():
    a = analyze(make(title="Tintenpatronen Canon PG-545", merchant="Amazon", price=9.99), NOW)
    assert a.rejected == "Pièce détachée / consommable"


def test_price_points_history():
    with History() as history:
        for i, price in enumerate((799.0, 749.0, 99.0)):
            history.record_price({"id": f"d:{i}", "title": "Sony WH-1000XM5", "price": price, "currency": "EUR"},
                                 NOW + timedelta(minutes=i))
        points = history.price_points("title:sony wh 1000xm5", "EUR")
        assert [p["price"] for p in points] == [799.0, 749.0, 99.0]
        assert points[-1]["deal"] == "d:2"
