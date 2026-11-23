# Guide du tableau de bord CarbonSync (assistant)

Base de connaissances de l'assistant. Chaque section `##` est un sujet ; la ligne `keywords` aide la recherche.

## CarbonSync en bref
<!-- keywords: application, présentation, navigation, menu, onglets, langue, thème, sombre, clair, démarrer, lancer, connexion -->
CarbonSync AI suit les émissions de CO₂ d'un site industriel (Scope 1, Scope 2 méthodes localisation et marché, criblage du Scope 3), prévoit les 6 prochains mois, répartit les émissions par zone de l'usine et compare la trajectoire à un objectif de réduction (−30 % en 2030 par rapport à 2019 par défaut) et à son budget carbone annuel. Les données arrivent sous forme de CSV par service (énergie, production, logistique, déchets & eau).

La barre du haut regroupe les pages : **Pilotage** (Vue d'ensemble, Prévisions, Répartition), **Alertes**, **Assistant**, **Modèles** (Laboratoire modèles), **Planification** (Budget carbone, Scénarios), **Données** (Dépôt de données, Qualité des données), **Preuves** (Inventaire GES, Méthodologie, Rapports), **Réglages** (Configuration). La barre reste fixe quand on fait défiler la page. Les pages affichées dépendent du rôle (lecteur, éditeur, administrateur).

En haut à droite de chaque page : le bouton **FR / EN** et le bouton **compte** : votre équipe avec qui est **en ligne ou hors ligne**, les **derniers fichiers reçus** (service, lignes, période, qui les a déposés) et la dernière exécution du pipeline, puis **Se déconnecter**. Le mot de passe se change dans Réglages → Configuration → **Mot de passe**. En bas à droite, le **bouton d'alertes** rond est au-dessus du bouton IA : **rouge** quand une alerte critique est ouverte, orange pour les avertissements, avec le nombre. L'apparence (palette, effet verre, mode clair / sombre, taille du texte) se règle dans Réglages → Configuration → Apparence.

Démarrage : double-cliquer sur `start.bat` ; le navigateur s'ouvre seul et la fenêtre affiche l'activité (pipeline, prévisions, assistant). Arrêt : `stop.bat` arrête le tableau de bord, ses tâches en arrière-plan et les modèles locaux, et ferme Ollama (`stop.bat keep-ollama` laisse Ollama ouvert). À la main : `streamlit run streamlit_app.py`.

## Vue d'ensemble
<!-- keywords: vue d'ensemble, accueil, indicateurs, 12 derniers mois, trajectoire, tout, historique complet, défiler, répartition par scope, mensuel, budget du mois, méthode marché, plafond, alertes ouvertes -->
Page d'accueil. Première ligne : émissions des **12 derniers mois** (et évolution sur un an), **budget du mois** (statut par rapport au plafond), prévision des **30 prochains jours**, **part du Scope 2**. Deuxième ligne : **Scope 2 localisation** et **marché** (12 mois, part des kWh sous contrat), **plafond de l'année** lu sur la trajectoire de l'objectif, et **alertes ouvertes**.

**Trajectoire des émissions** : historique + prévision avec intervalle à 90 %. Boutons 90 j / 180 j / 365 j / **Tout**. « Tout » affiche les 3 000+ jours : le graphique s'ouvre sur la dernière année, on le fait glisser (ou la barre du bas) pour remonter à n'importe quelle date ; raccourcis 6 m / 1 an / 3 ans / Tout dans le graphique. Les pics anormaux hors échelle sont marqués ▲ (survol = valeur réelle). Les jalons solaire sur site et programme d'efficacité sont indiqués.

**Répartition par scope** (12 derniers mois) et **Émissions mensuelles** (24 derniers mois, empilées par scope).

## Prévisions
<!-- keywords: forecast, prévision, horizon, 183 jours, intervalle, pic, modèle, comparer, nouvelle prévision, lancer, backtest, mase -->
Prévision active sur **183 jours** avec intervalle de confiance à 90 %. Indicateurs : total sur l'horizon, moyenne journalière, pic prévu, largeur moyenne de l'intervalle.

Graphique **Prévision et historique récent** (pas jour ou semaine), **10 jours les plus émetteurs**, **Dernière exécution de chaque modèle** (choisir les modèles à comparer, afficher les intervalles), **Précision en backtest** (MASE < 1 = meilleur que la référence naïve).

**Calculer une nouvelle prévision** (bouton en bas) : modèle Auto (routeur) / Ens-PC / Chronos-2 / Prophet, historique d'entraînement 180 / 365 / 730 / 1 095 jours. Chronos-2 prend 1 à 2 min sur CPU. La nouvelle prévision devient la prévision active.

## Laboratoire modèles
<!-- keywords: model lab, laboratoire, expérience, entraîner, tester, horizon, 3 mois, 6 mois, 12 mois, période d'entraînement, backtest, futur, prophet, chronos, ens-pc, comparer -->
Pour tester chaque modèle individuellement. Réglages de l'**Expérience** : modèles (Prophet, Chronos-2, Ens-PC), **horizon** 3 / 6 / 12 mois, **période d'entraînement** 6 / 12 / 24 mois ou tout l'historique, **mode** « Tester sur le passé » (backtest : la prévision est comparée aux données réelles à partir d'une date de coupure) ou « Prévoir le futur ». Bouton **Lancer l'expérience**.

