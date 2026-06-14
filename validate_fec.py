#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_fec.py — Outil de validation et correction de fichiers FEC
Fichier des Ecritures Comptables (Article A. 47 A-1 du Livre des procedures fiscales)

Version : 1.01
Date    : 2024-06-14
Auteur  : Damien Ruiz

Usage :
    python validate_fec.py <fichier_fec.txt>

Controles implementes (V1.00) :
    DATE_FORMAT      - Format EcritureDate/PieceDate doit etre YYYYMMDD
    MONTANT_INVALIDE - Debit/Credit doivent etre des nombres decimaux
    MONTANT_VIDE     - Debit/Credit ne doivent pas etre vides
    MONTANT_NEGATIF  - Debit/Credit ne peuvent pas etre negatifs
    COMPTE_INVALIDE  - CompteNum doit commencer par au moins 3 chiffres (PCG)
    SEQUENCE_RUPTURE - La numerotation EcritureNum doit etre continue
    DESEQUILIBRE     - Pour chaque EcritureNum, somme(Debit) == somme(Credit)
    CLASSE9          - Les comptes analytiques (classe 9) ne doivent pas figurer en FEC general
    LIB_VIDE         - EcritureLib ne peut pas etre vide
    AUX_CLASSE6      - CompAuxNum ne doit pas etre renseigne sur un compte de classe 6
    DEVISE_INCOMPLETE - Montantdevise et Idevise doivent etre renseignes ensemble
