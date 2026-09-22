"""replay_serie.py — QC OFFLINE delle serie fotografiche ("runs da integrare"):
rigioca 0..N.jpeg col motore operativo ATTUALE (ancora + controprove), SENZA VLM
(cosa = NON VERIFICATO: qui si valida il DOVE). Le serie vecchie hanno 10 marker
e qualche foro di rail perso nella mappa -> FORCEMAP implicito, dichiarato.

Output: per ogni serie una cartella AUTONOMA in <repo>/"data run da integrare"/
<RUNID>/ con lo STESSO corredo di una run live: foto numerate (copiate),
<RUNID>_registro.json, pixel.json, telemetria/ (viste per valutazione) e
RUN_<RUNID>.xlsx — l'Excel identico a quello delle run live.

Diverso da replay_run.py (che rigioca le run live col registro): qui non esiste
un registro, la classe non viene forzata (la classe forzata falsa i frame di
correzione) e ogni frame passa dagli stessi arbitri del live (assestamento,
correzione posizionale).

Uso:
  python src/offline/replay_serie.py            # tutte le serie in <repo>/runs da integrare
  python src/offline/replay_serie.py <RUNID>    # una sola
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # .../2-vlm
sys.path.insert(0, os.path.join(ROOT, "src", "probe"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "radar"))   # moduli radar
import differential as df          # noqa: E402
import radar_analyze as ra         # noqa: E402
import radar_operativa as ro       # noqa: E402
import nomi as nm                  # noqa: E402
import telemetria as tm            # noqa: E402

SERIE = os.path.join(os.path.dirname(ROOT), "runs da integrare")
# le run rigiocate vivono in data/runs, dove la raccolta le legge: una cartella sola
OUTBASE = os.path.join(os.path.dirname(ROOT), "3-raccolta-dati", "runs")


def steps_da_storico(storico):
    """`steps` dal solo storico: un record per step, l'ultimo vince.

    Le run live salvano sia `steps` (uno per step, la correzione sostituisce lo
    scarto: Operativa.records) sia `storico` (tutto, append-only). score_oper e
    gt_wizard leggono `steps`: qui si ricostruisce con la stessa semantica di
    records."""
    per_step = {}
    for r in storico:
        if r.get("step") is not None:
            per_step[r["step"]] = r
    return [per_step[n] for n in sorted(per_step)]


def replay(runid):
    cid = nm.cid(runid)
    srcdir = os.path.join(SERIE, runid)
    outdir = os.path.join(OUTBASE, runid)
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(srcdir):                 # cartella autonoma: foto copiate
        if f.split(".")[0].isdigit():
            dst = os.path.join(outdir, f)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(srcdir, f), dst)

    old = os.getcwd()
    os.chdir(srcdir)
    tel = tm.Telemetria(outdir, cid=cid, runid=runid)
    try:
        df.FORCEMAP = True                 # serie vecchie: mappa accettata, dichiarato
        oper = ro.Operativa(cid)
        sess = ra.Session()
        frames = sorted(int(f[:-5]) for f in os.listdir(".")
                        if f.endswith(".jpeg") and f[:-5].isdigit() and f != "0.jpeg")
        ultimo = None
        for k in frames:
            try:
                res = sess.analyze(k, novlm=True)
            except SystemExit as e:
                print(f"  k{k}: ANALISI INTERROTTA {e}")
                continue
            except Exception as e:
                print(f"  k{k}: ERRORE ANALISI {e}")
                continue
            ultimo = res                    # anche se il frame viene scartato sotto
            if res["blob_box"] is None:
                # come il live: frame senza blob si giudica solo se una correzione
                # è attesa o i fori attesi parlano
                if oper.correzione_in_attesa(res) is None and not oper.attesi_caldi(res):
                    continue
            elif oper.assestamento_sospetto(res) and oper.correzione_in_attesa(res) is None:
                continue
            rec = oper.valuta(res, fonte="serie")
            ordine = len(oper.storico)
            voce = oper.storico[-1] if oper.storico else {}
            try:
                tel.valutazione(res, rec, ordine, tipo=voce.get("tipo", "nuovo"))
            except Exception as e:
                print(f"  k{k}: telemetria KO ({e})")
            print(f"  k{k} | step {rec.get('step')} | {rec.get('verdetto')} | "
                  f"{(rec.get('spiegazione') or '')[:90]}")

        # Giudizio di chiusura: step golden rimasti senza verdetto perché il loro
        # frame è stato scartato. Misurato: tutti gli step scoperti hanno un solo foro
        # di griglia nel golden (l'altro capo sulla rail), e con un foro solo la
        # lettura muta di quel foro basta a far sembrare il frame un assestamento. Si
        # giudica l'ultimo scatto analizzato e si dichiara che il giudizio è tardivo:
        # meglio un verdetto marcato che un buco silenzioso.
        n_chiusura = 0
        while ultimo is not None and not oper.finita():
            prima = oper.step_i
            rec = oper.valuta(ultimo, fonte="serie")
            if oper.step_i == prima:
                break                       # l'arbitro l'ha letto come correzione: esco
            rec["chiusura"] = True
            if oper.storico:
                oper.storico[-1]["chiusura"] = True
            n_chiusura += 1
            ordine = len(oper.storico)
            try:
                tel.valutazione(ultimo, rec, ordine, tipo="chiusura")
            except Exception as e:
                print(f"  chiusura: telemetria KO ({e})")
            print(f"  CHIUSURA step {rec.get('step')} sull'ultimo scatto "
                  f"(k{ultimo['k']}) | {rec.get('verdetto')}")
        if n_chiusura:
            print(f"  {n_chiusura} step chiusi con giudizio tardivo")
    finally:
        os.chdir(old)

    json.dump({"run": runid, "commessa": cid,
               "steps": steps_da_storico(oper.storico),
               "storico": oper.storico},
              open(os.path.join(outdir, runid + "_registro.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({str(o): p for o, p in oper.pixel.items()},
              open(os.path.join(outdir, "pixel.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    try:
        out = tel.chiudi(oper)
        print(f"  excel: {out}")
    except Exception as e:
        print(f"  excel KO ({e})")
    return oper.records


def main():
    runs = ([sys.argv[1]] if len(sys.argv) > 1 else
            sorted(d for d in os.listdir(SERIE)
                   if os.path.isdir(os.path.join(SERIE, d))))
    tabella = []
    for runid in runs:
        print(f"== serie {runid} ==")
        try:
            finali = replay(runid)
        except SystemExit as e:
            print(f"  RUN SALTATA: {e}")
            tabella.append((runid, "MAPPA KO", ""))
            continue
        except Exception as e:
            print(f"  RUN SALTATA: {type(e).__name__} {e}")
            tabella.append((runid, "ERRORE", str(e)[:60]))
            continue
        vs = [str(r.get("verdetto", "-")) for r in finali]
        ko = sum(1 for v in vs if v.startswith("KO"))
        tabella.append((runid, f"{len(vs)} step, {ko} KO finali", " | ".join(vs)))
    print("\n===== RIEPILOGO SERIE =====")
    for rid, sintesi, dett in tabella:
        print(f"{rid:38s} {sintesi:22s} {dett}")


if __name__ == "__main__":
    main()
