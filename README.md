# PLM Intelligence — transformer un extrait PLM en décisions traçables

Projet 7 du hackathon ESILV A5 DPM/PLM (4 au 6 novembre 2026), « PLM Intelligence Challenge », proposé par Dassault Systèmes.

Nous sommes l'équipe engineering et digital d'un fournisseur de moteurs électriques pour l'automobile. Notre client OEM pilote ses développements dans 3DEXPERIENCE et ne nous transmet qu'un extrait XML brut de la structure produit. L'application transforme cet extrait en informations exploitables pour décider sur le coût, le risque, la qualité et la durabilité : explorer la BOM, repérer les composants critiques et mesurer l'impact d'un changement de composant. Chaque information affichée renvoie à l'élément XML dont elle provient.

## Contexte

Dans ce cas d'usage, l'OEM est le client et nous sommes son fournisseur de moteurs électriques. L'OEM gère le cycle de vie de ses véhicules dans 3DEXPERIENCE, mais nous n'avons pas accès à son environnement PLM. Notre seule source est un extrait XML qui décrit la structure produit, les assemblages, les composants, leurs attributs, leurs versions et leurs états de maturité.

Tout ce que l'application calcule ou affiche doit donc pouvoir être relié à un élément précis de cet extrait. C'est le principe directeur du projet.

## Fonctionnalités

- **Product Explorer** : naviguer dans la BOM et les assemblages, chercher et filtrer, inspecter un composant et ses propriétés, comparer les versions et les états de cycle de vie.
- **Product Intelligence** : identifier les composants critiques, les dépendances fournisseurs et les risques liés au cycle de vie.
- **Change Impact Analysis** : à partir d'un remplacement de composant envisagé, retrouver les assemblages, fournisseurs et coûts impactés.
- **Assistant IA (optionnel)** : interroger les données en langage naturel, chaque réponse affichant les sources PLM qui la justifient.

## Questions métier couvertes

| Question | Ce que l'application doit permettre de répondre |
|---|---|
| Understand | Quels sont les sous-ensembles majeurs du moteur ? Quelles versions et propriétés existent ? |
| Identify risks | Quels composants sont critiques ? Où sont les dépendances fournisseurs ou de cycle de vie ? |
| Assess a change | Si un composant est remplacé, quels assemblages, fournisseurs et coûts sont touchés ? |

## Architecture

```
XML OEM ──► parser ──► modèle de données ──┬──► analyses ──┬──► interface
                                           └──► agent ─────┘
```

Le parser lit l'extrait et le traduit en un modèle de données interne qui conserve, pour chaque objet, la référence de son élément XML d'origine. Les analyses et l'agent travaillent uniquement sur ce modèle, et l'interface affiche leurs résultats avec leurs sources.

| Dossier | Rôle |
|---|---|
| `data/raw/` | Extrait XML fourni par l'OEM, local uniquement (voir [Données](#données)) |
| `docs/` | Mapping XML vers le modèle de données, règles de risque, scénarios de démo |
| `src/plm/parser/` | Lecture du XML et mapping vers le modèle de données |
| `src/plm/analysis/` | Exploration de la BOM, calcul des risques, change impact |
| `src/plm/agent/` | Assistant IA avec citation des sources |
| `app/` | Interface web |
| `tests/` | Tests automatisés |
| `presentation/` | Supports du pitch |

## Données

L'extrait XML de l'OEM se place dans `data/raw/`. Il s'agit de données partenaires : il n'est jamais commité ni déployé publiquement. Le `.gitignore` exclut tout le contenu de `data/raw/` ainsi que tout fichier `*.xml` situé en dehors de `docs/`.

## Installation et lancement

Prérequis : Python 3.11 ou plus récent.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Charger l'extrait et les fichiers d'enrichissement, puis afficher un résumé et la couverture de l'enrichissement :

```bash
python -m plm.parser
```

Dans le code, tout passe par un point d'entrée unique :

```python
from plm.parser import load

data = load()
data.plm.where_used("prd-12535825-00000081")
data.enrichment.coverage(data.plm)
```

Tests et lint :

```bash
pytest
ruff check src tests && ruff format --check src tests
```

TODO : commande de lancement de l'interface, une fois le framework choisi.

## Organisation de l'équipe

| Workstream | Périmètre | Dossiers | Responsable |
|---|---|---|---|
| Data | Parsing et mapping du XML, modèle de données | `src/plm/parser`, `docs/` | |
| Analysis | Graphe BOM, risques, change impact | `src/plm/analysis` | |
| UI | Interface et parcours de démo | `app/` | |
| Agent | Assistant IA sourcé (optionnel) | `src/plm/agent` | |
| Pitch | Scénarios de démo, slides | `presentation/`, `docs/` | |

## Conventions Git

- `main` reste démontrable à tout moment.
- Branches courtes nommées `feat/<workstream>-<sujet>` ou `fix/<workstream>-<sujet>`.
- Toute modification passe par une PR vers `main`, relue par au moins une personne.
- Merge en squash.
- Merges fréquents, toutes les 2 à 3 heures, pour limiter les divergences.

## Planning

| Jour | Objectifs |
|---|---|
| J1 (4 nov.) | Analyse du XML et Product Explorer |
| J2 (5 nov.) | Risques, change impact et assistant |
| J3 (6 nov.) | Finalisation de la démo et pitch |

## Équipe

- Quentin TARLET
- Chloé DAUNAS
- Ayoub EL AMRANI
- Shubham BOLLU
- Téo MAHIEU
- Kishan Kumar MANTHRI