"""

__version__ = "1.01"

import sys
import os
import re
import json
from datetime import datetime, date
from pathlib import Path
from decimal import Decimal, InvalidOperation
from collections import defaultdict

import pandas as pd

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Les 18 champs obligatoires du FEC selon l'administration fiscale
CHAMPS_OBLIGATOIRES = [
    "JournalCode", "JournalLib", "EcritureNum", "EcritureDate",
    "CompteNum", "CompteLib", "CompAuxNum", "CompAuxLib",
    "PieceRef", "PieceDate", "EcritureLib", "Debit",
    "Credit", "EcritureLet", "DateLet", "ValidDate",
    "Montantdevise", "Idevise"
]

# Format de date FEC attendu
FORMAT_DATE_FEC = "%Y%m%d"

# Tolérance pour l'équilibre débit/crédit (arrondi float)
TOLERANCE_EQUILIBRE = Decimal("0.01")


# ---------------------------------------------------------------------------
# Classe représentant une erreur détectée
# ---------------------------------------------------------------------------

class ErreurFEC:
    """Représente une anomalie détectée dans le fichier FEC."""

    def __init__(self, ligne: int, champ: str, valeur: str, description: str, code: str):
        self.ligne = ligne          # Numéro de ligne dans le fichier (1-indexé, hors entête)
        self.champ = champ          # Nom du champ concerné
        self.valeur = valeur        # Valeur fautive trouvée
        self.description = description  # Message d'erreur lisible
        self.code = code            # Code court pour regroupement (ex: "DATE_FORMAT")
        self.corrigee = False       # Indique si la correction a été acceptée
        self.valeur_corrigee = None # Valeur proposée après correction

    def __str__(self):
        return (
            f"[Ligne {self.ligne:>3}] {self.code:<20} "
            f"Champ: {self.champ:<16} Valeur: «{self.valeur}»\n"
            f"             → {self.description}"
        )


# ---------------------------------------------------------------------------
# Parsing du FEC
# ---------------------------------------------------------------------------

def charger_fec(chemin: str) -> pd.DataFrame:
    """
    Charge un fichier FEC (séparateur tabulation) en DataFrame pandas.
    Gère l'encodage UTF-8 avec fallback sur latin-1 (fréquent en comptabilité française).
    """
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(
                chemin,
                sep="\t",
                dtype=str,          # Tout en string pour valider nous-mêmes
                keep_default_na=False,  # Ne pas convertir "" en NaN automatiquement
                encoding=encoding,
            )
            # Supprimer les espaces parasites dans les noms de colonnes
            df.columns = [c.strip() for c in df.columns]
            return df
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Impossible de lire le fichier {chemin} (encodage non reconnu).")


# ---------------------------------------------------------------------------
# Règles de validation — chaque fonction retourne une liste d'ErreurFEC
# ---------------------------------------------------------------------------

def valider_format_date(df: pd.DataFrame) -> list[ErreurFEC]:
    """Règle 1 : EcritureDate et PieceDate doivent être au format YYYYMMDD."""
    erreurs = []
    for champ in ("EcritureDate", "PieceDate"):
        if champ not in df.columns:
            continue
        for idx, valeur in df[champ].items():
            valeur = str(valeur).strip()
            if not valeur:
                continue
            # On accepte YYYYMMDD (8 chiffres)
            if not re.fullmatch(r"\d{8}", valeur):
                # Tentative de conversion depuis les formats alternatifs courants
                valeur_corrigee = None
                for fmt_alt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(valeur, fmt_alt)
                        valeur_corrigee = dt.strftime("%Y%m%d")
                        break
                    except ValueError:
                        continue

                if valeur_corrigee:
                    desc = (
                        f"Format de date invalide. Attendu YYYYMMDD, "
                        f"trouvé «{valeur}». Correction proposée : {valeur_corrigee}"
                    )
                else:
                    desc = f"Format de date invalide et non convertible : «{valeur}»."

                err = ErreurFEC(
                    ligne=idx + 2,
                    champ=champ,
                    valeur=valeur,
                    description=desc,
                    code="DATE_FORMAT",
                )
                err.valeur_corrigee = valeur_corrigee
                erreurs.append(err)
    return erreurs


def valider_montants(df: pd.DataFrame) -> list[ErreurFEC]:
    """Règle 2 : Debit et Credit doivent être des nombres décimaux ≥ 0."""
    erreurs = []
    for champ in ("Debit", "Credit"):
        if champ not in df.columns:
            continue
        for idx, valeur in df[champ].items():
            valeur_str = str(valeur).strip()
            # Champ vide → on propose 0.00
            if valeur_str == "":
                err = ErreurFEC(
                    ligne=idx + 2,
                    champ=champ,
                    valeur="(vide)",
                    description=f"Le champ {champ} est vide. Correction proposée : 0.00",
                    code="MONTANT_VIDE",
                )
                err.valeur_corrigee = "0.00"
                erreurs.append(err)
                continue
            # Valeur non numérique (ex: "N/A", texte libre)
            try:
                val = Decimal(valeur_str.replace(",", "."))
                if val < 0:
                    err = ErreurFEC(
                        ligne=idx + 2,
                        champ=champ,
                        valeur=valeur_str,
                        description=f"Montant négatif non autorisé dans {champ}.",
                        code="MONTANT_NEGATIF",
                    )
                    err.valeur_corrigee = str(abs(val))
                    erreurs.append(err)
            except InvalidOperation:
                err = ErreurFEC(
                    ligne=idx + 2,
                    champ=champ,
                    valeur=valeur_str,
                    description=(
                        f"Valeur non numérique dans {champ} : «{valeur_str}». "
                        f"Correction proposée : 0.00"
                    ),
                    code="MONTANT_INVALIDE",
                )
                err.valeur_corrigee = "0.00"
                erreurs.append(err)
    return erreurs


def valider_compte_num(df: pd.DataFrame) -> list[ErreurFEC]:
    """
    Règle 3 : CompteNum doit commencer par au moins 3 chiffres
    (plan comptable général français).
    """
    erreurs = []
    if "CompteNum" not in df.columns:
        return erreurs
    for idx, valeur in df["CompteNum"].items():
        valeur_str = str(valeur).strip()
        if not valeur_str:
            continue
        if not re.match(r"^\d{3}", valeur_str):
            # On extrait uniquement les chiffres comme suggestion
            chiffres = re.sub(r"[^\d]", "", valeur_str)
            suggestion = chiffres.zfill(3) if chiffres else "999999"
            err = ErreurFEC(
                ligne=idx + 2,
                champ="CompteNum",
                valeur=valeur_str,
                description=(
                    f"CompteNum «{valeur_str}» ne commence pas par 3 chiffres. "
                    f"Suggestion : {suggestion}"
                ),
                code="COMPTE_INVALIDE",
            )
            err.valeur_corrigee = suggestion
            erreurs.append(err)
    return erreurs


def valider_sequence_ecriture(df: pd.DataFrame) -> list[ErreurFEC]:
    """
    Règle 4 : La séquence des EcritureNum doit être continue et croissante
    (pas de trous dans la numérotation).
    """
    erreurs = []
    if "EcritureNum" not in df.columns:
        return erreurs

    # On ne prend que les valeurs uniques pour vérifier la séquence
    nums = []
    for v in df["EcritureNum"]:
        v_str = str(v).strip()
        try:
            nums.append(int(v_str))
        except ValueError:
            pass  # Les erreurs de format sont gérées ailleurs

    nums_uniques = sorted(set(nums))
    for i in range(1, len(nums_uniques)):
        attendu = nums_uniques[i - 1] + 1
        trouve = nums_uniques[i]
        if trouve != attendu:
            err = ErreurFEC(
                ligne=0,  # Erreur de séquence : pas de ligne unique
                champ="EcritureNum",
                valeur=f"{nums_uniques[i-1]} → {trouve}",
                description=(
                    f"Rupture de séquence dans EcritureNum : "
                    f"après {nums_uniques[i-1]}, attendu {attendu}, trouvé {trouve}."
                ),
                code="SEQUENCE_RUPTURE",
            )
            # Pas de correction automatique pour une rupture de séquence
            erreurs.append(err)
    return erreurs


def valider_equilibre(df: pd.DataFrame) -> list[ErreurFEC]:
    """
    Règle 5 : Pour chaque EcritureNum, la somme des Debits doit égaler
    la somme des Credits (équilibre comptable).
    """
    erreurs = []
    if not {"EcritureNum", "Debit", "Credit"}.issubset(df.columns):
        return erreurs

    groupes = defaultdict(lambda: {"debit": Decimal("0"), "credit": Decimal("0"), "lignes": []})

    for idx, row in df.iterrows():
        num = str(row.get("EcritureNum", "")).strip()
        try:
            d = Decimal(str(row.get("Debit", "0")).strip().replace(",", ".") or "0")
        except InvalidOperation:
            d = Decimal("0")
        try:
            c = Decimal(str(row.get("Credit", "0")).strip().replace(",", ".") or "0")
        except InvalidOperation:
            c = Decimal("0")

        groupes[num]["debit"] += d
        groupes[num]["credit"] += c
        groupes[num]["lignes"].append(idx + 2)

    for num, totaux in groupes.items():
        diff = abs(totaux["debit"] - totaux["credit"])
        if diff > TOLERANCE_EQUILIBRE:
            lignes_str = ", ".join(str(l) for l in totaux["lignes"])
            err = ErreurFEC(
                ligne=totaux["lignes"][0],
                champ="Debit/Credit",
                valeur=f"Σ Débit={totaux['debit']}, Σ Crédit={totaux['credit']}",
                description=(
                    f"Écriture n°{num} non équilibrée : "
                    f"Débit={totaux['debit']}, Crédit={totaux['credit']}, "
                    f"écart={diff}. Lignes concernées : {lignes_str}"
                ),
                code="DESEQUILIBRE",
            )
            erreurs.append(err)
    return erreurs


def valider_classe9(df: pd.DataFrame) -> list[ErreurFEC]:
    """
    Règle 6 : Les comptes de classe 9 (analytique) ne doivent pas figurer
    dans un FEC de comptabilité générale.
    """
    erreurs = []
    if "CompteNum" not in df.columns:
        return erreurs
    for idx, valeur in df["CompteNum"].items():
        valeur_str = str(valeur).strip()
        if valeur_str.startswith("9"):
            err = ErreurFEC(
                ligne=idx + 2,
                champ="CompteNum",
                valeur=valeur_str,
                description=(
                    f"Compte de classe 9 «{valeur_str}» détecté. "
                    f"Les comptes analytiques (classe 9) ne doivent pas figurer dans le FEC."
                ),
                code="CLASSE9",
            )
            erreurs.append(err)
    return erreurs


def valider_ecriture_lib(df: pd.DataFrame) -> list[ErreurFEC]:
    """Règle 7 : EcritureLib (libellé d'écriture) ne doit pas être vide."""
    erreurs = []
    if "EcritureLib" not in df.columns:
        return erreurs
    for idx, valeur in df["EcritureLib"].items():
        if str(valeur).strip() == "":
            err = ErreurFEC(
                ligne=idx + 2,
                champ="EcritureLib",
                valeur="(vide)",
                description="Libellé d'écriture vide. Correction proposée : «LIBELLE MANQUANT»",
                code="LIB_VIDE",
            )
            err.valeur_corrigee = "LIBELLE MANQUANT"
            erreurs.append(err)
    return erreurs


def valider_comp_aux_classe6(df: pd.DataFrame) -> list[ErreurFEC]:
    """
    Règle 8 : CompAuxNum ne doit pas être renseigné sur les comptes de classe 6
    (charges), car ce sont des comptes de gestion sans tiers.
    """
    erreurs = []
    if not {"CompteNum", "CompAuxNum"}.issubset(df.columns):
        return erreurs
    for idx, row in df.iterrows():
        compte = str(row.get("CompteNum", "")).strip()
        comp_aux = str(row.get("CompAuxNum", "")).strip()
        if compte.startswith("6") and comp_aux:
            err = ErreurFEC(
                ligne=idx + 2,
                champ="CompAuxNum",
                valeur=comp_aux,
                description=(
                    f"CompAuxNum «{comp_aux}» renseigné sur le compte de classe 6 "
                    f"«{compte}». Les comptes de charges n'ont pas de tiers associé."
                ),
                code="AUX_CLASSE6",
            )
            err.valeur_corrigee = ""
            erreurs.append(err)
    return erreurs


def valider_devise(df: pd.DataFrame) -> list[ErreurFEC]:
    """
    Règle 9 : Si Montantdevise est renseigné (non vide, non zéro),
    alors Idevise doit l'être aussi, et inversement.
    """
    erreurs = []
    if not {"Montantdevise", "Idevise"}.issubset(df.columns):
        return erreurs
    for idx, row in df.iterrows():
        montant = str(row.get("Montantdevise", "")).strip()
        idevise = str(row.get("Idevise", "")).strip()

        montant_renseigne = bool(montant) and montant not in ("0", "0.00", "0,00")
        idevise_renseigne = bool(idevise)

        if montant_renseigne and not idevise_renseigne:
            err = ErreurFEC(
                ligne=idx + 2,
                champ="Idevise",
                valeur="(vide)",
                description=(
                    f"Montantdevise={montant} est renseigné mais Idevise est vide. "
                    f"Le code devise (ex: USD, GBP) est obligatoire."
                ),
                code="DEVISE_INCOMPLETE",
            )
            erreurs.append(err)
        elif not montant_renseigne and idevise_renseigne:
            err = ErreurFEC(
                ligne=idx + 2,
                champ="Montantdevise",
                valeur="(vide)",
                description=(
                    f"Idevise={idevise} est renseigné mais Montantdevise est vide ou nul. "
                    f"Incohérence devise."
                ),
                code="DEVISE_INCOMPLETE",
            )
            erreurs.append(err)
    return erreurs


# ---------------------------------------------------------------------------
# Orchestration de toutes les règles
# ---------------------------------------------------------------------------

def valider_fec(df: pd.DataFrame) -> list[ErreurFEC]:
    """Lance toutes les règles de validation et retourne la liste consolidée."""
    toutes_regles = [
        valider_format_date,
        valider_montants,
        valider_compte_num,
        valider_sequence_ecriture,
        valider_equilibre,
        valider_classe9,
        valider_ecriture_lib,
        valider_comp_aux_classe6,
        valider_devise,
    ]
    erreurs = []
    for regle in toutes_regles:
        erreurs.extend(regle(df))

    # Tri par numéro de ligne pour affichage lisible
    erreurs.sort(key=lambda e: e.ligne)
    return erreurs


# ---------------------------------------------------------------------------
# Mode interactif — validation et correction une par une
# ---------------------------------------------------------------------------

def mode_interactif(df: pd.DataFrame, erreurs: list[ErreurFEC]) -> pd.DataFrame:
    """
    Affiche chaque erreur et demande confirmation avant d'appliquer la correction.
    Retourne le DataFrame modifié.
    """
    if not erreurs:
        print("\n✓ Aucune erreur détectée dans ce fichier FEC.")
        return df

    print(f"\n{'='*70}")
    print(f"  {len(erreurs)} erreur(s) détectée(s) — validation interactive")
    print(f"{'='*70}\n")

    df_corrige = df.copy()

    for i, erreur in enumerate(erreurs, 1):
        print(f"\n── Erreur {i}/{len(erreurs)} ──────────────────────────────────")
        print(erreur)

        if erreur.valeur_corrigee is not None:
            print(f"\n  Correction proposée : «{erreur.valeur_corrigee}»")
            reponse = input("  Appliquer cette correction ? [O/n] : ").strip().lower()
            if reponse in ("", "o", "oui", "y", "yes"):
                # Application de la correction dans le DataFrame
                _appliquer_correction(df_corrige, erreur)
                erreur.corrigee = True
                print("  → Correction appliquée.")
            else:
                print("  → Correction refusée, erreur conservée.")
        else:
            print("\n  ⚠ Aucune correction automatique disponible pour cette erreur.")
            input("  [Entrée pour continuer] ")

    return df_corrige


def _appliquer_correction(df: pd.DataFrame, erreur: ErreurFEC) -> None:
    """
    Modifie le DataFrame en place pour corriger une erreur donnée.
    Identifie la ligne par erreur.ligne (1-indexé, hors entête).
    """
    # erreur.ligne = numéro de ligne dans le fichier (entête = 1, données à partir de 2)
    # Dans le DataFrame, l'index commence à 0 → ligne_df = erreur.ligne - 2
    if erreur.ligne == 0:
        # Erreur de séquence globale, pas applicable ligne par ligne
        return

    idx_df = erreur.ligne - 2  # Conversion numéro de ligne → index DataFrame

    if idx_df < 0 or idx_df >= len(df):
        return

    champ = erreur.champ

    # Cas spécial : erreur sur deux champs (Debit/Credit pour déséquilibre)
    if "/" in champ:
        return  # Déséquilibre non corrigeable automatiquement

    if champ in df.columns:
        df.at[idx_df, champ] = erreur.valeur_corrigee


# ---------------------------------------------------------------------------
# Export du FEC corrigé
# ---------------------------------------------------------------------------

def exporter_fec(df: pd.DataFrame, chemin_sortie: str) -> None:
    """Exporte le DataFrame au format FEC (TSV, encodage UTF-8 avec BOM pour Excel)."""
    df.to_csv(
        chemin_sortie,
        sep="\t",
        index=False,
        encoding="utf-8-sig",  # BOM pour compatibilité Excel
    )
    print(f"\n✓ FEC corrigé exporté : {chemin_sortie}")


# ---------------------------------------------------------------------------
# Génération du rapport PDF
# ---------------------------------------------------------------------------

def generer_rapport_pdf(
    chemin_fec: str,
    erreurs: list[ErreurFEC],
    chemin_sortie: str,
) -> None:
    """
    Génère un rapport PDF professionnel listant toutes les erreurs détectées,
    les corrections appliquées ou refusées, avec un résumé exécutif.
    Utilise fpdf2.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        print(
            "\n⚠ fpdf2 non installé. Installez-le avec : pip install fpdf2\n"
            "  Le rapport PDF ne sera pas généré."
        )
        return

    def _pdf_str(s: str) -> str:
        """
        Translitère tous les caractères hors latin-1 pour la compatibilité
        fpdf2 avec les polices core (Helvetica).
        """
        import unicodedata
        # Table de remplacement explicite pour les cas fréquents
        TABLE = {
            "—": "-", "–": "-",
            "≠": "!=",   # ≠
            "≤": "<=",   # ≤
            "≥": ">=",   # ≥
            "✓": "[OK]", # ✓
            "✔": "[OK]", # ✔
            "⚠": "[!]",  # ⚠
            "•": "-",    # •
            "«": '"',    # «
            "»": '"',    # »
            "'": "'", "'": "'",
            """: '"', """: '"',
            "…": "...",
        }
        result = []
        for ch in s:
            if ch in TABLE:
                result.append(TABLE[ch])
            else:
                # Pour les autres : normalisation NFD puis suppression des diacritiques
                nfd = unicodedata.normalize("NFD", ch)
                ascii_ch = nfd.encode("ascii", "ignore").decode("ascii")
                result.append(ascii_ch if ascii_ch else "?")
        return "".join(result)

    # Couleurs corporate Pennylane-inspired
    COULEUR_TITRE = (30, 30, 80)          # Bleu marine
    COULEUR_SECTION = (50, 100, 170)      # Bleu moyen
    COULEUR_ERREUR = (200, 50, 50)        # Rouge
    COULEUR_OK = (40, 140, 80)            # Vert
    COULEUR_AVERTISSEMENT = (200, 130, 0) # Orange
    COULEUR_LIGNE_PAIRE = (245, 247, 252) # Gris très clair
    COULEUR_ENTETE = (220, 228, 245)      # Bleu très clair

    nb_corrigees = sum(1 for e in erreurs if e.corrigee)
    nb_refusees = sum(1 for e in erreurs if not e.corrigee and e.valeur_corrigee is not None)
    nb_sans_correction = sum(1 for e in erreurs if e.valeur_corrigee is None)

    # Statistiques par type d'erreur
    stats_codes = defaultdict(int)
    for e in erreurs:
        stats_codes[e.code] += 1

    class PDF(FPDF):
        # Surcharge pour translitérer automatiquement tous les textes
        def cell(self, w, h=0, txt="", **kwargs):
            return super().cell(w, h, _pdf_str(str(txt)), **kwargs)

        def multi_cell(self, w, h, txt="", **kwargs):
            return super().multi_cell(w, h, _pdf_str(str(txt)), **kwargs)

        def header(self):
            # Barre de titre colorée
            self.set_fill_color(*COULEUR_TITRE)
            self.rect(0, 0, 210, 18, "F")
            self.set_font("Helvetica", "B", 13)
            self.set_text_color(255, 255, 255)
            self.set_xy(10, 4)
            self.cell(0, 10, "Rapport de validation FEC - Fichier des Ecritures Comptables", ln=False)
            self.set_xy(0, 19)
            self.set_text_color(0, 0, 0)

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 6, f"Page {self.page_no()} — Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} — Usage confidentiel", align="C")

        def titre_section(self, texte: str):
            self.ln(4)
            self.set_fill_color(*COULEUR_SECTION)
            self.set_text_color(255, 255, 255)
            self.set_font("Helvetica", "B", 10)
            self.cell(0, 8, f"  {texte}", ln=True, fill=True)
            self.set_text_color(0, 0, 0)
            self.ln(2)

        def kpi_box(self, label: str, valeur: str, couleur):
            x, y = self.get_x(), self.get_y()
            self.set_fill_color(*couleur)
            self.set_text_color(255, 255, 255)
            self.set_font("Helvetica", "B", 18)
            self.cell(38, 16, valeur, border=0, fill=True, align="C")
            self.set_font("Helvetica", "", 7)
            self.set_xy(x, y + 16)
            self.set_fill_color(240, 240, 245)
            self.set_text_color(60, 60, 60)
            self.cell(38, 7, label, border=0, fill=True, align="C")
            self.set_xy(x + 40, y)

    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── En-tête informatif ──────────────────────────────────────────────────
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*COULEUR_TITRE)
    nom_fichier = Path(chemin_fec).name
    pdf.cell(0, 7, f"Fichier analysé : {nom_fichier}", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 5, f"Date d'analyse : {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}", ln=True)
    pdf.cell(0, 5, f"Chemin complet : {os.path.abspath(chemin_fec)}", ln=True)
    pdf.ln(4)

    # ── Résumé exécutif (KPI) ───────────────────────────────────────────────
    pdf.titre_section("1. Résumé exécutif")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    if not erreurs:
        pdf.set_text_color(*COULEUR_OK)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 10, "✓ Aucune anomalie détectée. Le fichier FEC est conforme.", ln=True)
    else:
        statut = "NON CONFORME" if nb_corrigees < len(erreurs) else "CORRIGÉ"
        couleur_statut = COULEUR_ERREUR if statut == "NON CONFORME" else COULEUR_OK
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*couleur_statut)
        pdf.cell(0, 6, f"Statut du fichier : {statut}", ln=True)
        pdf.set_text_color(40, 40, 40)
        pdf.ln(3)

        # Boîtes KPI
        pdf.kpi_box("ERREURS TOTALES", str(len(erreurs)), COULEUR_ERREUR)
        pdf.kpi_box("CORRIGÉES", str(nb_corrigees), COULEUR_OK)
        pdf.kpi_box("REFUSÉES", str(nb_refusees), COULEUR_AVERTISSEMENT)
        pdf.kpi_box("SANS CORRECTION", str(nb_sans_correction), (120, 120, 120))
        pdf.ln(26)

        # Tableau de synthèse par type
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(40, 40, 40)
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 5, "Ce rapport a été généré automatiquement par l'outil de validation FEC. "
                        "Il est destiné à être transmis à l'expert-comptable ou au commissaire aux comptes.", ln=True)

    # ── Statistiques par type d'erreur ──────────────────────────────────────
    if stats_codes:
        pdf.titre_section("2. Répartition des anomalies par type")

        descriptions_codes = {
            "DATE_FORMAT":       "Format de date incorrect (attendu YYYYMMDD)",
            "MONTANT_VIDE":      "Champ montant vide (Debit ou Credit)",
            "MONTANT_INVALIDE":  "Valeur non numérique dans un champ montant",
            "MONTANT_NEGATIF":   "Montant négatif dans Debit ou Credit",
            "COMPTE_INVALIDE":   "CompteNum ne commençant pas par 3 chiffres",
            "SEQUENCE_RUPTURE":  "Rupture dans la séquence EcritureNum",
            "DESEQUILIBRE":      "Écriture déséquilibrée (Débit ≠ Crédit)",
            "CLASSE9":           "Compte de classe 9 (analytique) détecté",
            "LIB_VIDE":          "EcritureLib (libellé) vide",
            "AUX_CLASSE6":       "CompAuxNum renseigné sur un compte de classe 6",
            "DEVISE_INCOMPLETE": "Montantdevise / Idevise incohérents",
        }

        # Entêtes du tableau
        col_w = [35, 105, 20, 20]
        pdf.set_fill_color(*COULEUR_ENTETE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*COULEUR_TITRE)
        for header, w in zip(["Code", "Description", "Nb", "Corrigées"], col_w):
            pdf.cell(w, 7, header, border=1, fill=True, align="C")
        pdf.ln()

        for i, (code, nb) in enumerate(sorted(stats_codes.items())):
            nb_corr_code = sum(1 for e in erreurs if e.code == code and e.corrigee)
            if i % 2 == 0:
                pdf.set_fill_color(*COULEUR_LIGNE_PAIRE)
            else:
                pdf.set_fill_color(255, 255, 255)
            pdf.set_text_color(40, 40, 40)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(col_w[0], 6, code, border="LR", fill=True)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(col_w[1], 6, descriptions_codes.get(code, code), border="LR", fill=True)
            pdf.cell(col_w[2], 6, str(nb), border="LR", fill=True, align="C")
            pdf.cell(col_w[3], 6, str(nb_corr_code), border="LR", fill=True, align="C")
            pdf.ln()

        # Ligne de total
        pdf.set_fill_color(*COULEUR_SECTION)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_w[0] + col_w[1], 6, "TOTAL", border=1, fill=True)
        pdf.cell(col_w[2], 6, str(len(erreurs)), border=1, fill=True, align="C")
        pdf.cell(col_w[3], 6, str(nb_corrigees), border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_text_color(40, 40, 40)

    # ── Détail de chaque erreur ──────────────────────────────────────────────
    if erreurs:
        pdf.titre_section("3. Détail des anomalies détectées")

        LARGEUR_UTILE = 190  # mm disponibles entre marges (210 - 2x10)

        for i, erreur in enumerate(erreurs, 1):
            # Estimation hauteur minimale de la carte : saut de page préventif
            if pdf.get_y() > 248:
                pdf.add_page()

            # ── Statut et couleur ────────────────────────────────────────────
            if erreur.corrigee:
                couleur_statut = COULEUR_OK
                statut_txt = "CORRIGEE"
            elif erreur.valeur_corrigee is not None:
                couleur_statut = COULEUR_AVERTISSEMENT
                statut_txt = "REFUSEE"
            else:
                couleur_statut = (120, 120, 120)
                statut_txt = "MANUELLE"

            # ── Ligne 1 : badge numéro | code (large) | badge statut ─────────
            # Largeurs : numéro=14  code=reste  statut=32  → code prend tout l'espace
            W_NUM    = 14
            W_STATUT = 32
            W_CODE   = LARGEUR_UTILE - W_NUM - W_STATUT  # ~144 mm — jamais tronqué

            pdf.set_fill_color(*couleur_statut)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(W_NUM, 6, f"#{i:02d}", fill=True, align="C")

            pdf.set_fill_color(235, 239, 252)
            pdf.set_text_color(*COULEUR_TITRE)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(W_CODE, 6, f"  {erreur.code}", fill=True)

            pdf.set_fill_color(*couleur_statut)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(W_STATUT, 6, statut_txt, fill=True, align="C")
            pdf.ln(6)

            # ── Ligne 2 : localisation (ligne + champ) sur fond très clair ──
            ligne_txt = f"Ligne {erreur.ligne}" if erreur.ligne > 0 else "Global"
            champ_txt = f"Champ : {erreur.champ}"
            meta_txt  = f"  {ligne_txt}   |   {champ_txt}"

            pdf.set_fill_color(245, 246, 250)
            pdf.set_text_color(100, 110, 140)
            pdf.set_font("Helvetica", "I", 7.5)
            pdf.cell(LARGEUR_UTILE, 5, meta_txt, fill=True, border="LR")
            pdf.ln(5)

            # ── Ligne 3 : description ────────────────────────────────────────
            pdf.set_fill_color(250, 251, 255)
            pdf.set_text_color(50, 50, 50)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_x(10)
            pdf.multi_cell(LARGEUR_UTILE, 4.5, f"  {erreur.description}",
                           fill=True, border="LRB")

            # ── Ligne 4 : correction appliquée (si acceptée) ─────────────────
            if erreur.corrigee and erreur.valeur_corrigee is not None:
                pdf.set_x(10)
                pdf.set_fill_color(232, 248, 236)
                pdf.set_text_color(*COULEUR_OK)
                pdf.set_font("Helvetica", "B", 7.5)
                pdf.cell(LARGEUR_UTILE, 5,
                         f"  [OK] Valeur remplacee par : \"{erreur.valeur_corrigee}\"",
                         fill=True, border="LRB", ln=True)

            pdf.ln(3)

    # ── Conclusion ──────────────────────────────────────────────────────────
    pdf.titre_section("4. Conclusion et recommandations")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)

    if not erreurs:
        pdf.multi_cell(0, 5,
            "Le fichier FEC analysé est conforme aux exigences de l'article A. 47 A-1 du "
            "Livre des procédures fiscales. Il peut être transmis à l'administration fiscale "
            "sans modification.")
    else:
        lignes_conclusion = [
            f"L'analyse du fichier {Path(chemin_fec).name} a révélé {len(erreurs)} anomalie(s).",
            "",
            f"• {nb_corrigees} correction(s) ont été appliquées automatiquement.",
            f"• {nb_refusees} correction(s) ont été refusées par l'opérateur.",
            f"• {nb_sans_correction} erreur(s) nécessitent une intervention manuelle.",
            "",
        ]
        if nb_corrigees < len(erreurs):
            lignes_conclusion += [
                "⚠ ATTENTION : Le fichier corrigé exporté contient encore des anomalies.",
                "  Une vérification manuelle complémentaire est requise avant toute",
                "  transmission à l'administration fiscale (DGFiP).",
            ]
        else:
            lignes_conclusion += [
                "✓ Toutes les erreurs corrigibles ont été traitées.",
                "  Le fichier fec_corrige.txt peut être soumis à une vérification finale",
                "  par l'expert-comptable avant transmission à la DGFiP.",
            ]
        pdf.multi_cell(0, 5, "\n".join(lignes_conclusion))

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4,
        "Références légales : Article A. 47 A-1 du Livre des procédures fiscales (LPF) — "
        "BOI-CF-IOR-60-40-20 — Arrêté du 29 juillet 2013 relatif aux informations à fournir "
        "pour les contrôles fiscaux.")

    # ── Sauvegarde ──────────────────────────────────────────────────────────
    pdf.output(chemin_sortie)
    print(f"\n✓ Rapport PDF généré : {chemin_sortie}")


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("Exemple : python validate_fec.py fec_sale.txt")
        sys.exit(1)

    chemin_fec = sys.argv[1]
    if not os.path.exists(chemin_fec):
        print(f"Erreur : fichier introuvable → {chemin_fec}")
        sys.exit(1)

    print(f"\n{'='*70}")
    print(f"  VALIDATEUR FEC — Fichier des Écritures Comptables")
    print(f"{'='*70}")
    print(f"  Fichier : {chemin_fec}")
    print(f"  Date    : {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*70}\n")

    # 1. Chargement
    print("→ Chargement du fichier FEC...")
    df = charger_fec(chemin_fec)
    print(f"  {len(df)} ligne(s) chargée(s), {len(df.columns)} colonne(s).")

    # Vérification des colonnes obligatoires
    manquantes = [c for c in CHAMPS_OBLIGATOIRES if c not in df.columns]
    if manquantes:
        print(f"\n⚠ Colonnes manquantes dans le fichier : {', '.join(manquantes)}")

    # 2. Validation
    print("\n→ Analyse des anomalies en cours...")
    erreurs = valider_fec(df)
    print(f"  {len(erreurs)} anomalie(s) détectée(s).")

    # 3. Mode interactif
    df_corrige = mode_interactif(df, erreurs)

    # 4. Export FEC corrigé
    dossier = os.path.dirname(os.path.abspath(chemin_fec))
    chemin_corrige = os.path.join(dossier, "fec_corrige.txt")
    exporter_fec(df_corrige, chemin_corrige)

    # 5. Rapport PDF
    date_str = datetime.now().strftime("%Y%m%d")
    chemin_pdf = os.path.join(dossier, f"rapport_corrections_{date_str}.pdf")
    print("\n→ Génération du rapport PDF...")
    generer_rapport_pdf(chemin_fec, erreurs, chemin_pdf)

    # 6. Récapitulatif final
    nb_corrigees = sum(1 for e in erreurs if e.corrigee)
    print(f"\n{'='*70}")
    print(f"  RÉCAPITULATIF")
    print(f"{'='*70}")
    print(f"  Erreurs détectées  : {len(erreurs)}")
    print(f"  Corrections        : {nb_corrigees}")
    print(f"  FEC corrigé        : {chemin_corrige}")
    print(f"  Rapport PDF        : {chemin_pdf}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
