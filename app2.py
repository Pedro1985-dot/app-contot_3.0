from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple
import math

Zone = Literal["A", "B", "C", "D", "E", "F"]

# -----------------------------
# Helpers
# -----------------------------
def _round2(x: float) -> float:
    return float(f"{x:.2f}")

def _annualities_by_threshold(value_eur: float, default_n: int) -> int:
    """
    Regola generale: incentivo in unica soluzione se <= 15.000 €,
    altrimenti in n annualità (2 o 5 in base all'intervento).
    """
    return 1 if value_eur <= 15000 else default_n

def _band_index_sl(sl_m2: float) -> int:
    """
    Tabella 16: Sl < 12 ; 12 < Sl < 50 ; 50 < Sl < 200 ; 200 < Sl < 500 ; Sl > 500
    Implementazione pratica a bande contigue (limiti inclusivi superiori).
    """
    if sl_m2 <= 12:
        return 0
    if sl_m2 <= 50:
        return 1
    if sl_m2 <= 200:
        return 2
    if sl_m2 <= 500:
        return 3
    return 4

@dataclass
class Result:
    intervention: str
    annual_incentive_eur: float
    total_incentive_eur: float
    n_rates: int
    annual_rate_eur: float
    details: Dict
    notes: List[str]

# -----------------------------
# POMPE DI CALORE (elettriche) – Tabelle 8 e 9
# -----------------------------
PDC_Quf: Dict[Zone, float] = {  # Tabella 8 (Quf)
    "A": 600, "B": 850, "C": 1100, "D": 1400, "E": 1700, "F": 1800
}

# Tabella 9 (Ci) – chiavi "famiglia" + bande di potenza
# valori in €/kWht
def pdc_ci(pdc_type: str, prated_kw: float) -> float:
    p = prated_kw
    t = pdc_type.lower().strip()

    # aria/aria split/multisplit <= 12
    if t == "aria_aria_split":
        if p > 12:
            raise ValueError("aria_aria_split valido solo per Prated <= 12 kW (Tabella 9).")
        return 0.070

    # fixed double duct (in Tabella 9 nella stessa riga di split/multisplit)
    if t == "fixed_double_duct":
        if p > 12:
            raise ValueError("fixed_double_duct: in Tabella 9 è riportato con split/multisplit <= 12 kW.")
        return 0.200

    # aria/aria VRF/VRV
    if t == "aria_aria_vrf":
        if p <= 35:
            return 0.15
        return 0.055

    # aria/aria rooftop
    if t == "aria_aria_rooftop":
        if p <= 35:
            return 0.15
        return 0.055

    # aria/acqua
    if t == "aria_acqua":
        if p <= 35:
            return 0.15
        return 0.06

    # acqua/aria (acqua di falda / aria)
    if t == "acqua_aria":
        if p <= 35:
            return 0.160
        return 0.06

    # acqua/acqua (acqua di falda / acqua)
    if t == "acqua_acqua":
        if p <= 35:
            return 0.160
        return 0.06

    # salamoia/aria (geotermiche circuito chiuso)
    if t == "salamoia_aria":
        if p <= 35:
            return 0.160
        return 0.06

    # salamoia/acqua (geotermiche circuito chiuso)
    if t == "salamoia_acqua":
        if p <= 35:
            return 0.160
        return 0.06

    raise ValueError(f"Tipo PDC non riconosciuto: {pdc_type}")

