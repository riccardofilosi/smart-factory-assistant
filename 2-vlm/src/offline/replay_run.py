"""replay_run.py — replay offline fedele di una run salvata, senza radar né telefono.

Rigioca le foto di data/runs/<RUNID> con la stessa pipeline del live (valuta
compresa), ma la classe per-frame viene dal registro live (cls_vista dello
storico): niente VLM, e niente classe-dello-step-corrente forzata (sui frame di
correzione la classe forzata falsa il COSA e il puntatore si blocca). Lo skip
assestamento è identico a radar_live: salta solo se non c'è una correzione attesa.

Il registro live non si tocca: l'esito finisce in data/replay_run/<RUNID>_replay.json
(stessa forma dello storico: lista di record di valuta) per il diff pre/post modifica.

Uso:
  python src/offline/replay_run.py                # tutte le run in data/runs con registro
  python src/offline/replay_run.py <RUNID>        # una sola (es. F1.2)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src", "probe"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "radar"))   # moduli radar
import radar_analyze as ra          # noqa: E402
import radar_operativa as ro        # noqa: E402
import differential as df           # noqa: E402  (usato dalla pipeline)
import telemetria as tm             # noqa: E402
from replay_serie import steps_da_storico    # noqa: E402

RUNS = os.path.join(ROOT, "data", "runs")
REGISTRI = os.path.join(ROOT, "data", "registri")
OUT = os.path.join(ROOT, "data", "replay_run")


def replay(runid):
    reg_path = os.path.join(REGISTRI, runid + "_registro.json")
    if not os.path.isfile(reg_path):
        print(f"[{runid}] senza registro live: salto (serve cls_vista per-frame)")
        return None
    reg = json.load(open(reg_path, encoding="utf-8"))
    cls_by_k = {r["k"]: r["cosa"]["cls_vista"] for r in reg["storico"] if r.get("cosa")}
    cid = reg["commessa"]
    rundir = os.path.join(RUNS, runid)
    tel = tm.Telemetria(rundir, cid=cid, runid=runid)

    old = os.getcwd()
    os.chdir(rundir)
    recs = []
    try:
        oper = ro.Operativa(cid)
        sess = ra.Session()
        frames = sorted(int(f[:-5]) for f in os.listdir(".")
                        if f.endswith(".jpeg") and f[:-5].isdigit() and f != "0.jpeg")
        for k in frames:
            fc = cls_by_k.get(k)
            if fc is None and not oper.attese:
                continue                  # frame che il live non ha giudicato, nessun KO aperto
            try:
                res = sess.analyze(k, novlm=True, force_cls=fc)
            except Exception as e:
                print(f"  k{k}: ERRORE ANALISI {e}")
                continue
            if fc is None or res["blob_box"] is None:
                # come il live: frame senza classe live o senza blob si giudica solo se
                # correzione attesa o fori attesi caldi
                if oper.correzione_in_attesa(res) is None and not oper.attesi_caldi(res):
                    continue
                print(f"  k{k}: senza blob/classe ma i fori parlano -> giudico")
            elif oper.assestamento_sospetto(res) and oper.correzione_in_attesa(res) is None:
                continue
            rec = oper.valuta(res, fonte="registro")
            recs.append(rec)
            ordine = len(oper.storico)
            voce = oper.storico[-1] if oper.storico else {}
            try:
                tel.valutazione(res, rec, ordine, tipo=voce.get("tipo", "nuovo"))
            except Exception as e:
                print(f"  k{k}: telemetria KO ({e})")
            print(f"  k{k} | step {rec.get('step')} | {rec.get('verdetto')} | "
                  f"conc={rec.get('radar', {}).get('concordanza')}")
    finally:
        os.chdir(old)

    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, runid + "_replay.json")
    json.dump({"run": runid, "commessa": cid, "storico": recs},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # corredo completo, come replay_serie: la run live va riemessa dal motore
    # attuale, non solo confrontata (i RUN_*.xlsx delle run vecchie portano i
    # verdetti del motore di allora). Il registro live è un documento storico (cosa
    # decise il motore quel giorno): si conserva accanto, non si perde. Scritto una
    # volta sola, mai sovrascritto.
    live_path = os.path.join(REGISTRI, runid + "_registro_live.json")
    if not os.path.exists(live_path):
        json.dump(reg, open(live_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"  registro live conservato in {os.path.basename(live_path)}")
    json.dump({"run": runid, "commessa": cid,
               "steps": steps_da_storico(oper.storico),
               "storico": oper.storico},
              open(os.path.join(REGISTRI, runid + "_registro.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({str(o): p for o, p in oper.pixel.items()},
              open(os.path.join(rundir, "pixel.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    try:
        print(f"  excel: {tel.chiudi(oper)}")
    except Exception as e:
        print(f"  excel KO ({e})")
    print(f"[{runid}] {len(recs)} valutazioni -> {out_path}")
    return recs


def main():
    if len(sys.argv) > 1:
        runids = [sys.argv[1]]
    else:
        runids = sorted(d for d in os.listdir(RUNS)
                        if os.path.isdir(os.path.join(RUNS, d)))
    for runid in runids:
        print(f"== replay {runid} ==")
        replay(runid)


if __name__ == "__main__":
    main()
