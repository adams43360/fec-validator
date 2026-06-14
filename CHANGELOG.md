# Changelog

Toutes les modifications notables de ce projet sont documentées ici.

Format : `[VERSION] - YYYY-MM-DD`

---

## [1.00] - 2024-06-08

### Première version publique

**Périmètre fonctionnel :**
- Chargement d'un FEC au format TXT (séparateur tabulation, encodage UTF-8 ou Latin-1)
- 11 contrôles de conformité couvrant les anomalies les plus fréquentes constatées en contrôle fiscal :
  - `DATE_FORMAT` — format de date YYYYMMDD
  - `MONTANT_INVALIDE` — valeur non numérique dans Debit/Credit
  - `MONTANT_VIDE` — champ Debit ou Credit vide
  - `MONTANT_NEGATIF` — montant négatif (convention colonnes séparées)
  - `COMPTE_INVALIDE` — CompteNum ne commençant pas par 3 chiffres (PCG)
  - `SEQUENCE_RUPTURE` — trou dans la numérotation EcritureNum
  - `DESEQUILIBRE` — écriture non équilibrée (Σ Débit ≠ Σ Crédit, tolérance 0,01 €)
  - `CLASSE9` — présence de comptes analytiques (classe 9) dans le FEC général
  - `LIB_VIDE` — libellé EcritureLib absent
  - `AUX_CLASSE6` — CompAuxNum renseigné sur un compte de classe 6 (charges)
  - `DEVISE_INCOMPLETE` — Montantdevise et Idevise incohérents
- Mode de validation interactif (confirmation par erreur avec `[O/n]`)
- Correction automatique des erreurs corrigeables (dates, montants invalides/vides, libellés manquants, tiers sur charges)
- Export du FEC corrigé en `fec_corrige.txt` (TSV, UTF-8 avec BOM)
- Génération d'un rapport PDF professionnel (`rapport_corrections_YYYYMMDD.pdf`) avec résumé exécutif, KPIs, tableau par type d'erreur et détail ligne à ligne

**Fichiers de test fournis :**
- `fec_propre.txt` — FEC valide (25 lignes, 10 écritures, exercice 2024, société de services)
- `fec_sale.txt` — FEC avec 10 erreurs injectées couvrant chacun des contrôles principaux

**Dépendances :** `pandas`, `fpdf2`
