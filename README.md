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
  sources/french_retailers.py  marchands FR/UE prioritaires (Amazon FR/DE/ES/IT, Fnac, Darty, Boulanger, LDLC…)
  net.py                    httpx.AsyncClient HTTP/2, pool de connexions, rotation UA, retry + backoff
  merchant_apis.py          API JSON (Keepa, optionnelle), ASIN, panier direct, liens Amazon .fr/.de/.es/.it/.uk
  price_verifier.py         prix marché réel + résolveur JSON-LD schema.org (sans clé)
  reference_catalog.py      119 produits + règles par caractéristiques (stockage, PC = GPU + 450 €, OLED par diagonale)
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
4. **URL marchande réelle** pour les meilleurs candidats (score ≥ 60, 10 par cycle, une fois par annonce) :
   une requête vers `/visit/threadmain/<id>` de Pepper, en-tête `Location` lu **sans suivre** la redirection,
   puis décodage du paramètre `url=`. Ni le traceur d'affiliation ni le marchand ne sont contactés.
   Résultat : ASIN, lien panier, marchand canonique et `smid` (vendu par Amazon lui-même → vendeur officiel).
5. **Scoring** (`glitch_engine.py`) :
   - `real_discount = (médiane marché − prix) / médiane marché` ; ≥ 55 % sur une marque connue → score ≥ 90, badge **CERTIFIED PRICE GLITCH** ;
   - **erreur de virgule** : prix ≈ 10 % / 1 % du marché, ou prix × 10 dans la fourchette habituelle de la famille (GPU, TV, portable…) ;
   - **rejets stricts** (score 0) : annonces sans prix, forfaits / abonnements / contrats / énergie / VPN / banques (y compris O2, Vodafone, Telekom, SFR, Bouygues), pièces détachées et consommables ≤ 50 € (coussinets, bracelets, cartouches…), accessoires sans marque, clés et jeux dématérialisés, dropshipping, vendeurs tiers non vérifiés, prix barrés gonflés, produits sans marque connue ni vendeur réputé ;
   - pas d'« erreur de virgule » pour les jeux (« Cyberpunk 2077 sur PS5 » n'est pas une PS5 à 350 €) ;
   - bonus : vendeur officiel (« vendu et expédié par »), enseigne réputée, marque connue, votes rapides (°/h), chute éclair sous le plus bas historique ;
   - malus : votes négatifs ;
   - pas de certification pour les accessoires ni pour les articles de moins de 50 € ;
   - l'âge et l'expiration ne modifient **pas** le score : `age_minutes`, `is_fresh` (< 30 min), `active` et le badge `[EXPIRÉ]` sont des champs à part.
   - chaque annonce porte `score_breakdown`, le détail des points, affiché dans l'interface.
6. **Alertes** pour tout score > 80, annonce active de moins de 24 h (`ALERT_MAX_AGE_MINUTES`), une seule fois par prix (SQLite) : score, prix
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

`docs/index.html` : application sans étape de build (polices Inter / JetBrains Mono, icônes SVG intégrées) :

- barre de commande : statut **LIVE** avec compte à rebours du prochain scan (30 s), son, notifications de bureau, recherche `Ctrl+K` ou `/` (produit, marque, marchand, ASIN) ;
- 4 indicateurs : erreurs critiques, meilleure marge de revente, offres fraîches (< 30 min), sources et marchands scannés ;
- onglets *Pépites actives*, *Glitches certifiés 80+*, *Marchands France*, *Tech & GPU*, *Toutes*, *Rejetées (audit)* ; tri par score, marge, remise, date ou prix ; vue **grille** ou **tableau** ;
- favoris ⭐ et **alertes par mots-clés** (stockés dans le navigateur) : surlignage, son, toast et notification quand une nouvelle annonce correspond ;
- panneau de détail : décomposition du score, prix de référence et source, vérification du vendeur (ASIN, `smid`, URL résolue), historique de prix, **calculateur de marge de revente** (commission, frais, ROI) ;
- couleurs par score : 90-100 rose (bordure pulsée si active), 70-89 ambre, 50-69 émeraude.

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