Résultats : prévisions vs réel, erreur absolue moyenne par semaine de prévision, **tableau des scores** (MAE, MASE, couverture 90 %, biais) avec la référence naïve saisonnière, liste des **expériences de la session** (bouton Effacer), **journal de la dernière exécution**, et l'onglet « comment ça marche » (composantes Prophet : tendance, saisonnalités).

Les expériences ne modifient pas la prévision active.

## Budget carbone
<!-- keywords: budget, plafond, mensuel, annuel, dépassement, statut, vert, orange, rouge, consommé, projection, fin de mois, atterrissage, objectif, trajectoire, année de référence, 2030, -30 %, réduction, poids saisonniers, dans la trajectoire -->
Réel + prévision comparés au **plafond mensuel**. Choisir l'**année** et le **mois**. Indicateurs : plafond du mois, consommé, projection fin de mois, borne haute 90 %. Graphique **Consommation cumulée du mois** et vue **Année** (atterrissage estimé vs plafond annuel).

**Objectif et trajectoire** : une année de référence (2019 par défaut, 3 397 tCO₂e), une année cible (2030) et une réduction (−30 %, soit 2 378 tCO₂e). La trajectoire annuelle est une droite de la référence à la cible ; **le plafond de chaque année est lu sur cette trajectoire** (2026 : 2 749 tCO₂e). Le graphique montre la trajectoire, les années réelles et la projection de l'année ; le tableau indique pour chaque année si elle était dans la trajectoire. Un **plafond fixe** peut être choisi à la place (Configuration → Objectif et budget).

**Poids mensuels** : par défaut le **profil saisonnier** du site (part de chaque mois sur les 3 dernières années complètes : août est plus bas à cause de la fermeture d'été) ; ils peuvent aussi être saisis à la main (total 1,00). Plafond du mois = plafond de l'année × poids du mois.

Statut d'un mois : **vert** si la borne haute 90 % reste sous le plafond, **orange** si seule la projection centrale y reste, **rouge** sinon. Au-delà de l'horizon de prévision, les mois ne sont pas estimés sur cette page. Le périmètre du budget est Scope 1 + Scope 2 localisation + déchets et eau.

## Alertes
<!-- keywords: alertes, alerte, cloche, badge, notification, avertissement, critique, orange, rouge, anomalie, hors de la bande, bande 90 %, données anciennes, aucune donnée, service, dérive du routeur, quarantaine, vérifier maintenant, e-mail -->
Le **bouton d'alertes** rond en bas à droite (au-dessus du bouton IA) indique le nombre d'alertes ouvertes ; il devient **rouge** quand l'une est critique. Un clic les liste (les critiques sur fond rouge foncé) avec **Voir les alertes critiques** et **Ouvrir la page Alertes**. Sur la page Alertes, les alertes critiques ont un fond **rouge foncé**, les avertissements un fond orangé, et un filtre affiche Toutes / Critiques / À surveiller / Informations. Le pipeline les vérifie après chaque exécution ; les éditeurs peuvent aussi cliquer sur **Vérifier maintenant**.

Contrôles : **budget** (un mois dont la projection dépasse son plafond : orange à moins de 10 %, rouge au-delà), **fin d'année** (projection annuelle au-dessus du plafond de l'année sur la trajectoire), **anomalies** (jours réels hors de la bande 90 % de la prévision sur les 90 derniers jours), **données** (un service qui n'a rien envoyé depuis 45 jours), **routeur** (modèle du routeur nettement moins bon que le meilleur dans le backtest), **quarantaine** (fichiers à corriger). Chaque carte dit ce qui s'est passé et renvoie vers la page à ouvrir. Les alertes résolues passent dans l'historique. Le graphique montre les jours réels face à la bande 90 %.

Seuils : Configuration → Rapports et alertes. L'envoi par e-mail est optionnel et non configuré.

## Répartition par zone
<!-- keywords: répartition, zone, usine, atelier, p1, p2, p3, coupe, préparation, assemblage, utilités, logistique, poste, sous-catégorie, compresseurs, fours, chariots, clés de répartition, sous-compteurs, responsable -->
Émissions par **zone de l'usine** : **P1 Coupe**, **P2 Préparation**, **P3 Assemblage**, **UT Utilités et bâtiments**, **LOG Logistique**, et par **poste** (machines de coupe, compresseurs, fours, lignes d'assemblage, climatisation, chaudière, chariots élévateurs, camions de livraison…). Choisir la période ; indicateurs, barres par zone et par poste, vue mensuelle par zone (24 mois) et tableau détaillé avec le responsable de chaque zone.

