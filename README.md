# FEC Validator

Outil Python de **validation et correction interactive** de fichiers FEC (Fichier des Écritures Comptables), conforme à l'article A. 47 A-1 du Livre des procédures fiscales (LPF).

> Version actuelle : **1.00**

---

## Contexte réglementaire

Le FEC est un fichier obligatoire remis à l'administration fiscale (DGFiP) lors d'un contrôle fiscal informatisé (CFE). Il doit respecter un format précis : 18 champs séparés par des tabulations, encodage UTF-8, et des règles comptables strictes (équilibre débit/crédit, séquence de numérotation, format de dates, etc.).

Une anomalie dans le FEC peut entraîner une **majoration de 5 000 € par exercice** ou une reconstitution du résultat imposable par l'administration.

---

## Fonctionnalités (V1.00)

- Chargement du FEC (UTF-8 ou Latin-1, séparateur tabulation)
- **11 contrôles de conformité** couvrant format, montants, comptes, équilibre et cohérence
- Mode interactif : affichage de chaque erreur avec proposition de correction `[O/n]`
- Export du FEC corrigé (`fec_corrige.txt`)
- Génération d'un **rapport PDF professionnel** (`rapport_corrections_YYYYMMDD.pdf`) avec KPIs, tableau de synthèse par type d'erreur et détail ligne à ligne

---

## Installation

```bash
pip install pandas fpdf2
```

Python 3.9+ requis. Aucune autre dépendance externe.

---

## Utilisation

```bash
python validate_fec.py <fichier_fec.txt>
```

**Exemple avec le fichier de test fourni :**

```bash
python validate_fec.py fec_sale.txt
```

Le script affiche chaque anomalie, propose une correction et demande confirmation :

```
── Erreur 2/12 ──────────────────────────────────
[Ligne   2] DATE_FORMAT    Champ: EcritureDate    Valeur: «03/01/2024»
             → Format de date invalide. Attendu YYYYMMDD, trouvé «03/01/2024».
               Correction proposée : 20240103

  Appliquer cette correction ? [O/n] :
```

À la fin, deux fichiers sont générés dans le même dossier que le FEC d'entrée :
- `fec_corrige.txt` — FEC avec les corrections acceptées
- `rapport_corrections_YYYYMMDD.pdf` — rapport destiné à l'expert-comptable

---

## Fichiers de test fournis

| Fichier | Description |
|---|---|
| `fec_propre.txt` | FEC valide — 10 écritures, 25 lignes, exercice 2024, société de services |
| `fec_sale.txt` | Même FEC avec **10 erreurs injectées** (voir section suivante) |

---

## Contrôles implémentés

### `DATE_FORMAT` — Format de date invalide

**Champs contrôlés :** `EcritureDate`, `PieceDate`

**Règle :** Les dates doivent être au format `YYYYMMDD` (8 chiffres consécutifs), conformément au cahier des charges DGFiP.

**Ce que l'on détecte :** Toute valeur ne correspondant pas à ce format — par exemple `03/01/2024`, `2024-01-03`, ou une chaîne non date.

**Correction automatique :** Si le format est reconnu (`DD/MM/YYYY`, `DD-MM-YYYY`, `YYYY-MM-DD`), conversion vers `YYYYMMDD`. Sinon, signalement manuel.

**Erreur injectée dans `fec_sale.txt` :** Ligne 2, `EcritureDate` = `03/01/2024`.

---

### `MONTANT_INVALIDE` — Valeur non numérique dans Debit ou Credit

**Champs contrôlés :** `Debit`, `Credit`

**Règle :** Les montants doivent être des nombres décimaux avec point ou virgule comme séparateur (ex : `1200.00`).

**Ce que l'on détecte :** Toute valeur non convertible en nombre décimal — texte libre, symboles (`N/A`, `-`, `#VALEUR!`), chaînes vides traitées séparément.

**Correction automatique :** Remplacement par `0.00`.

