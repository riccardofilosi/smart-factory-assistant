"""score_oper.py — score di una run operativa contro il ground truth dell'operatore.

Uso:  python src/radar/score_oper.py <runid> [--no-xlsx]

Legge data/registri/<id>_registro.json + <id>_gt.json (se il GT manca lo chiede a
terminale e lo salva). Classifica ogni step:

  err_montaggio  = fori reali != fori attesi dal golden, oppure colore/polarità
                   sbagliati (errore dell'operatore). La polarità è una bandiera a
                   sé: con i fori giusti e il pezzo girato le altre due restano
                   false e il KO POLARITA verrebbe contato come falso allarme.
  segnalato      = verdetto sistema non "OK"/"OK FORTE" (allarme di qualunque tipo)

  esito:  VERO ALLARME (err+segnalato)   MISS (err+pulito)  <- il caso grave
          FALSO ALLARME (ok+segnalato)   VERO OK (ok+pulito)

Precisione del radar misurata contro il reale (non contro l'atteso).
Append/replace nel registro cumulativo data/registri/registro_errori.csv e
rigenera data/registri/andamento_errori.xlsx (pivot per commessa/classe/run)."""
import argparse
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATI = os.path.join(HERE, "..", "..", "data", "registri")
CSV = os.path.join(DATI, "registro_errori.csv")
XLSX = os.path.join(DATI, "andamento_errori.xlsx")
PULITI = {"OK", "OK FORTE"}
NON_VALUTABILI = {"NON VERIFICABILE", "FUORI GOLDEN"}


def parse_hole(h):
    """'g13' -> (riga, colonna) di griglia, None se rail/non parsabile."""
    m = re.fullmatch(r"([a-j])(\d+)", str(h).strip().lower())
    return (ord(m.group(1)) - ord("a"), int(m.group(2))) if m else None


def scost_max(reale, atteso):
    """Scostamento massimo (Chebyshev di griglia) di ogni foro reale dal più
    vicino foro atteso. 1 = off-by-one classico. None se non calcolabile."""
    ru = [parse_hole(h) for h in reale]
    au = [parse_hole(h) for h in atteso]
    ru, au = [u for u in ru if u], [u for u in au if u]
    if not ru or not au:
        return None
    return max(min(max(abs(r[0] - a[0]), abs(r[1] - a[1])) for a in au) for r in ru)


def foro_valido(h):
    """Foro di griglia (g13) o rail (+b/-t): True se il token è plausibile.
    Blocca input come 's'/'n' (risposte sì/no salvate come fori)."""
    return bool(parse_hole(h)) or bool(re.fullmatch(r"[+-]([bt]\d{0,2})?", str(h).strip().lower()))