Méthode : chaque compteur du site (électricité, fioul, diesel, déchets, eau) est réparti avec les **clés de répartition** de config.yaml (breakdown) ; les zones totalisent toujours le total du site. Les clés sont des estimations tant qu'il n'y a pas de sous-compteurs ; elles se modifient dans Configuration → Inventaire et répartition.

## Inventaire GES
<!-- keywords: inventaire, inventaire ges, esrs e1-6, scope 2 méthode marché, localisation, mix résiduel, ppa, offre verte, certificats, contrat, scope 3, criblage, catégorie, achats, cuivre, pvc, trajets domicile-travail, transport, calculé, estimé, exclu -->
L'inventaire GES complet sur une période : **Scope 1**, **Scope 2 localisation** et **marché**, **Scope 3**, et les totaux pour les deux méthodes du Scope 2 (tableau ESRS E1-6). Scope 2 par année et tableau des instruments contractuels.

**Scope 2 méthode marché** : les kWh couverts par un contrat (PPA, offre verte, certificats) prennent le facteur du contrat ; le reste prend le mix résiduel. Aucun contrat n'est encore enregistré et la Tunisie n'a pas de mix résiduel publié : la moyenne du réseau sert d'approximation et les deux méthodes donnent le même chiffre.

**Criblage du Scope 3** : chacune des 15 catégories est marquée **calculée** (données du site, ex. déchets, eau), **estimée** (hypothèses sourcées : cuivre et PVC achetés par unité produite, amont des combustibles et de l'électricité, transport amont et aval, trajets domicile-travail) ou **exclue** (avec le motif). Des ordres de grandeur pour fixer les priorités, à remplacer par les achats réels et une enquête mobilité. Hypothèses : Configuration → Inventaire et répartition.

## Connexion et rôles
<!-- keywords: connexion, se connecter, se déconnecter, mot de passe, compte, utilisateur, rôle, lecteur, éditeur, administrateur, admin, premier démarrage, bloqué, débloquer, mot de passe oublié, session, cookie, droits -->
Au premier démarrage, le tableau de bord demande de créer le compte **administrateur**. Ensuite chacun se connecte avec un identifiant et un mot de passe.

Rôles : **lecteur** (consulte toutes les pages de suivi, le budget, les scénarios, les rapports, l'assistant ; ne change que son propre mot de passe dans Configuration → Mot de passe), **éditeur** (dépose aussi des données, lance les prévisions et le laboratoire, génère les rapports, vérifie les alertes, modifie objectif, inventaire, répartition, facteurs et réglages des rapports), **administrateur** (aussi apparence, mise en page, système, utilisateurs, pages IA & API et retour aux données d'origine). Les boutons non autorisés sont grisés avec une info-bulle.

Sécurité : les mots de passe ne sont stockés que sous forme d'empreintes PBKDF2-SHA256 (600 000 itérations) dans data/auth/users.json (jamais versionné) ; une session dure 12 heures ; 5 mots de passe faux bloquent le compte 15 minutes. Mot de passe oublié : un administrateur le réinitialise (Configuration → Utilisateurs ou `python -m carbonsync.auth passwd <identifiant>`). La connexion peut être désactivée avec `auth: enabled: false` dans config.yaml (tout le monde est alors administrateur).

## Scénarios
<!-- keywords: scénario, what-if, levier, solaire, autoconsommation, efficacité, production, simulation, émissions évitées, enregistrer -->
Teste l'effet de leviers sur les 6 prochains mois (prévision active comme référence). Curseurs : **autoconsommation solaire** 0–40 %, **efficacité énergétique** 0–15 %, **volume de production** −20 à +20 %.

Résultats : référence 183 j, scénario, **émissions évitées**, par jour, contribution de chaque levier, courbe référence vs scénario, **scénarios enregistrés**.

Règles du simulateur : le solaire et l'efficacité réduisent l'électricité (Scope 2) ; l'efficacité réduit aussi les combustibles de moitié ; la production a une élasticité de 0,75 sur les émissions.

## Dépôt de données
<!-- keywords: upload, déposer, ajouter, csv, nouveau mois, trimestre, zip, dossier, service, département, pipeline, relancer, réinitialiser, données d'origine, modèle csv, samples -->
Pour ajouter des données. Une tuile par service montre le dernier jour reçu et son état. On peut déposer :

- un CSV **dans la zone d'un service** (énergie, production, logistique, déchets & eau) ;
- **tout en une fois** : plusieurs CSV, une archive **.zip** ou un **dossier** ; le service est reconnu par le nom du fichier ou ses colonnes.

Après dépôt, le **pipeline complet** se relance : validation → chargement → table journalière → prévision (routeur : Chronos-2) → rafraîchissement du backtest → vérification des alertes → rapports Excel et PDF. Toutes les pages se mettent à jour. Un mois n'est intégré que lorsque **les 4 services** l'ont envoyé ; sinon il reste « en attente ».

Autres actions : **Modèle CSV** (télécharger un gabarit par service), **Relancer sans nouveau fichier**, interrupteur « Générer le rapport Excel », **Historique du pipeline**, **Couverture par service** (18 derniers mois), **Revenir aux données d'origine** (supprime les fichiers déposés et restaure les originaux, avec confirmation).

Fichiers d'exemple prêts à déposer : dossier `samples/` du projet (`2026-Q1.zip` déjà déposé, `2026-Q2.zip` = avril–juin 2026, `2026-Q3.zip` = juillet–septembre 2026).

## Qualité des données
<!-- keywords: qualité, quarantaine, rejet, rejeté, accepté, corrigé, imputé, imputation, confiance, schéma, colonnes, erreur, fichier -->
Ce qui est entré, ce qui a été corrigé, ce qui a été rejeté. Indicateurs : **fichiers acceptés**, **en quarantaine**, **jours imputés**, **indice de confiance** (/100).

**Motifs de rejet**, **journal de quarantaine** (filtrable par service) avec, pour chaque fichier, le motif et la **correction suggérée**, et **schémas attendus** (colonnes obligatoires par service).

Réparations automatiques : séparateurs, formats français (virgule décimale, dates jj/mm/aaaa), unités, alias de colonnes. Un fichier irréparable part en quarantaine : le corriger puis le redéposer dans Dépôt de données.

## Méthodologie
<!-- keywords: méthodologie, backtest, précision, mae, mase, couverture, fenêtre, meilleur modèle, évaluation -->
Résultats du **backtest glissant** : 6, 12 et 24 mois d'historique, horizon 183 jours. Indicateurs : meilleur modèle, erreur moyenne (MAE), meilleure couverture 90 %, nombre d'évaluations. Graphiques : MAE par fenêtre d'historique, couverture empirique de l'intervalle 90 %, erreur relative à la référence naïve.

## Rapports
<!-- keywords: rapport, excel, pdf, synthèse, télécharger, générer, audit, csrd, esrs, conclusions, recommandations, export, csv -->
Conclusions et recommandations générées automatiquement, puis deux documents :

- **Classeur d'audit complet** (Excel, 10 onglets : synthèse, annuel, mensuel, activité, prévision, budget, modèles, qualité, méthodologie, journal) ;
- **Rapport de synthèse** (PDF, 2 pages : chiffres clés, graphiques, conclusions, budget, prévision, modèles, recommandations).

Bouton **Générer** puis **Télécharger** ; les rapports précédents restent disponibles (« Rapports générés »). Jeux de données téléchargeables : émissions journalières, synthèse mensuelle, synthèse annuelle, prévision active (CSV), backtest (JSON). Tableau de **préparation ESRS E1-6**.

## Configuration
<!-- keywords: réglages, configuration, paramètres, apparence, thème, palette, couleur, verre, plat, fond animé, fond d'écran, sombre, clair, taille du texte, densité, angles, animations, mise en page, menu, masquer une page, page d'accueil, bouton assistant, pied de page, largeur, plafond, budget annuel, poids mensuels, facteurs, rapports, nom du site, langue des rapports, langue par défaut, mode de données, synthétique, production, exporter, importer, valeurs par défaut, objectif, année de référence, mix résiduel, instruments, ppa, hypothèses scope 3, clés de répartition, zones, utilisateurs, comptes, rôles, routeur, seuils d'alerte -->
Jusqu'à huit onglets selon le rôle (éditeurs : Objectif et budget, Inventaire et répartition, Facteurs, Rapports et alertes ; administrateurs : tous) :
- **Apparence** (admin) : palette (Émeraude, Océan, Indigo, Ambre, Corail, Graphite, ou **Personnalisée** avec une couleur d'accent au choix), effet **Verre** ou **Plat**, mode Auto / Clair / Sombre, taille du texte S / M / L, densité, angles, animations, **fond animé**. Les contrastes sont vérifiés et chaque palette est ajustée pour rester lisible. **Appliquer** change toutes les pages sans redémarrage. Les couleurs des graphiques restent fixes (Scope 1 orange, Scope 2 bleu…).
- **Mise en page** (admin) : pages affichées dans le menu (Vue d'ensemble, Alertes, Assistant et Configuration restent toujours affichées), page d'accueil, largeur des pages, bouton assistant flottant, pied de page.
- **Objectif et budget** : année de référence, année cible, réduction en %, plafond de chaque année (lu sur la trajectoire ou fixe), poids mensuels (profil saisonnier ou saisis). Le plafond de l'année qui en résulte est affiché avant d'enregistrer.
- **Inventaire et répartition** : Scope 2 méthode marché (mix résiduel et sa source, instruments contractuels : PPA, offre verte, certificats, avec dates et part des kWh ou MWh/an) ; hypothèses du Scope 3 (matières par unité et facteurs, trajets, énergie amont, transport, motifs des catégories exclues) ; zones et clés de répartition (les parts d'un compteur doivent totaliser 1).
- **Facteurs** : facteurs d'émission avec unité, scope et source, date de revue et ancienneté. Lecture seule (modifiés dans config.yaml, puis on relance le pipeline).
- **Rapports et alertes** : nom du site, langue des rapports Excel et PDF ; seuils des alertes (aucune donnée depuis N jours, fenêtre d'anomalies, écart du routeur). L'envoi par e-mail n'est pas configuré.
- **Utilisateurs** (admin) : créer des comptes (lecteur, éditeur, administrateur), changer un rôle, réinitialiser un mot de passe, débloquer, supprimer.
- **Système** (admin) : langue par défaut, mode de données, chiffres du moteur, **routeur et backtest** (modèle Chronos-2, repli Prophet, historique minimal 60 jours, backtest après chaque dépôt), **Exporter / importer** les réglages, **Rétablir les valeurs par défaut**, historique des modifications.

Chaque enregistrement crée une copie d'audit horodatée de la configuration.

## IA & API
<!-- keywords: clé api, clé, ia & api, fournisseur, ollama, ollama cloud, openrouter, gratuit, modèles gratuits, nemotron, gemma, ling, limite gratuite, quota, repli, collecte des données, gpu, libérer le gpu, mémoire vidéo, arrêter les modèles, démarrer, décharger, deepseek, kimi, glm, mistral, claude, anthropic, expert, mode expert, cloud, gestionnaire d'identification, keyring, coût, dépense, plafond de dépense, jetons, modèle, top 5, essai rapide, solde, crédit, opus, sonnet, haiku, effort, test de connexion, supprimer la clé, données envoyées, confidentialité, fiche expert, modèle local, contexte -->
Groupe de pages **IA & API** : les moteurs de l'assistant. Chaque fournisseur cloud a sa page, avec sa clé API. Les clés sont rangées dans le **Gestionnaire d'identification Windows**, jamais dans config.yaml, les journaux, les exports ou les conversations, et ne sont affichées que masquées (sk-ant-…a1b2, sk-or-…a1b2, ou …a1b2 pour une clé sans préfixe). On les colle dans un champ masqué puis **Enregistrer la clé** ; **Tester la connexion** vérifie la clé sans consommer de jetons ; **Supprimer la clé** la retire.

**Vue d'ensemble IA** : une tuile par fournisseur (Ollama local, Ollama Cloud, Claude, OpenRouter ; OpenAI, Gemini, Mistral et « compatible OpenAI » bientôt disponibles) avec son état, son modèle et sa dépense du jour ; le **plafond de dépense par jour**, commun à tous les fournisseurs cloud (0 = sans plafond) et le tableau des 30 derniers jours ; les **données envoyées au cloud** ; la **fiche de l'expert** (docs/assistant/expert_brief.md), lue par chaque modèle cloud au début de chaque conversation. Une fois le plafond atteint, les modèles cloud se mettent en pause jusqu'au lendemain.

**Ollama** : la clé **Ollama Cloud** (créée sur ollama.com/settings/keys ; une variable OLLAMA_API_KEY sert si aucune clé n'est enregistrée). Le test lit le solde du compte. Les **5 meilleurs modèles cloud** pour l'assistant, avec leur tarif : DeepSeek V4 Pro (recommandé), Kimi K3.1 Pro, GLM 5.3, Mistral Large 4, DeepSeek V4.1 Flash (rapide, économique) ; **Choisir** en fait le modèle de l'assistant. **Autre modèle** : tout autre modèle cloud d'Ollama. **Essai rapide** : une question d'un mot au modèle choisi, pour vérifier qu'il répond (coût : une fraction de centime). Ollama Cloud se paie à l'usage, par jeton (petite quantité gratuite chaque mois sur certains modèles, puis crédits ou abonnement). Plus bas, **Ollama local** : modèles installés sur ce PC, part GPU, modèle local par défaut et taille du contexte (8k, 16k recommandé, 32k). **Arrêter les modèles locaux** retire tous les modèles de la mémoire pour **libérer le GPU** (jeu, rendu, autre logiciel) ; le serveur Ollama reste ouvert sans occuper le GPU, et l'assistant ne recharge plus le modèle tout seul. **Démarrer** recharge le modèle par défaut tout de suite (environ 10 s), et ouvre Ollama s'il était fermé ; la première question avec un modèle local le recharge aussi. Un modèle local se retire seul de la mémoire 20 min après la dernière question.

**OpenRouter** : des **modèles gratuits** de plusieurs éditeurs avec une seule clé (créée sur openrouter.ai/settings/keys, commence par sk-or-v1- ; ou la variable OPENROUTER_API_KEY). Le test lit la clé et le **quota gratuit du jour** sans requête de modèle. Les **5 meilleurs modèles gratuits** pour l'assistant, tous avec outils et réflexion : NVIDIA Nemotron 3 Ultra (recommandé), Google Gemma 4 31B, NVIDIA Nemotron 3.5 Super VL, inclusionAI Ling 3.2 Flash, Google Gemma 4 26B A4B (rapide) ; **Choisir** en fait le modèle de l'assistant. **Autre modèle gratuit** : tout autre modèle gratuit avec outils ; **Actualiser la liste** relit la liste publique d'OpenRouter. **Limites gratuites** : 20 requêtes par minute, 50 par jour (1 000 par jour une fois 10 crédits achetés) ; une question prend 2 à 4 requêtes. **Repli automatique** : si le modèle choisi est saturé, OpenRouter passe à un autre modèle gratuit (sans coût ; le panneau Sources indique le modèle qui a répondu). **Refuser la collecte** : n'utilise que des fournisseurs qui ne conservent pas les requêtes (certains modèles gratuits deviennent indisponibles). OpenRouter ne conserve pas les requêtes par défaut, mais le fournisseur d'un modèle gratuit peut les journaliser ; seules des données agrégées partent.

**Claude** : la clé Anthropic (console Claude, platform.claude.com → API Keys ; ou la variable ANTHROPIC_API_KEY), le modèle (Opus 5.5, Sonnet 5.5, Haiku 5.5) et l'**effort de réflexion** (faible, moyen recommandé, élevé). Repli automatique sur Opus et Sonnet : une demande déclinée pour raison de politique est relancée sur un autre modèle Claude.

**Données envoyées au cloud** : la question et la conversation Expert, la fiche de l'expert, le contexte (dates, plafond, facteurs, prévision active) et les résultats d'outils agrégés au mois ou plus (périodes élargies au mois entier). Jamais : les lignes journalières, les fichiers CSV, les résultats lus avec un modèle local, votre identité, vos clés.

## Le pipeline de données
<!-- keywords: pipeline, étapes, boucle, validation, chargement, duckdb, table journalière, traitement, journal, terminal, rafraîchissement du backtest, vérification des alertes -->
Étapes : 1) **validation** des CSV (schémas, réparations, quarantaine) ; 2) **chargement** dans DuckDB (tables brutes par service) ; 3) **table journalière** : une ligne par jour, jours manquants imputés et signalés, un mois n'entre que quand tous les services l'ont ; 4) **prévision** selon le routeur (Chronos-2) ; 5) **rafraîchissement du backtest** quand de nouvelles données sont arrivées (fenêtres glissantes, sauté s'il est à jour) ; 6) **vérification des alertes** ; 7) **rapports** Excel + PDF. Chaque exécution est journalisée (historique du pipeline) et visible dans la fenêtre de commande.

En ligne de commande : `python -m carbonsync.pipeline` (options `--no-forecast`, `--no-report`, `--no-backtest`, `--backtest` pour le forcer). Alertes seules : `python -m carbonsync.alerts`.

## Modèles de prévision et routeur
<!-- keywords: modèle, prophet, chronos, ens-pc, ensemble, routeur, règle, 60 jours, modèle par défaut, naïve, fondation, intervalle, mae, mase, couverture, biais, backtest après dépôt -->
- **Chronos-2** : modèle de fondation pour séries temporelles (Amazon), utilisé sans entraînement spécifique (zero-shot). **Modèle par défaut.**
- **Prophet** : tendance + saisonnalités hebdomadaire et annuelle + régresseurs ; le modèle de repli.
- **Ens-PC** : moyenne de Prophet et Chronos-2 ; disponible au choix (Prévisions → Calculer une nouvelle prévision).
- **Référence naïve saisonnière** : répète la dernière semaine ; sert d'étalon.

**Routeur** (règle de production) : **Chronos-2 dès 60 jours d'historique**, sinon Prophet ; Prophet aussi si Chronos-2 échoue. Pourquoi : dans le backtest, Chronos-2 a l'erreur la plus faible et la couverture 90 % la plus proche de 90 %. Le backtest est recalculé après chaque dépôt, et une alerte se lève si le modèle du routeur devient nettement moins bon que le meilleur. Réglages : Configuration → Système → Routeur et backtest.

Mesures : **MAE** (erreur absolue moyenne, t/jour), **MASE** (MAE divisée par celle de la référence naïve ; < 1 = mieux), **couverture 90 %** (part des jours réels dans l'intervalle ; idéal ≈ 90 %), **biais** (sur- ou sous-prévision moyenne).

## Scopes et facteurs d'émission
<!-- keywords: scope 1, scope 2, scope 3, autres, facteur, émission, kgco2, électricité, fioul, diesel, déchets, eau, ghg protocol, calcul, localisation, méthode marché, mix résiduel -->
Émissions = activité × facteur d'émission.

- **Scope 1** (combustion directe) : fioul lourd 2,68 kgCO₂e/L, diesel logistique 2,68 kgCO₂e/L.
- **Scope 2** (électricité achetée), deux méthodes : **localisation** = kWh achetés × réseau tunisien 0,483 kgCO₂e/kWh ; **marché** = kWh couverts par un contrat × facteur du contrat + autres kWh × mix résiduel. Le solaire sur site est derrière le compteur : il réduit déjà les kWh achetés, dans les deux méthodes.
- **Autres** (dans le budget) : déchets solides 0,5 kgCO₂e/kg, eau de procédé 0,3 kgCO₂e/m³. Dans l'inventaire GES ce sont les catégories 5 et 1 du Scope 3.
- **Scope 3** : estimations de criblage (voir Inventaire GES).

Référentiels : GHG Protocol (dont Scope 2 Guidance), CSRD / ESRS E1. Les facteurs et leur date de revue sont dans Configuration → Facteurs et dans Méthodologie.

## Imputation et quarantaine
<!-- keywords: imputé, imputation, manquant, trou, quarantaine, rejet, en attente, mois incomplet -->
Un **jour imputé** est un jour manquant estimé à partir des jours voisins ; il est signalé (colonne is_imputed) et compté dans Qualité des données. Un fichier **en quarantaine** n'a pas pu être réparé : il est exclu jusqu'à correction. Un mois **en attente** a été reçu d'au moins un service mais pas des quatre ; il n'entre dans les calculs que lorsqu'il est complet.

## Questions fréquentes
<!-- keywords: comment, faq, ajouter trois mois, nouveau trimestre, télécharger rapport, changer plafond, nouvelle prévision, réinitialiser, pourquoi, aide, ajouter un utilisateur, mot de passe oublié, changer l'objectif -->
- **Ajouter 3 mois de données** : Données → Dépôt de données → « Tout déposer d'un coup » → déposer le .zip (ex. `samples/2026-Q2.zip`). Le pipeline se relance seul (prévision, backtest, alertes, rapports).
- **Revenir aux données d'origine** (administrateur) : Dépôt de données → « Revenir aux données d'origine » → Confirmer.
- **Recalculer la prévision** : Prévisions → « Calculer une nouvelle prévision ».
- **Changer l'objectif ou le plafond** : Réglages → Configuration → Objectif et budget → « Enregistrer l'objectif et le budget ».
- **Enregistrer un contrat d'électricité verte** : Configuration → Inventaire et répartition → Scope 2 méthode marché → ajouter une ligne → « Enregistrer le Scope 2 ».
- **Ajouter un utilisateur / réinitialiser un mot de passe** : Configuration → Utilisateurs (administrateur), ou `python -m carbonsync.auth add <nom> --role editor`.
- **Changer les couleurs ou passer en mode clair** : Réglages → Configuration → Apparence → « Appliquer ».
- **Masquer une page du menu** : Réglages → Configuration → Mise en page → « Enregistrer la mise en page ».
- **Télécharger un rapport** : Preuves → Rapports → Générer → Télécharger.
- **Tester un modèle sur une autre période** : Modèles → Laboratoire modèles.
- **Voir tout l'historique** : Vue d'ensemble → Trajectoire → Tout.
- **Pourquoi un mois est « en attente »** : un ou plusieurs services ne l'ont pas encore envoyé.

## L'assistant
<!-- keywords: assistant, chatbot, ia, expert, analyse, local, cloud, clé api, confidentialité, sources, ollama, qwen, moteur, réflexion, historique, chiffre non retrouvé, vérifier, exporter, exporter la conversation, plan d'action, markdown, pdf -->
Page **Assistant** (menu du haut, après Pilotage). L'assistant répond à partir des données du tableau de bord via des outils en lecture seule : il ne modifie jamais les données ni les réglages. Chaque réponse montre les outils utilisés, des graphiques tirés de leurs résultats et un panneau **Sources et détails** (moteur, date des données, prévision utilisée, durée, jetons).

**Moteur** : un modèle local via Ollama sur la carte graphique (Qwen 3.5 9B recommandé, Qwen 3.5 4B plus rapide ; rien ne quitte le PC), ou l'**assistant intégré** (sans modèle : questions guidées, chiffres exacts), qui prend aussi le relais si Ollama ne tourne pas. Le premier message charge le modèle en mémoire (environ 10 s).

**Réflexion** : le modèle réfléchit avant de répondre (plus juste, plus lent). **Analyse** : diagnostic chiffré sur 1, 3, 6 ou 12 mois (points chauds, explication LMDI, projection annuelle vs plafond, réductions nécessaires, leviers) et plan d'action. **Historique** : rouvrir ou supprimer une conversation (enregistrées sur ce PC). **Nouvelle** : nouvelle conversation. **Exporter** : télécharger toute la conversation, ou seulement la dernière réponse (par exemple le plan d'action), en **Markdown** ou en **PDF**, avec les sources de chaque réponse (outils lus, date des données, prévision, moteur, chiffres à vérifier).

**Chiffres non retrouvés** : chaque nombre de la réponse est comparé aux données consultées ; ceux qui n'y figurent pas (calculés ou supposés par le modèle) sont signalés sous la réponse, à vérifier.

**Mode Expert** : un modèle cloud avec votre propre clé API (DeepSeek, Kimi, GLM ou Mistral via Ollama Cloud, les modèles gratuits d'OpenRouter comme Nemotron ou Gemma, ou Claude d'Anthropic), à choisir dans **Moteur** une fois la clé enregistrée dans IA & API → Ollama, OpenRouter ou Claude. Il lit d'abord la fiche de l'expert (rôle d'analyste en décarbonation industrielle, site, données, indicateurs, outils, règles) et ne reçoit que des **données agrégées** : totaux par mois ou plus larges, jamais les lignes journalières ni les fichiers. Passer du mode local au mode Expert (ou l'inverse) ouvre une nouvelle conversation. Le panneau Sources indique le coût estimé de chaque réponse. Si la clé manque, si le plafond de dépense du jour est atteint ou si l'API ne répond pas, l'assistant intégré répond à la place.

## L'assistant flottant
<!-- keywords: bouton rond, bouton flottant, bulle, icône, rond vert, en bas à droite, toutes les pages, sans changer de page, ouvrir l'assistant, fermer, panneau, raccourci, cette page, expliquer cette page, onglet assistant -->
Un **bouton rond vert** (icône IA) en bas à droite de chaque page ouvre l'assistant dans un panneau par-dessus la page : on pose une question sans quitter la page. Le panneau connaît la page ouverte : la première suggestion **explique cette page**, les suivantes portent sur elle (budget, prévision, quarantaine…), et la page accompagne la question envoyée au modèle. C'est **la même conversation et le même moteur** que l'onglet Assistant : une question posée ici s'y retrouve. En haut du panneau : nouvelle conversation et **Ouvrir dans l'onglet Assistant** (historique, réflexion, analyse). **Moteur et export** : changer de moteur, exporter en Markdown ou PDF. Le panneau affiche les 4 dernières questions ; le bouton (croix) le referme. Il n'apparaît pas sur l'onglet Assistant lui-même.