**Erreur injectée dans `fec_sale.txt` :** Ligne 3, `Debit` = `N/A`.

---

### `MONTANT_VIDE` — Champ Debit ou Credit vide

**Champs contrôlés :** `Debit`, `Credit`

**Règle :** Les champs montant ne peuvent pas être vides. La valeur neutre est `0.00`.

**Ce que l'on détecte :** Cellule vide (chaîne vide ou espace seul).

**Correction automatique :** Remplacement par `0.00`.

**Erreur injectée dans `fec_sale.txt` :** Ligne 13, `Credit` vide.

---

### `MONTANT_NEGATIF` — Montant négatif

**Champs contrôlés :** `Debit`, `Credit`

**Règle :** Le FEC utilise la convention en colonnes séparées (Debit / Credit) : les montants sont toujours positifs. Un montant négatif indique une erreur de saisie ou un export incorrect depuis le logiciel comptable.

**Ce que l'on détecte :** Toute valeur numérique strictement inférieure à 0.

**Correction automatique :** Passage en valeur absolue.

---

### `COMPTE_INVALIDE` — CompteNum non conforme au PCG

**Champ contrôlé :** `CompteNum`

**Règle :** Le numéro de compte doit commencer par au moins **3 chiffres** consécutifs, conformément au Plan Comptable Général (PCG) français. Les comptes sont organisés en classes 1 à 9 et les sous-comptes étendent ce préfixe numérique.

**Ce que l'on détecte :** Toute valeur dont les 3 premiers caractères contiennent une lettre ou un caractère spécial — par exemple `ACH001`, `VTE-100`, `Client`.

**Correction automatique :** Extraction des chiffres présents dans la valeur, complétés à 3 caractères minimum (proposition indicative, à vérifier manuellement).

**Erreur injectée dans `fec_sale.txt` :** Ligne 5, `CompteNum` = `ACH001`.

---

### `SEQUENCE_RUPTURE` — Rupture dans la numérotation EcritureNum

**Champ contrôlé :** `EcritureNum`

**Règle :** Les numéros d'écritures doivent former une séquence **continue et croissante** sans trou. L'administration fiscale contrôle systématiquement l'intégrité de cette séquence pour détecter des suppressions d'écritures.

**Ce que l'on détecte :** Tout saut dans la liste des `EcritureNum` distincts — par exemple la séquence `1, 2, 3, 5` signale la disparition de l'écriture `4`.

**Correction automatique :** Aucune (une rupture de séquence nécessite une analyse comptable manuelle pour déterminer si une écriture a été supprimée ou si la numérotation est simplement incorrecte).

**Erreur injectée dans `fec_sale.txt` :** Saut de `EcritureNum` 3 → 5 (le 4 est absent).

---

### `DESEQUILIBRE` — Écriture comptable non équilibrée

**Champs contrôlés :** `EcritureNum`, `Debit`, `Credit`

**Règle :** En comptabilité en partie double, **toute écriture doit être équilibrée** : la somme des débits doit égaler la somme des crédits pour un même `EcritureNum`. La tolérance acceptée est de 0,01 € (arrondi flottant).

**Ce que l'on détecte :** Pour chaque groupe de lignes partageant le même `EcritureNum`, on calcule `Σ Debit` et `Σ Credit`. Si `|Σ Debit - Σ Crédit| > 0,01`, l'écriture est signalée avec le détail des montants et les numéros de lignes concernés.

**Correction automatique :** Aucune (le rééquilibrage nécessite une décision comptable).

**Erreur injectée dans `fec_sale.txt` :** Écriture n°5 — `Σ Débit = 3 600,00 €`, `Σ Crédit = 3 100,00 €`, écart de 500 €.

---

### `CLASSE9` — Compte analytique (classe 9) dans le FEC général

**Champ contrôlé :** `CompteNum`

