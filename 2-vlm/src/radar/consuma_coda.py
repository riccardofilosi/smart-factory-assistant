"""consuma_coda.py — consumatore della coda di scatti (cattura differita).

La coda è la cartella di una run (0.jpeg = board vuota, poi 1.jpeg, 2.jpeg, ...
salvati a intervallo fisso dal produttore in sola cattura). Questo processo li
elabora in ordine con la pipeline operativa, scartando gli scatti inutilizzabili
prima del VLM, e stampa gli avvisi: quale componente è sbagliato e dove.

Il diff è calcolato contro l'ultimo frame accettato (non il precedente in coda,
spesso con la mano nel campo) via analyze(prev_k=...). Uno scatto scartato non
aggiorna il riferimento.

Uso:
  # cartella già completa (batch / test):
  python src/radar/consuma_coda.py data/runs/<run> --commessa F1 --forcecls
  # concorrente all'assemblaggio (poll dei nuovi frame, fine su _fine.flag):
  python src/radar/consuma_coda.py <cartella> --commessa F1 --concorrente
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src", "probe"))
sys.path.insert(0, HERE)
import radar_analyze as ra          # noqa: E402
import radar_operativa as ro        # noqa: E402
import differential as df           # noqa: E402
import telemetria as tm             # noqa: E402

KO = {"KO CLASSE", "KO COLORE", "KO POSIZIONE", "KO POLARITA",
      "OK?", "DEBOLE"}                                    # verdetti da AVVISARE


def _asc(s):
    return str(s).replace("→", "->").replace("Ω", "Ohm").replace("·", ".").encode(
        "ascii", "replace").decode()


def _prossimo(rundir, k, concorrente, poll, timeout):
    """Ritorna il path di {k}.jpeg quando c'è; None se la coda è finita.
    In modo concorrente aspetta il file finché non compare o arriva _fine.flag."""
    p = os.path.join(rundir, f"{k}.jpeg")
    if os.path.exists(p):
        return p
    if not concorrente:
        return None
    t0 = time.time()
    while not os.path.exists(p):
        if os.path.exists(os.path.join(rundir, "_fine.flag")):
            return p if os.path.exists(p) else None
        if time.time() - t0 > timeout:
            print(f"[timeout: {k}.jpeg non arrivato in {timeout}s, chiudo]")
            return None
        time.sleep(poll)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundir")
    ap.add_argument("--commessa", required=True)
    ap.add_argument("--concorrente", action="store_true", help="poll dei nuovi frame + _fine.flag")
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--novlm", action="store_true", help="niente VLM (test headless)")
    ap.add_argument("--forcecls", action="store_true", help="classe = attesa dal golden (test/regressione)")
    ap.add_argument("--out", default=None, help="dove salvare il registro (default <rundir>/_registro_coda.json)")
    ap.add_argument("--telemetria", action="store_true",
                    help="artefatti per valutazione + RUN_<id>.xlsx (come il live)")
    a = ap.parse_args()
    rundir = os.path.abspath(a.rundir)
    if a.out:                      # il loop gira in chdir(rundir): un --out relativo
        a.out = os.path.abspath(a.out)   # finirebbe DENTRO la run (o esploderebbe)
    if not os.path.exists(os.path.join(rundir, "0.jpeg")):
        sys.exit(f"manca 0.jpeg in {rundir}")

    # Telemetria opt-in: il consumatore nasce muto, ma per rigiudicare una run
    # d'archivio serve anche il foglio. Costruita prima del chdir: vuole il path
    # assoluto della run.
    tel = (tm.Telemetria(rundir, cid=a.commessa, runid=os.path.basename(rundir))
           if a.telemetria else None)

    old = os.getcwd(); os.chdir(rundir)
    alerts = []
    try:
        oper = ro.Operativa(a.commessa)
        sess = ra.Session()
        tot = len(oper.build)
        last_committed = 0                       # board vuota = stato accettato iniziale
        ko_di_fila = 0                           # scatti inutilizzabili consecutivi
        k = 1
        print(f"[consumatore] commessa {a.commessa}, {tot} step attesi. "
              f"{'concorrente' if a.concorrente else 'batch'}.")
        while True:
            fp = _prossimo(rundir, k, a.concorrente, a.poll, a.timeout)
            if fp is None:
                break
            st = oper.step_atteso()
            if st is None:
                print(f"[k{k}] FUORI GOLDEN (tutti gli step gia' visti)")
                break
            fc = df.KIND2CLS.get(st["kind"], st["kind"]) if a.forcecls else None
            try:
                res = sess.analyze(k, novlm=a.novlm, force_cls=fc, prev_k=last_committed)
            except SystemExit as e:
                print(f"[k{k}] mappa/frame ko: {e}"); k += 1; continue
            except Exception as e:
                print(f"[k{k}] errore analyze: {str(e)[:60]}"); k += 1; continue
            # Guardie a monte: un frame che non le supera non si analizza e, soprattutto,
            # non diventa il riferimento dei confronti successivi.
            if res.get("frame_ko"):
                ko_di_fila += 1
                print(f"[k{k}] SCARTATO - {res['note']}")
                if ko_di_fila >= 3:
                    print(f"      !! FERMATI: {ko_di_fila} scatti di fila inutilizzabili. "
                          f"Controlla inquadratura e messa a fuoco, poi riprendi.")
                k += 1
                continue
            ko_di_fila = 0
            # Nessun componente nuovo -> non si accetta il frame, non si consuma lo step.
            # Una correzione attesa però non è scarto: assestamento_sospetto risponde
            # True anche quando l'operatore rimette a posto uno step già bocciato (il
            # pezzo si muove sui fori già occupati, la firma dell'assestamento). La
            # seconda domanda, come in radar_live e nei replay, tiene quei frame.
            if res["blob_box"] is None or (oper.assestamento_sospetto(res)
                                           and oper.correzione_in_attesa(res) is None):
                print(f"[k{k}] scartato (nessun componente nuovo / scena mossa)")
                k += 1
                continue
            rec = oper.valuta(res, fonte="coda")
            v = rec.get("verdetto", "?")
            corr = rec.get("correzione_di")
            step = rec.get("step")
            el = rec.get("atteso", {}).get("el", "?")
            tag = f"CORREZIONE step {corr}" if corr else f"step {step}/{tot}"
            line = f"[k{k}] {tag}: {_asc(el)} -> {v}"
            print(line)
            if tel is not None:
                ordine = len(oper.storico)
                voce = oper.storico[-1] if oper.storico else {}
                try:
                    tel.valutazione(res, rec, ordine, tipo=voce.get("tipo", "nuovo"))
                except Exception as e:
                    print(f"      telemetria KO ({e})")
            if v in KO:
                alerts.append((k, step, el, v, rec.get("spiegazione", "")))
                print(f"      !! AVVISO: {_asc(rec.get('spiegazione',''))[:100]}")
            last_committed = k
            k += 1

        out = a.out or os.path.join(rundir, "_registro_coda.json")
        json.dump(oper.dump(), open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        # Sidecar posizionale, come nel live. Nome diverso da pixel.json: quello è il
        # dato della run live e non va sovrascritto da una rielaborazione offline.
        if oper.pixel:
            with open(os.path.join(rundir, "pixel_coda.json"), "w", encoding="utf-8") as f:
                json.dump(oper.pixel, f, ensure_ascii=False, separators=(",", ":"))
            print(f"[pixel] pixel_coda.json ({len(oper.pixel)} valutazioni)")
        print(f"\n[fine] {len(oper.records)} step processati. Registro: {out}")
        print(f"[AVVISI] {len(alerts)} da controllare:")
        for kk, step, el, v, sp in alerts:
            print(f"  - STEP {step} ({_asc(el)}): {v}")
        if tel is not None:
            if oper.pixel:                       # il foglio incorpora le viste posizionali
                with open(os.path.join(rundir, "pixel.json"), "w", encoding="utf-8") as f:
                    json.dump({str(o): p for o, p in oper.pixel.items()}, f,
                              ensure_ascii=False, separators=(",", ":"))
            try:
                print(f"[excel] {tel.chiudi(oper)}")
            except Exception as e:
                print(f"[excel] KO ({e})")
    finally:
        os.chdir(old)


if __name__ == "__main__":
    main()
