from __future__ import annotations

import streamlit as st

st.title("App attiva")
st.write("Se leggi questo, Streamlit funziona")

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

# =============================
# STREAMLIT UI (PROFESSIONALE)
# =============================

import json
from dataclasses import asdict

st.set_page_config(
    page_title="Calcolatore Conto Termico 3.0",
    page_icon="🧮",
    layout="wide",
)

def _eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

def _safe_run(fn):
    """Esegue fn() e gestisce errori mostrando messaggio UI pulito."""
    try:
        return fn(), None
    except Exception as e:
        return None, str(e)

def _result_block(res: "Result"):
    # KPI in alto
    c1, c2, c3 = st.columns(3)
    c1.metric("Incentivo totale", _eur(res.total_incentive_eur))
    c2.metric("Numero rate", str(res.n_rates))
    c3.metric("Importo rata", _eur(res.annual_rate_eur))

    # Note
    if res.notes:
        st.info("**Note:**\n\n" + "\n".join([f"- {n}" for n in res.notes]))

    # Dettagli (collassabile)
    with st.expander("Vedi dettagli di calcolo"):
        st.json(res.details)

    # Download JSON del risultato
    payload = asdict(res)
    st.download_button(
        "⬇️ Scarica risultato (JSON)",
        data=json.dumps(payload, indent=2, ensure_ascii=False),
        file_name="risultato_conto_termico.json",
        mime="application/json",
        use_container_width=True,
    )

# -----------------------------
# Header
# -----------------------------
st.title("Calcolatore Conto Termico 3.0")
st.caption("Versione Streamlit — prototipo professionale (input guidati + risultati + export).")

# -----------------------------
# Sidebar: impostazioni generali
# -----------------------------
with st.sidebar:
    st.header("Impostazioni")
    zone = st.selectbox("Zona climatica", ["A","B","C","D","E","F"], index=3)

    st.divider()
    st.subheader("Output")
    show_details_default = st.checkbox("Mostra dettagli automaticamente", value=False)
    st.caption("Suggerimento: lascia OFF per un output pulito e veloce.")

# -----------------------------
# Tabs
# -----------------------------
tab_pdc, tab_ibrido, tab_biomassa, tab_solare, tab_pv, tab_ev = st.tabs(
    ["PDC elettrica", "Ibrido/Bivalente", "Biomassa", "Solare termico", "FV + Accumulo", "EV Charging"]
)

# =============================
# TAB 1 — PDC
# =============================
with tab_pdc:
    st.subheader("Pompa di Calore Elettrica")

    colA, colB, colC = st.columns([1.2, 1, 1])
    with colA:
        pdc_type = st.selectbox(
            "Tipo PDC (Tabella 9)",
            [
                "aria_acqua",
                "aria_aria_split",
                "aria_aria_vrf",
                "aria_aria_rooftop",
                "acqua_aria",
                "acqua_acqua",
                "salamoia_aria",
                "salamoia_acqua",
                "fixed_double_duct",
            ],
            index=0,
        )
        prated_kw = st.number_input("Prated (kW)", min_value=0.1, value=10.0, step=0.1)

    with colB:
        kp = st.number_input("kp", min_value=0.1, value=1.0, step=0.1)

    with colC:
        scop = None
        cop35 = None
        if pdc_type == "fixed_double_duct":
            cop35 = st.number_input("COP35 (solo fixed_double_duct)", min_value=1.1, value=3.2, step=0.1)
            st.caption("Per fixed_double_duct il codice usa kp=2.6 come da Regole.")
        else:
            scop = st.number_input("SCOP", min_value=1.1, value=4.0, step=0.1)

    run = st.button("Calcola PDC", type="primary", use_container_width=True)

    if run:
        res, err = _safe_run(lambda: calc_pdc_elettrica(
            zone=zone,
            pdc_type=pdc_type,
            prated_kw=prated_kw,
            scop=scop,
            kp=kp,
            cop35=cop35
        ))
        if err:
            st.error(err)
        else:
            st.success("Calcolo completato.")
            _result_block(res)
            if show_details_default:
                st.json(res.details)