def calc_pdc_elettrica(
    zone: Zone,
    pdc_type: str,
    prated_kw: float,
    scop: Optional[float] = None,
    kp: float = 1.0,
    cop35: Optional[float] = None,
) -> Result:
    """
    Ia = Ei * Ci
    Qu = Prated * Quf
    Ei = Qu * (1 - 1/SCOP) * kp   (pompe di calore elettriche)
    Caso specifico fixed_double_duct: Ei = Qu * (1 - 1/COP35) * kp, con kp=2.6 indicato dalle Regole.
    """
    notes: List[str] = []
    q_u = prated_kw * PDC_Quf[zone]
    ci = pdc_ci(pdc_type, prated_kw)

    if pdc_type.lower().strip() == "fixed_double_duct":
        if cop35 is None:
            raise ValueError("fixed_double_duct richiede COP35.")
        # dalle Regole: kp assunto = 2.6 per fixed double duct
        kp_eff = 2.6
        e_i = q_u * (1.0 - 1.0 / cop35) * kp_eff
        notes.append("fixed_double_duct: usato COP35 e kp=2.6 (come da Regole).")
    else:
        if scop is None:
            raise ValueError("Serve SCOP (oppure usa fixed_double_duct con COP35).")
        e_i = q_u * (1.0 - 1.0 / scop) * kp

    ia = e_i * ci

    # annualità: 2 se <=35 kW, altrimenti 5
    default_n = 2 if prated_kw <= 35 else 5
    itot = ia * default_n
    n_rates = _annualities_by_threshold(itot, default_n)
    annual_rate = itot / n_rates

    return Result(
        intervention="PDC elettrica",
        annual_incentive_eur=_round2(ia if n_rates != 1 else itot),  # se rata unica, ha senso mostrare "annuo" = totale
        total_incentive_eur=_round2(itot),
        n_rates=n_rates,
        annual_rate_eur=_round2(annual_rate),
        details={
            "zone": zone,
            "Quf": PDC_Quf[zone],
            "Prated_kW": prated_kw,
            "Qu_kWht": q_u,
            "Ci_eur_per_kWht": ci,
            "Ei_kWht": e_i,
            "kp": kp,
            "SCOP": scop,
            "COP35": cop35,
        },
        notes=notes
    )

# -----------------------------
# SISTEMI IBRIDI / BIVALENTI add-on – Tabella 18
# -----------------------------
def hybrid_k(system_type: str, boiler_pn_kw: float) -> float:
    t = system_type.lower().strip()
    if t == "ibrido_factory_made":
        return 1.25
    if t == "bivalente_addon":
        return 1.0 if boiler_pn_kw <= 35 else 1.1
    raise ValueError("system_type deve essere: ibrido_factory_made | bivalente_addon")

def calc_ibrido(
    zone: Zone,
    system_type: str,
    boiler_pn_kw: float,
    pdc_type: str,
    prated_kw: float,
    scop: Optional[float] = None,
    kp: float = 1.0,
    cop35: Optional[float] = None,
) -> Result:
    base = calc_pdc_elettrica(zone, pdc_type, prated_kw, scop=scop, kp=kp, cop35=cop35)
    k = hybrid_k(system_type, boiler_pn_kw)

    # Ia = k * Ei * Ci  => basta moltiplicare la parte annua
    # annualità dipende dalla potenza nominale totale in riscaldamento delle PDC del sistema (qui prated_kw)
    default_n = 2 if prated_kw <= 35 else 5

    # ricostruisco Ia e Itot coerenti
    ia = (base.details["Ei_kWht"] * base.details["Ci_eur_per_kWht"]) * k
    itot = ia * default_n
    n_rates = _annualities_by_threshold(itot, default_n)
    annual_rate = itot / n_rates

    return Result(
        intervention="Sistema ibrido/bivalente (add-on)",
        annual_incentive_eur=_round2(ia if n_rates != 1 else itot),
        total_incentive_eur=_round2(itot),
        n_rates=n_rates,
        annual_rate_eur=_round2(annual_rate),
        details={
            **base.details,
            "system_type": system_type,
            "boiler_Pn_kW": boiler_pn_kw,
            "k": k,
        },
        notes=base.notes + [f"Applicato k={k} (Tabella 18)."]
    )

# -----------------------------
# BIOMASSA – Tabelle 10, 11, 12, 13
# -----------------------------
BIOMASS_HR: Dict[Zone, float] = {  # Tabella 11
    "A": 600, "B": 850, "C": 1100, "D": 1400, "E": 1700, "F": 1800
}

