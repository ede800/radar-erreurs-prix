# 📡 Radar erreurs de prix

Détecte en temps réel les **vraies erreurs de prix** (Amazon, Cdiscount, Fnac, Darty, MediaMarkt…),
les vérifie contre le prix du marché, leur attribue un **score de confiance 0-100** et alerte
instantanément sur Discord / Telegram.

**Site : https://ede800.github.io/radar-erreurs-prix/**

## Deux modes d'exécution

| Mode | Commande | Fréquence |
|---|---|---|
| **Daemon temps réel** (recommandé) | `python -m scraper.daemon` | toutes les 20 s (`POLL_INTERVAL`) |
| GitHub Action de secours | `python -m scraper.fetch` | toutes les 30 min (horaire non garanti par GitHub) |

Les deux exécutent le même cycle et écrivent `docs/data/deals.json`. Une erreur de prix dure
souvent moins de 5 minutes : seul le daemon (sur un PC, un Raspberry Pi ou un petit VPS) est
assez rapide pour les alertes.

## Architecture

```
scraper/
  sources/pepper_source.py  flux RSS Pepper : groupe « erreur de prix » + flux de TOUTES les nouveautés
  net.py                    httpx.AsyncClient HTTP/2, pool de connexions, rotation UA, retry + backoff
  merchant_apis.py          API JSON (Keepa, optionnelle), ASIN, panier direct, liens Amazon .fr/.de/.es/.it/.uk
  price_verifier.py         prix marché réel + résolveur JSON-LD schema.org (sans clé)
  reference_catalog.py      prix plancher de référence (GPU, consoles, Apple, Dyson, TV OLED…)
  glitch_engine.py          scoring 0-100 et filtres (module pur, sans réseau)
  history.py                SQLite data/history.db : déjà vus, plus bas historiques, alertes envoyées
  notifier.py               alertes Discord / Telegram
  pipeline.py               un cycle complet
  daemon.py / fetch.py      points d'entrée
```

### Cycle

1. **Ingestion** concurrente des 4 sites (Dealabs 🇫🇷, mydealz 🇩🇪, Preisjäger 🇦🇹, HotUKDeals 🇬🇧) :
   le groupe « erreur de prix » et le flux des nouvelles annonces, publié quelques secondes après
   la soumission. Dans ce flux, seules les annonces avec un mot-clé (« erreur de prix », « bug de prix »,
   « Preisfehler », « price glitch »…) ou une marque connue sont gardées.
2. **Enrichissement** : la page de l'annonce donne la température (votes), le **meilleur prix ailleurs**
   saisi par la communauté et l'état expiré. Il est mis en cache 15 min.
3. **Vérification croisée, sans clé payante** :
   - meilleur prix ailleurs (Pepper) ;
   - JSON-LD schema.org des liens postés dans l'annonce, avec un délai maximal de 3 s : un comparateur (`AggregateOffer`) donne une fourchette marché. La fiche du marchand ne donne que le prix *actuel*, c'est-à-dire l'erreur elle-même : elle sert à vérifier que l'erreur est encore en ligne (`live_price`, `live_available`, `live_corrected`), jamais de référence ;
   - Keepa, optionnel (`KEEPA_API_KEY`) ;
   - à défaut, le **catalogue de référence** (`reference_catalog.py`), quel que soit l'âge de l'annonce : une RTX 5070 à 165 € face à une base de 620 € → −73 % → score 99.

   Le prix barré du marchand n'est **jamais** pris comme preuve. Le catalogue écarte les accessoires (« coque pour AirPods Pro 2 »), les jeux (« Elden Ring PS5 »), les PC complets avec GPU et les forfaits. Il ne suffit pas non plus à valider un vendeur tiers : c'est l'appât d'arnaque classique.
4. **Scoring** (`glitch_engine.py`) :
   - `real_discount = (médiane marché − prix) / médiane marché` ; ≥ 55 % sur une marque connue → score ≥ 90, badge **CERTIFIED PRICE GLITCH** ;
   - **erreur de virgule** : prix ≈ 10 % / 1 % du marché, ou prix × 10 dans la fourchette habituelle de la famille (GPU, TV, portable…) ;
   - **filtre déchets** : accessoires sans marque, coques, câbles, clés et jeux dématérialisés, dropshipping, vendeurs tiers non vérifiés, prix barrés gonflés, produits sans marque connue ni vendeur réputé ;
   - bonus : vendeur officiel (« vendu et expédié par »), enseigne réputée, marque connue, votes rapides (°/h), chute éclair sous le plus bas historique ;
   - malus : votes négatifs ;
   - pas de certification pour les accessoires ni pour les articles de moins de 50 € ;
   - l'âge et l'expiration ne modifient **pas** le score : `age_minutes`, `is_fresh` (< 30 min), `active` et le badge `[EXPIRÉ]` sont des champs à part.
5. **Alertes** pour tout score > 80, annonce active de moins de 24 h (`ALERT_MAX_AGE_MINUTES`), une seule fois par prix (SQLite) : score, prix
   contre prix marché, économie et marge de revente estimée, type de vendeur, lien panier direct
   pour Amazon.

## Installation

```sh
pip install -r requirements.txt
cp .env.example .env            # puis renseigner DISCORD_WEBHOOK_URL et/ou TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
python -m scraper.daemon        # radar temps réel
python -m http.server -d docs 8000   # terminal web : http://localhost:8000
python -m pytest -q
```

Pour la GitHub Action, ajouter les mêmes variables en *secrets* du dépôt (`DISCORD_WEBHOOK_URL`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `KEEPA_API_KEY`). `data/history.db` est conservé
entre deux exécutions grâce au cache d'Actions.

> Si le daemon et l'Action tournent en même temps, tous deux écrivent `deals.json`. En pratique,
> utiliser le daemon pour les alertes et laisser l'Action publier le site.

## Interface

`docs/index.html` (sans dépendance, thème sombre) : préréglages « 🚨 Erreurs critiques probables »,
« 🔥 Grosses réductions » et « Toutes les offres » (les annonces écartées y apparaissent grisées avec
leur motif). Filtres par score minimum, marge de revente, catégorie, marchand et pays. Badges
« ⚡ Erreur de virgule », « 📉 −65 % sous le prix marché », « 🔥 Posté il y a < 2 min »… Le bouton
**LIVE** recharge les données toutes les 30 s et émet un son et une notification de bureau quand
une nouvelle annonce dépasse 80.

## Limites

- Les marketplaces ne sont pas scrapées directement : c'est contraire à leurs conditions
  d'utilisation et les IP de datacenter sont vite bloquées. Les signalements viennent des
  communautés. Amazon, idealo et les grandes enseignes (Fnac, Darty, Cdiscount…) bloquent les robots
  (captcha, 403) : le résolveur JSON-LD ne les interroge pas et ne fonctionne que sur les sites ouverts.
- Pepper ne publie pas l'URL marchande d'une annonce, seulement son domaine. Le lien de redirection
  `/visit/` enregistre un clic affilié : il n'est jamais suivi automatiquement. Seuls les liens
  directs postés dans la description sont utilisés.
- Les prix du catalogue sont des planchers à mettre à jour à la main.
- Sans prix marché ni entrée au catalogue, le score reste prudent : c'est voulu pour éviter les
  faux positifs.
- Les notes des vendeurs tiers ne sont pas disponibles : un vendeur tiers est écarté tant que le prix
  n'est pas vérifié par le marché.

## Avertissement

Une erreur de prix est souvent corrigée en quelques minutes, et le marchand peut annuler la commande.
Vérifiez toujours l'annonce d'origine avant d'acheter.