# =============================
# TAB 2 — IBRIDO/BIVALENTE
# =============================
with tab_ibrido:
    st.subheader("Sistema ibrido / bivalente (add-on)")

    colA, colB = st.columns([1.2, 1])
    with colA:
        system_type = st.selectbox("Tipologia", ["ibrido_factory_made", "bivalente_addon"], index=0)
        boiler_pn_kw = st.number_input("Pn caldaia (kW)", min_value=0.1, value=24.0, step=0.1)
        pdc_type = st.selectbox(
            "Tipo PDC",
            [
                "aria_acqua",
                "aria_aria_split",
                "aria_aria_vrf",
                "aria_aria_rooftop",
                "acqua_aria",
                "acqua_acqua",
                "salamoia_aria",
                "salamoia_acqua",
                "fixed_double_duct",
            ],
            index=0,
            key="hyb_pdc_type",
        )
        prated_kw = st.number_input("Prated PDC (kW)", min_value=0.1, value=10.0, step=0.1, key="hyb_prated")

    with colB:
        kp = st.number_input("kp", min_value=0.1, value=1.0, step=0.1, key="hyb_kp")
        scop = None
        cop35 = None
        if pdc_type == "fixed_double_duct":
            cop35 = st.number_input("COP35", min_value=1.1, value=3.2, step=0.1, key="hyb_cop35")
        else:
            scop = st.number_input("SCOP", min_value=1.1, value=4.0, step=0.1, key="hyb_scop")

    run = st.button("Calcola Ibrido/Bivalente", type="primary", use_container_width=True)

    if run:
        res, err = _safe_run(lambda: calc_ibrido(
            zone=zone,
            system_type=system_type,
            boiler_pn_kw=boiler_pn_kw,
            pdc_type=pdc_type,
            prated_kw=prated_kw,
            scop=scop,
            kp=kp,
            cop35=cop35
        ))
        if err:
            st.error(err)
        else:
            st.success("Calcolo completato.")
            _result_block(res)

# =============================
# TAB 3 — BIOMASSA
# =============================
with tab_biomassa:
    st.subheader("Generatori a Biomassa")

    colA, colB, colC = st.columns([1.2, 1, 1])
    with colA:
        device = st.selectbox(
            "Tipo generatore",
            ["caldaia", "stufa_termocamino_legna", "stufa_termocamino_pellet"],
            index=0
        )
    with colB:
        pn_kw = st.number_input("Pn (kW)", min_value=0.1, value=30.0, step=0.1)
    with colC:
        red_pp = st.number_input("Riduzione PP (%)", min_value=0.0, value=30.0, step=1.0)

    run = st.button("Calcola Biomassa", type="primary", use_container_width=True)

    if run:
        res, err = _safe_run(lambda: calc_biomassa(
            zone=zone,
            device=device,
            pn_kw=pn_kw,
            reduction_pp_percent=red_pp
        ))
        if err:
            st.error(err)
        else:
            st.success("Calcolo completato.")
            _result_block(res)

# =============================
# TAB 4 — SOLARE TERMICO
# =============================
with tab_solare:
    st.subheader("Solare Termico")

    colA, colB, colC = st.columns([1.2, 1, 1])
    with colA:
        application = st.selectbox(
            "Applicazione",
            ["acs", "acs_risc_bassaT_rete", "concentrazione", "solar_cooling"],
            index=0
        )
        collector_kind = st.selectbox(
            "Tipologia collettore",
            ["piano_sottovuoto", "factory_made", "concentrazione"],
            index=0
        )

    with colB:
        modules_n = st.number_input("Numero moduli", min_value=1, value=10, step=1)
        module_ag = st.number_input("Ag modulo (m²)", min_value=0.1, value=2.0, step=0.1)

    with colC:
        sl = st.number_input("Sl totale (m²)", min_value=0.1, value=float(modules_n) * float(module_ag), step=0.1)

    st.caption("Se Sl non coincide con moduli×Ag, l’app lo segnala nelle note (tolleranza 5%).")

    q_kwh = None
    ql_mj = None
    if collector_kind == "factory_made":
        ql_mj = st.number_input("QL (MJ/anno per modulo)", min_value=0.1, value=1325.0, step=1.0)
    else:
        q_kwh = st.number_input("Qcol/Qsol (kWht/anno per modulo)", min_value=0.1, value=1200.0, step=1.0)

    run = st.button("Calcola Solare Termico", type="primary", use_container_width=True)

    if run:
        res, err = _safe_run(lambda: calc_solare_termico(
            application=application,
            sl_m2=sl,
            modules_n=modules_n,
            module_ag_m2=module_ag,
            collector_kind=collector_kind,
            q_kwh_per_year_per_module=q_kwh,
            ql_mj_per_year_per_module=ql_mj
        ))
        if err:
            st.error(err)
        else:
            st.success("Calcolo completato.")
            _result_block(res)