def biomass_ci(device: str, pn_kw: float) -> float:
    d = device.lower().strip()
    if d == "caldaia":
        if pn_kw <= 35:
            return 0.060
        if pn_kw <= 500:
            return 0.025
        return 0.020
    if d == "stufa_termocamino_legna":
        return 0.045
    if d == "stufa_termocamino_pellet":
        return 0.055
    raise ValueError("device: caldaia | stufa_termocamino_legna | stufa_termocamino_pellet")

def biomass_ce(reduction_pp_percent: float) -> float:
    """
    Tabelle 12/13: Ce in funzione della riduzione % delle emissioni di particolato primario.
    """
    if reduction_pp_percent <= 20:
        return 1.0
    if reduction_pp_percent <= 50:
        return 1.2
    return 1.5

def calc_biomassa(
    zone: Zone,
    device: str,
    pn_kw: float,
    reduction_pp_percent: float,
) -> Result:
    hr = BIOMASS_HR[zone]
    ci = biomass_ci(device, pn_kw)
    ce = biomass_ce(reduction_pp_percent)

    d = device.lower().strip()
    if d == "caldaia":
        ia = pn_kw * hr * ci * ce
    else:
        # stufe e termocamini: Ia = 3,35 * ln(Pn) * hr * Ci * Ce
        ia = 3.35 * math.log(pn_kw) * hr * ci * ce

    default_n = 2 if pn_kw <= 35 else 5
    itot = ia * default_n
    n_rates = _annualities_by_threshold(itot, default_n)
    annual_rate = itot / n_rates

    return Result(
        intervention="Biomassa",
        annual_incentive_eur=_round2(ia if n_rates != 1 else itot),
        total_incentive_eur=_round2(itot),
        n_rates=n_rates,
        annual_rate_eur=_round2(annual_rate),
        details={
            "zone": zone,
            "Pn_kW": pn_kw,
            "hr": hr,
            "Ci_eur_per_kWht": ci,
            "Ce": ce,
            "reduction_PP_percent": reduction_pp_percent,
            "formula": "Pn*hr*Ci*Ce" if d == "caldaia" else "3.35*ln(Pn)*hr*Ci*Ce",
        },
        notes=[]
    )

# -----------------------------
# SOLARE TERMICO – Tabelle 16 e 17
# -----------------------------
SOLAR_CI: Dict[str, List[float]] = {
    "acs": [0.35, 0.32, 0.13, 0.12, 0.11],
    "acs_risc_bassaT_rete": [0.36, 0.33, 0.13, 0.12, 0.11],
    "concentrazione": [0.38, 0.35, 0.13, 0.12, 0.11],
    "solar_cooling": [0.43, 0.40, 0.17, 0.15, 0.14],
}

def calc_solare_termico(
    application: str,
    sl_m2: float,                 # superficie lorda totale campo solare
    modules_n: int,
    module_ag_m2: float,          # area lorda singolo modulo
    collector_kind: Literal["piano_sottovuoto", "factory_made", "concentrazione"],
    q_kwh_per_year_per_module: Optional[float] = None,  # Qcol o Qsol (kWht/anno per modulo)
    ql_mj_per_year_per_module: Optional[float] = None,  # QL (MJ/anno per modulo) per factory made
) -> Result:
    app = application.lower().strip()
    if app not in SOLAR_CI:
        raise ValueError("application: acs | acs_risc_bassaT_rete | concentrazione | solar_cooling")

    # Sl coerente anche se l'utente la passa: possiamo ricalcolarla per controllo
    sl_calc = modules_n * module_ag_m2
    if abs(sl_calc - sl_m2) / max(sl_m2, 1e-9) > 0.05:
        # tolleranza 5%: segnalo solo
        note = f"Nota: Sl fornita ({sl_m2}) differisce da moduli*Ag ({sl_calc:.2f}). Uso Sl fornita."
        notes = [note]
    else:
        notes = []

    band = _band_index_sl(sl_m2)
    ci = SOLAR_CI[app][band]

    # Qu in kWht/m2
    if collector_kind == "factory_made":
        if ql_mj_per_year_per_module is None:
            raise ValueError("factory_made richiede QL (MJ/anno per modulo).")
        qu = (ql_mj_per_year_per_module / 3.6) / module_ag_m2
    else:
        if q_kwh_per_year_per_module is None:
            raise ValueError("piano_sottovuoto/concentrazione richiede Qcol o Qsol (kWht/anno per modulo).")
        qu = q_kwh_per_year_per_module / module_ag_m2

    ia = ci * qu * sl_m2

    default_n = 2 if sl_m2 <= 50 else 5
    itot = ia * default_n
    n_rates = _annualities_by_threshold(itot, default_n)
    annual_rate = itot / n_rates

    return Result(
        intervention="Solare termico",
        annual_incentive_eur=_round2(ia if n_rates != 1 else itot),
        total_incentive_eur=_round2(itot),
        n_rates=n_rates,
        annual_rate_eur=_round2(annual_rate),
        details={
            "application": app,
            "Sl_m2": sl_m2,
            "Ci_eur_per_kWht": ci,
            "band_index": band,
            "collector_kind": collector_kind,
            "modules_n": modules_n,
            "module_Ag_m2": module_ag_m2,
            "Qu_kWht_per_m2": qu,
        },
        notes=notes
    )