def chiedi_gt(reg, gt_path):
    """GT interattivo (backfill di run senza _gt.json). Solo fori, non sì/no."""
    print(f"GT mancante: {gt_path}\nInserisci i FORI REALI (es. g7,g13 | invio=atteso | -=non montato).")
    steps = []
    for s in reg["steps"]:
        if s.get("step") is None or not s["atteso"].get("coords"):
            continue
        att = s["atteso"]["coords"]
        while True:
            r = input(f"  step {s['step']} {s['atteso']['el']} - atteso {','.join(att)} - reale? "
                      ).strip().lower().replace(" ", "")
            reale = att if r == "" else ([] if r == "-" else r.split(","))
            bad = [h for h in reale if not foro_valido(h)]
            if not bad:
                break
            print(f"    '{','.join(bad)}' non sono fori (formato: g13, +b). Riprova.")
        steps.append({"step": s["step"], "k": s["k"], "atteso": att, "reale": reale,
                      "errore_montaggio": sorted(reale) != sorted(att)})
    gt = {"commessa": reg["commessa"], "steps": steps}
    json.dump(gt, open(gt_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"GT salvato: {gt_path}")
    return gt


def _golden_build(cid):
    """Step della commessa dal golden (per contare quelli mai visti dal radar)."""
    # src/probe non è nel path quando score_oper è lanciato da solo.
    if os.path.join(HERE, "..", "probe") not in sys.path:
        sys.path.insert(0, os.path.join(HERE, "..", "probe"))
    import differential as _df
    p = _df._cerca_golden() or ""
    for c in json.load(open(p, encoding="utf-8")):
        if c["id"] == cid:
            return c["build"]
    return []


def score_run(runid):
    """Righe (list of dict) di una run: registro + GT -> classificazione per step."""
    reg_path = os.path.join(DATI, f"{runid}_registro.json")
    gt_path = os.path.join(DATI, f"{runid}_gt.json")
    if not os.path.exists(reg_path):
        sys.exit(f"registro non trovato: {reg_path}")
    reg = json.load(open(reg_path, encoding="utf-8"))
    gt = (json.load(open(gt_path, encoding="utf-8")) if os.path.exists(gt_path)
          else chiedi_gt(reg, gt_path))
    gt_by_step = {s["step"]: s for s in gt["steps"]}
    m = re.search(r"(\d{4})(\d{2})(\d{2})-\d{6}", runid)
    data = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""

    rows = []
    for s in reg["steps"]:
        if s.get("step") is None:
            continue
        g = gt_by_step.get(s["step"])
        verdetto = s["verdetto"]
        atteso = s["atteso"].get("coords", [])
        # gt_illeggibile: l'operatore ha visto il pezzo ma non è riuscito a dire in
        # quale foro finisse il piede. Il ground truth su quello step non esiste:
        # contarlo come errore fabbrica MISS, contarlo come corretto regala VERO OK.
        # Esce dalle metriche.
        if (g is None or not atteso or verdetto in NON_VALUTABILI
                or g.get("gt_illeggibile")):
            esito, err, reale = "NV", None, (g["reale"] if g else [])
        else:
            reale = g["reale"]
            err = (g["errore_montaggio"] or g.get("errore_colore", False)
                   or g.get("errore_polarita", False))
            segnalato = verdetto not in PULITI
            esito = (("VERO ALLARME" if segnalato else "MISS") if err
                     else ("FALSO ALLARME" if segnalato else "VERO OK"))
        radar = s.get("radar", {}).get("pins", [])
        radar_grid = [h for h in radar if parse_hole(h)]
        reale_grid = [h for h in reale if parse_hole(h)]
        rows.append({
            "run": runid, "data": data, "commessa": reg["commessa"],
            "step": s["step"], "k": s["k"], "classe": s["atteso"]["cls"],
            "el": s["atteso"]["el"],
            "atteso": ",".join(atteso), "reale": ",".join(reale),
            "err_montaggio": err, "verdetto": verdetto,
            "concordanza": s.get("radar", {}).get("concordanza", "-"),
            "esito": esito,
            "gate_miss": bool(s.get("gate_miss", False)),
            "radar_pins": ",".join(radar) or "-",
            "radar_ok": len(set(radar_grid) & set(reale_grid)),
            "radar_tot": len(reale_grid),
            "scost_max": scost_max(reale, atteso) if err else 0,
        })
    # Step golden mai arrivati al radar (es. gate sordo su un jumper corto: lo step
    # non viene mai analizzato). Errore del sistema, va contato.
    visti = {r["step"] for r in rows}
    for idx, stb in enumerate(_golden_build(reg["commessa"]), 1):
        if idx in visti:
            continue
        rows.append({
            "run": runid, "data": data, "commessa": reg["commessa"],
            "step": idx, "k": None, "classe": stb.get("kind", "?"),
            "el": stb.get("el", "?"),
            "atteso": ",".join(stb.get("coords", [])), "reale": "",
            "err_montaggio": False, "verdetto": "-", "concordanza": "-",
            "esito": "NON RILEVATO", "gate_miss": True, "radar_pins": "-",
            "radar_ok": 0, "radar_tot": 0, "scost_max": None,
        })
    rows.sort(key=lambda r: r["step"])
    return rows


def aggiorna_registro(rows):
    """Append/replace nel CSV cumulativo (re-score di una run = sostituisce)."""
    df_new = pd.DataFrame(rows)
    if os.path.exists(CSV):
        df = pd.read_csv(CSV)
        df = df[df["run"] != rows[0]["run"]]
        df = pd.concat([df, df_new], ignore_index=True)
    else:
        df = df_new
    df = df.sort_values(["data", "run", "step"]).reset_index(drop=True)
    df.to_csv(CSV, index=False)
    return df


def genera_xlsx(df):
    """andamento_errori.xlsx: dati + pivot per commessa / classe / run."""
    d = df[df["esito"] != "NV"].copy()

    def pivot(by):
        g = d.groupby(by)
        out = pd.DataFrame({
            "step": g.size(),
            "errori_montaggio": g["err_montaggio"].sum(),
            "NON_RILEVATO": g.apply(lambda x: (x["esito"] == "NON RILEVATO").sum(), include_groups=False),
            "GATE_MISS": g.apply(lambda x: x["gate_miss"].fillna(False).astype(bool).sum(),
                                 include_groups=False),
            "MISS": g.apply(lambda x: (x["esito"] == "MISS").sum(), include_groups=False),
            "VERO_ALLARME": g.apply(lambda x: (x["esito"] == "VERO ALLARME").sum(), include_groups=False),
            "FALSO_ALLARME": g.apply(lambda x: (x["esito"] == "FALSO ALLARME").sum(), include_groups=False),
            "VERO_OK": g.apply(lambda x: (x["esito"] == "VERO OK").sum(), include_groups=False),
            "radar_pin_ok": g["radar_ok"].sum(),
            "radar_pin_tot": g["radar_tot"].sum(),
        })
        out["miss_rate"] = (out["MISS"] / out["errori_montaggio"]
                            .where(out["errori_montaggio"] > 0)).round(2)
        out["radar_pin_pct"] = (out["radar_pin_ok"] / out["radar_pin_tot"]
                                .where(out["radar_pin_tot"] > 0)).round(2)
        return out.reset_index()

    with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="dati", index=False)
        pivot("commessa").to_excel(w, sheet_name="per_commessa", index=False)
        pivot("classe").to_excel(w, sheet_name="per_classe", index=False)
        pivot("run").to_excel(w, sheet_name="per_run", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runid", help="es. F1.2")
    ap.add_argument("--no-xlsx", action="store_true")
    ap.add_argument("--wizard", action="store_true",
                    help="(ri)fai il GT col wizard grafico sui pannelli radar della run")
    a = ap.parse_args()
    if a.wizard:
        sys.path.insert(0, HERE)
        import radar_ui as ui
        reg = json.load(open(os.path.join(DATI, f"{a.runid}_registro.json"), encoding="utf-8"))
        rundir = os.path.join(HERE, "..", "..", "data", "runs", a.runid)
        gt = ui.gt_wizard(reg["steps"], os.path.abspath(rundir), reg["commessa"])
        if gt is None:
            sys.exit("wizard chiuso senza salvare: GT invariato.")
        ui.salva_gt(gt, os.path.join(DATI, f"{a.runid}_registro.json"))
    rows = score_run(a.runid)
    df = aggiorna_registro(rows)
    if not a.no_xlsx:
        try:
            genera_xlsx(df)
        except PermissionError:
            print("xlsx NON aggiornato: chiudi andamento_errori.xlsx (Excel/VS Code) e rilancia."
                  " Il csv e' comunque aggiornato.")

    # riepilogo terminale (ASCII, console cp1252)
    val = [r for r in rows if r["esito"] != "NV"]
    nerr = sum(1 for r in val if r["err_montaggio"])
    miss = [r for r in val if r["esito"] == "MISS"]
    print(f"\nrun {a.runid}: {len(val)} step valutati, {nerr} errori montaggio")
    for r in val:
        seg = f"  step {r['step']} {r['classe']:<12} atteso {r['atteso']:<14} reale {r['reale']:<14} -> {r['esito']}"
        if r["esito"] == "MISS":
            seg += f"  [verdetto era: {r['verdetto']}, scost {r['scost_max']}]"
        print(seg)
    print(f"MISS: {len(miss)}/{nerr} errori non rilevati" if nerr else "MISS: nessun errore da rilevare")
    nr = [r for r in rows if r["esito"] == "NON RILEVATO"]
    if nr:
        print(f"NON RILEVATI (mai analizzati dal radar): "
              + ", ".join(f"step {r['step']} {r['el']}" for r in nr))
    gm = [r for r in rows if r.get("gate_miss") and r["esito"] != "NON RILEVATO"]
    if gm:
        print("GATE MISS salvati con F (dati buoni, gate fallito): "
              + ", ".join(f"step {r['step']}" for r in gm))
    tot = pd.read_csv(CSV)
    totv = tot[tot["esito"] != "NV"]
    print(f"cumulativo: {len(totv)} step, {int(totv['err_montaggio'].sum())} errori, "
          f"{(totv['esito'] == 'MISS').sum()} MISS  -> {CSV}")
    if not a.no_xlsx:
        print(f"pivot: {XLSX}")


if __name__ == "__main__":
    main()