# =============================
# TAB 5 — FV + ACCUMULO (ADD-ON A PDC)
# =============================
with tab_pv:
    st.subheader("Fotovoltaico + Accumulo (add-on a PDC)")
    st.caption("Serve l’Itot del generatore principale (PDC) perché c’è il vincolo Imax = Itot PDC.")

    colA, colB, colC = st.columns([1.2, 1, 1])
    with colA:
        pdc_itot = st.number_input("Itot PDC (€) — limite massimo", min_value=0.0, value=0.0, step=10.0)
        public_100 = st.checkbox("Edificio pubblico (100% spesa)", value=False)
    with colB:
        p_pv = st.number_input("Potenza FV (kW)", min_value=0.1, value=6.0, step=0.1)
        cost_pv = st.number_input("Costo FV (€)", min_value=0.0, value=9000.0, step=50.0)
    with colC:
        storage_kwh = st.number_input("Accumulo (kWh)", min_value=0.0, value=10.0, step=0.5)
        cost_storage = st.number_input("Costo accumulo (€)", min_value=0.0, value=6000.0, step=50.0)

    bonus_pp = st.selectbox("Bonus percentuale (punti %)", [0, 5, 10, 15], index=0)

    run = st.button("Calcola FV + Accumulo", type="primary", use_container_width=True)

    if run:
        res, err = _safe_run(lambda: calc_pv_accumulo(
            pdc_total_incentive_eur=pdc_itot,
            p_pv_kw=p_pv,
            cost_pv_eur=cost_pv,
            storage_kwh=storage_kwh,
            cost_storage_eur=cost_storage,
            bonus_pp=int(bonus_pp),
            public_building_100pct=public_100
        ))
        if err:
            st.error(err)
        else:
            st.success("Calcolo completato.")
            _result_block(res)

# =============================
# TAB 6 — EV CHARGING (ADD-ON A PDC)
# =============================
with tab_ev:
    st.subheader("Ricarica veicoli elettrici (add-on a PDC)")
    st.caption("Serve l’Itot del generatore principale (PDC) perché c’è il vincolo Imax = Itot PDC.")

    colA, colB = st.columns([1.2, 1])
    with colA:
        pdc_itot = st.number_input("Itot PDC (€) — limite massimo", min_value=0.0, value=0.0, step=10.0, key="ev_itot")
        eligible_cost = st.number_input("Costo ammissibile C (€)", min_value=0.0, value=3000.0, step=50.0)
    with colB:
        category = st.selectbox("Categoria", ["a_mono", "a_tri", "b_22_50", "b_50_100", "b_gt100"], index=1)
        power_kw = None
        if category == "b_22_50":
            power_kw = st.number_input("Potenza infrastruttura (kW)", min_value=22.1, value=30.0, step=0.1)

    run = st.button("Calcola EV Charging", type="primary", use_container_width=True)

    if run:
        res, err = _safe_run(lambda: calc_ev(
            pdc_total_incentive_eur=pdc_itot,
            eligible_cost_eur=eligible_cost,
            category=category,
            power_kw=power_kw
        ))
        if err:
            st.error(err)
        else:
            st.success("Calcolo completato.")
            _result_block(res)