# -----------------------------
# FV + ACCUMULO (solo come add-on a PDC) – %spesa, cap costi, vincolo Imax=Itot PDC
# -----------------------------
def pv_cftv_max(p_kw: float) -> float:
    # Cap €/kW in funzione PFTV (valori delle Regole Applicative)
    if p_kw <= 20:
        return 1500
    if p_kw <= 200:
        return 1200
    if p_kw <= 600:
        return 1100
    if p_kw <= 1000:
        return 1050
    raise ValueError("PFTV fuori range (max 1000 kW in tabella).")

def calc_pv_accumulo(
    pdc_total_incentive_eur: float,
    p_pv_kw: float,
    cost_pv_eur: float,
    storage_kwh: float = 0.0,
    cost_storage_eur: float = 0.0,
    bonus_pp: int = 0,   # +5 / +10 / +15 punti percentuali
    public_building_100pct: bool = False,
) -> Result:
    # %spesa
    if public_building_100pct:
        perc = 1.0
        notes = ["Edificio pubblico: percentuale spesa incentivata 100%."]
    else:
        perc = 0.20 + (bonus_pp / 100.0)
        notes = [f"%spesa = 20% + {bonus_pp}pp = {perc*100:.1f}%"]

    # Cap costi specifici
    cftv = min(cost_pv_eur / p_pv_kw, pv_cftv_max(p_pv_kw))
    cacc = 0.0
    if storage_kwh > 0:
        cacc = min(cost_storage_eur / storage_kwh, 1000.0)  # CACC max

    itot_raw = perc * (cftv * p_pv_kw + cacc * storage_kwh)

    # vincolo Imax = Itot PDC
    itot = min(itot_raw, pdc_total_incentive_eur)
    if itot < itot_raw:
        notes.append("Applicato vincolo Imax = Itot generatore (PDC).")

    # rate: segue di fatto la logica del generatore principale; qui non la deduco automaticamente
    # (se vuoi, la aggancio al risultato PDC che calcoli prima).
    n_rates = _annualities_by_threshold(itot, 2)  # fallback: 2
    annual_rate = itot / n_rates

    return Result(
        intervention="FV + accumulo (add-on)",
        annual_incentive_eur=_round2(annual_rate if n_rates != 1 else itot),
        total_incentive_eur=_round2(itot),
        n_rates=n_rates,
        annual_rate_eur=_round2(annual_rate),
        details={
            "P_pv_kW": p_pv_kw,
            "CFTV_eur_per_kW_used": cftv,
            "storage_kWh": storage_kwh,
            "CACC_eur_per_kWh_used": cacc,
            "perc": perc,
            "itot_raw": itot_raw,
            "Imax_pdc": pdc_total_incentive_eur,
        },
        notes=notes
    )