**Règle :** Les comptes de **classe 9** sont réservés à la comptabilité analytique (ou de gestion). Le FEC porte uniquement sur la comptabilité générale (classes 1 à 8). La présence d'un compte de classe 9 indique un export incorrect depuis le logiciel comptable.

**Ce que l'on détecte :** Tout `CompteNum` dont le premier caractère est `9`.

**Correction automatique :** Aucune (la ligne doit être supprimée ou reclassée).

**Erreur injectée dans `fec_sale.txt` :** Ligne 23, `CompteNum` = `912000`.

---

### `LIB_VIDE` — Libellé d'écriture absent

**Champ contrôlé :** `EcritureLib`

**Règle :** Le libellé d'écriture est obligatoire. Il permet d'identifier la nature de l'opération et est examiné par le vérificateur lors du CFE.

**Ce que l'on détecte :** Cellule vide ou contenant uniquement des espaces.

**Correction automatique :** Remplacement par le marqueur `LIBELLE MANQUANT` (à préciser ensuite manuellement).

**Erreur injectée dans `fec_sale.txt` :** Ligne 12, `EcritureLib` vide.

---

### `AUX_CLASSE6` — Tiers (CompAuxNum) sur un compte de charges

**Champs contrôlés :** `CompteNum`, `CompAuxNum`

**Règle :** Le compte auxiliaire (`CompAuxNum`) est réservé aux comptes de **tiers** : fournisseurs (401xxx), clients (411xxx), autres tiers. Les comptes de **classe 6** (charges) sont des comptes de gestion et ne portent pas de tiers dans le PCG français.

**Ce que l'on détecte :** Toute ligne où `CompteNum` commence par `6` **et** `CompAuxNum` est renseigné.

**Correction automatique :** Vidage de `CompAuxNum` (et `CompAuxLib` associé).

**Erreur injectée dans `fec_sale.txt` :** Ligne 26, `CompteNum` = `601000`, `CompAuxNum` = `F003`.

---

### `DEVISE_INCOMPLETE` — Incohérence entre Montantdevise et Idevise

**Champs contrôlés :** `Montantdevise`, `Idevise`

**Règle :** Les champs devise sont couplés : si `Montantdevise` est renseigné (valeur non nulle), alors `Idevise` (code ISO de la devise, ex : `USD`, `GBP`) **doit** l'être aussi — et vice versa. Un seul des deux renseigné constitue une anomalie.

**Ce que l'on détecte :** `Montantdevise` non nul avec `Idevise` vide, ou `Idevise` renseigné avec `Montantdevise` vide/nul.

**Correction automatique :** Aucune (le code devise doit être saisi manuellement).

**Erreur injectée dans `fec_sale.txt` :** Ligne 28, `Montantdevise` = `650.00` sans `Idevise`.

---

## Rapport PDF généré

Le rapport inclut :
- **Résumé exécutif** avec KPIs (erreurs totales, corrigées, refusées, sans correction automatique)
- **Tableau de répartition** des anomalies par type avec comptage et taux de correction
- **Détail ligne à ligne** de chaque anomalie avec statut (CORRIGÉE / REFUSÉE / MANUELLE)
- **Conclusion et recommandations** à destination de l'expert-comptable
- Références légales (Article A. 47 A-1 LPF, BOI-CF-IOR-60-40-20)

---

## Roadmap

Voir [CHANGELOG.md](CHANGELOG.md) pour l'historique des versions.

Améliorations prévues :
- Export du rapport en Excel (avec onglet par type d'erreur)
- Détection des doublons d'écritures (même `PieceRef` + même montant)
- Contrôle de la cohérence des dates (date écriture < date pièce)
- Interface CLI avec options `--auto` (correction sans confirmation) et `--report-only`
- Support multi-exercices

---

## Références légales

- Article A. 47 A-1 du Livre des procédures fiscales (LPF)
- BOI-CF-IOR-60-40-20 — Fichier des écritures comptables
- Arrêté du 29 juillet 2013 relatif aux informations à fournir pour les contrôles fiscaux
