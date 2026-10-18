# 📡 Radar erreurs de prix

Répertorie automatiquement les **erreurs de prix** signalées sur Amazon, Cdiscount, Fnac, Darty et les autres grandes marketplaces.

**Site : https://ede800.github.io/radar-erreurs-prix/**

## Fonctionnement

Toutes les 30 minutes, une GitHub Action :

1. lit les flux RSS des groupes « erreur de prix » des communautés de bons plans :
   [Dealabs](https://www.dealabs.com/groupe/erreur-de-prix) 🇫🇷,
   [mydealz](https://www.mydealz.de/gruppe/preisfehler) 🇩🇪,
   [Preisjäger](https://www.preisjaeger.at/gruppe/preisfehler) 🇦🇹,
   [HotUKDeals](https://www.hotukdeals.com/tag/price-glitch) 🇬🇧 ;
2. extrait le marchand, le prix, le prix d'origine quand il est mentionné (« au lieu de », « statt », « was »…) et la remise ;
3. classe chaque annonce par marketplace (Amazon, Cdiscount, Fnac, Darty, eBay, AliExpress…) ;
4. fusionne le tout dans [`docs/data/deals.json`](docs/data/deals.json), en gardant 60 jours d'historique ;
5. committe le fichier, ce qui met à jour le site GitHub Pages.

Le site (`docs/index.html`, sans dépendance) permet de filtrer par marketplace et par pays, de chercher et de trier par date, remise ou prix.

> Les marketplaces ne sont pas scrapées directement : c'est contraire à leurs conditions d'utilisation et les IP de GitHub Actions sont vite bloquées. Les signalements viennent des communautés, qui vérifient elles-mêmes les erreurs.

## En local

Python 3.10+ uniquement, aucune dépendance :

```sh
python scraper/fetch.py             # met à jour docs/data/deals.json
python -m unittest discover -s tests
python -m http.server -d docs 8000  # puis http://localhost:8000
```

## Ajouter une source

Ajouter une entrée dans `SOURCES` de [`scraper/fetch.py`](scraper/fetch.py). Les flux du réseau Pepper (Dealabs, mydealz, Preisjäger, HotUKDeals, Chollometro…) fonctionnent tels quels. Pour reconnaître une nouvelle marketplace, compléter `MARKETPLACES`.

## Avertissement

Une erreur de prix est souvent corrigée en quelques heures, et le marchand peut annuler la commande. Vérifiez toujours l'annonce d'origine avant d'acheter.