# -----------------------------
# EV CHARGING privata (add-on a PDC) – cap costi + vincolo Imax = Itot PDC
# -----------------------------
def ev_cost_max(category: str, power_kw: Optional[float] = None) -> float:
    """
    Categoria:
    - A_mono (7.4 < P <= 22): 2.400 €/punto
    - A_tri  (7.4 < P <= 22): 8.400 €/punto
    - B_22_50 (22 < P <= 50): 1.200 €/kW
    - B_50_100 (50 < P <= 100): 60.000 €/infrastruttura
    - B_gt100 (P > 100): 110.000 €/infrastruttura
    """
    c = category.lower().strip()
    if c == "a_mono":
        return 2400.0
    if c == "a_tri":
        return 8400.0
    if c == "b_22_50":
        if power_kw is None:
            raise ValueError("b_22_50 richiede power_kw.")
        return 1200.0 * power_kw
    if c == "b_50_100":
        return 60000.0
    if c == "b_gt100":
        return 110000.0
    raise ValueError("Categoria non valida.")

def calc_ev(
    pdc_total_incentive_eur: float,
    eligible_cost_eur: float,
    category: str,
    power_kw: Optional[float] = None
) -> Result:
    cmax = ev_cost_max(category, power_kw=power_kw)
    cost_used = min(eligible_cost_eur, cmax)

    itot_raw = 0.30 * cost_used
    itot = min(itot_raw, pdc_total_incentive_eur)

    notes = [f"Costo usato = min(C, Cmax) = {cost_used:.2f} €; incentivo = 30% del costo usato."]
    if itot < itot_raw:
        notes.append("Applicato vincolo Imax = Itot generatore (PDC).")

    n_rates = _annualities_by_threshold(itot, 2)  # fallback
    annual_rate = itot / n_rates

    return Result(
        intervention="EV charging privata (add-on)",
        annual_incentive_eur=_round2(annual_rate if n_rates != 1 else itot),
        total_incentive_eur=_round2(itot),
        n_rates=n_rates,
        annual_rate_eur=_round2(annual_rate),
        details={
            "eligible_cost_eur": eligible_cost_eur,
            "Cmax_eur": cmax,
            "cost_used_eur": cost_used,
            "itot_raw": itot_raw,
            "Imax_pdc": pdc_total_incentive_eur,
            "category": category,
            "power_kw": power_kw,
        },
        notes=notes
    )

# -----------------------------
# ESEMPIO RAPIDO
# -----------------------------
if __name__ == "__main__":
    # Esempio: PDC aria/acqua 10 kW in zona D con SCOP 4.0, kp=1
    pdc = calc_pdc_elettrica(zone="D", pdc_type="aria_acqua", prated_kw=10, scop=4.0, kp=1.0)
    print(pdc)

    # Ibrido factory made (caldaia 24 kW), stessa PDC
    hyb = calc_ibrido(zone="D", system_type="ibrido_factory_made", boiler_pn_kw=24,
                      pdc_type="aria_acqua", prated_kw=10, scop=4.0, kp=1.0)
    print(hyb)

    # Biomassa: caldaia 30 kW in zona E, riduzione PP 30%
    bio = calc_biomassa(zone="E", device="caldaia", pn_kw=30, reduction_pp_percent=30)
    print(bio)

    # Solare termico ACS: 20 m2, 10 moduli da 2 m2, Qcol 1200 kWht/anno per modulo
    sol = calc_solare_termico(application="acs", sl_m2=20, modules_n=10, module_ag_m2=2.0,
                              collector_kind="piano_sottovuoto", q_kwh_per_year_per_module=1200)
    print(sol)

    # FV add-on: 6 kW, costo 9.000 €, accumulo 10 kWh costo 6.000 €, bonus 10pp
    pv = calc_pv_accumulo(pdc_total_incentive_eur=pdc.total_incentive_eur,
                          p_pv_kw=6, cost_pv_eur=9000,
                          storage_kwh=10, cost_storage_eur=6000,
                          bonus_pp=10, public_building_100pct=False)
    print(pv)

    # EV: wallbox trifase (A_tri) costo 3.000 €
    ev = calc_ev(pdc_total_incentive_eur=pdc.total_incentive_eur,
                 eligible_cost_eur=3000, category="A_tri")
    print(ev)
