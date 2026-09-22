"""replay_vlm.py — rigioca TUTTE le run di data/runs col motore COMPLETO e produce
gli Excel richiesti: uno per run (dentro la cartella-run) più uno unico che li unisce.

Differenza da replay_run.py / replay_serie.py: qui il VLM CHIAMA DAVVERO. La classe
non viene né forzata dal registro (replay_run) né spenta (replay_serie): ogni frame
promosso passa da df.vlm_arrived, esattamente come nel live. Il resto del giro è
quello del live:

    gate -> guardie -> blob -> COSA (VLM) -> DOVE (NCC + rami di classe)
         -> arbitro correzioni -> valuta vs golden -> telemetria

GATE, cosa è riapplicabile e cosa no (misurato su tutte le 27 run).
`radar_gate.should_analyze` fa due test:
  1. SCENA FERMA  frame_diff(cur, scatto PRECEDENTE DELLA CATTURA) < STILL 0.010
  2. NOVITÀ      novelty(cur, ultimo VALIDATO) >= NOVELTY 0.040
Nelle cartelle-run stanno SOLO i frame già validati (il live salva k.jpeg dopo il
gate; gli scarti non erano su disco). Il test 1 confronta quindi due frame separati da
un intero montaggio, non da 2 secondi: misura 0.010-0.150 e boccerebbe 178 frame su
269 — non è una regressione del gate, è che lo stream grezzo non esiste più.
Qui il test 1 si MISURA e si scrive in scatti.csv, ma NON filtra (per costruzione quei
frame l'hanno già passato dal vivo); il test 2 è calcolabile e FILTRA come nel live
(1 solo scarto su 269). Dichiarato anche nel foglio Excel.

NON DISTRUTTIVO: la telemetria live (telemetria/, RUN_<id>.xlsx), i registri di
data/registri e pixel.json non si toccano. Qui si scrive solo:
    <run>/telemetria_vlm/         viste e scatti.csv di questa rigiocata
    <run>/VLM_<runid>.xlsx        l'Excel della run
    data/replay_vlm/<runid>_registro.json
    data/VLM_TUTTE_LE_RUN.xlsx    l'Excel unico

Uso:
  python src/offline/replay_vlm.py                 # tutte le run di data/runs
  python src/offline/replay_vlm.py <RUNID> [...]   # solo quelle indicate
  python src/offline/replay_vlm.py --solo-unione   # ricostruisce l'Excel unico dai JSON già fatti
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # .../2-vlm
sys.path.insert(0, os.path.join(ROOT, "src", "probe"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "radar"))   # moduli radar

import cv2                            # noqa: E402
import differential as df             # noqa: E402
import radar_analyze as ra            # noqa: E402
import radar_gate as rg               # noqa: E402
import radar_operativa as ro          # noqa: E402
import filo as fl                    # noqa: E402
import nomi as nm                     # noqa: E402
import telemetria as tm               # noqa: E402
from replay_serie import steps_da_storico    # noqa: E402

RUNS = os.path.join(ROOT, "data", "runs")
OUT = os.path.join(ROOT, "data", "replay_vlm")
UNICO = os.path.join(ROOT, "data", "VLM_TUTTE_LE_RUN.xlsx")
SUB = "telemetria_vlm"

NOTA_GATE = ("gate: novita' localizzata (NOVELTY 0.040) APPLICATA; scena ferma "
             "(STILL 0.010) misurata e registrata in telemetria_vlm/01_scarti/"
             "scatti.csv ma non applicata - lo stream grezzo pre-gate non e' su "
             "disco, in cartella-run stanno solo i frame gia' validati dal live")


def _cartella_lavoro(rundir, reali):
    """Copia 0.jpeg + le foto RINUMERATE CONTIGUE in una cartella temporanea.

    Serve perché il motore propaga la mappa foto per foto (differential.make_frame:
    frame(k) chiama frame(k-1) e registra ORB sulla precedente): un BUCO nella
    numerazione — foto tolte a mano dalla cartella-run — spezza la catena e l'analisi
    muore con "manca <k>.jpeg". Si lavora su una copia, la cartella-run resta
    intatta.

    Ritorna (cartella, {k_di_lavoro: k_reale})."""
    work = tempfile.mkdtemp(prefix="vlm_")
    shutil.copy2(os.path.join(rundir, "0.jpeg"), os.path.join(work, "0.jpeg"))
    orig = {}
    for i, k in enumerate(reali, 1):
        shutil.copy2(os.path.join(rundir, f"{k}.jpeg"), os.path.join(work, f"{i}.jpeg"))
        orig[i] = k
    return work, orig


# ---------------------------------------------------------------- una run
def replay(runid):
    """Rigioca una run. Ritorna la lista di righe (una per valutazione) o None."""
    cid = nm.cid(runid)
    rundir = os.path.abspath(os.path.join(RUNS, runid))
    reali = sorted(int(f[:-5]) for f in os.listdir(rundir)
                   if f.endswith(".jpeg") and f[:-5].isdigit() and f != "0.jpeg")
    if not reali:
        print(f"[{runid}] nessun frame: salto")
        return None
    work, orig = _cartella_lavoro(rundir, reali)
    if orig and max(orig) != max(orig.values()):
        print(f"  numerazione con buchi ({len(reali)} foto, ultima {max(reali)}): "
              f"rinumerate 1..{len(reali)} in copia, l'Excel riporta i numeri VERI")
    frames = sorted(orig)

    old = os.getcwd()
    os.chdir(work)
    tel = tm.Telemetria(rundir, cid=cid, runid=runid, sub=SUB)
    forcemap = False
    try:
        img0 = cv2.imread("0.jpeg")
        tel.fase_mappa(img0)                       # SETUP del flowchart
        oper = ro.Operativa(cid)                   # sys.exit se commessa sconosciuta
        df.FORCEMAP = False
        try:
            sess = ra.Session()                    # costruisce la mappa su 0.jpeg
        except SystemExit as e:
            # difetto minore della mappa (es. 1 foro di rail): è il caso per cui il
            # live ha --forcemap. Si prosegue dichiarandolo.
            if "MAPPA" not in str(e):
                raise
            print(f"  {str(e).splitlines()[0]} -> riprovo con FORCEMAP (dichiarato)")
            df.FORCEMAP = True
            forcemap = True
            sess = ra.Session()
        prev_raw, last_valid = img0, img0
        nshot = 0
        assest = {}                     # step -> foto REALE dell'ultimo suo assestamento
        for k in frames:
            nshot += 1
            kr = orig[k]                      # numero VERO della foto nella cartella-run
            cur = cv2.imread(f"{k}.jpeg")
            if cur is None:
                print(f"  k{kr}: foto illeggibile, salto")
                continue
            # ---- GATE (vedi docstring: STILL misurato, NOVELTY applicato) ----
            d_prev = rg.frame_diff(cur, prev_raw)
            nov = rg.novelty(cur, last_valid)
            prev_raw = cur
            if nov < rg.NOVELTY:
                tel.scatto(nshot, "SCARTO_GATE", motivo="ferma_ma_uguale",
                           d_prev=d_prev, novelty=nov, img=cur)
                print(f"  k{kr}: scarto gate (novelty {nov:.3f} < {rg.NOVELTY})")
                continue
            last_valid = cur
            # ---- GUARDIE + BLOB + COSA(VLM) + DOVE ----
            try:
                res = sess.analyze(k)             # niente novlm/force_cls: VLM VIVO
            except SystemExit as e:
                print(f"  k{kr}: ANALISI INTERROTTA {e}")
                continue
            except Exception as e:
                print(f"  k{kr}: ERRORE ANALISI {type(e).__name__} {e}")
                continue
            # ---- novità reale? (stessa logica del live) ----
            if res["blob_box"] is None:
                if oper.correzione_in_attesa(res) is None and not oper.attesi_caldi(res):
                    tel.scatto(nshot,
                               "SCARTO_GUARDIA" if res.get("frame_ko") else "NO_BLOB",
                               motivo=res.get("frame_ko") or "nessun blob",
                               d_prev=d_prev, novelty=nov,
                               allineamento=res.get("allineamento"),
                               caldi=res.get("caldi"), img=cur)
                    print(f"  k{kr}: {res.get('frame_ko') or 'nessun blob'} -> scarto")
                    continue
            elif (oper.assestamento_sospetto(res)
                  and oper.correzione_in_attesa(res) is None):
                # Di chi è il pezzo mosso. Lo step m è già stato giudicato su uno scatto
                # precedente allo spostamento: si tiene il numero di foto reale
                # dell'ultimo assestamento, e nel foglio la riga di quello step ci
                # punterà invece che alla prima.
                di = getattr(oper, "assestamento_di", None)
                if di:
                    assest[di] = kr
                tel.scatto(nshot, "ASSESTAMENTO",
                           motivo=(f"pezzo dello step {di} rimesso a posto" if di else
                                   f"step {oper.step_i + 1} resta aperto"),
                           d_prev=d_prev, novelty=nov,
                           allineamento=res.get("allineamento"),
                           caldi=res.get("caldi"), img=cur)
                print(f"  k{kr}: assestamento -> " + (f"pezzo dello step {di}" if di else
                                                     f"step {oper.step_i + 1} resta aperto"))
                continue
            tel.scatto(nshot, "ANALIZZATO", motivo=f"foto {kr}",
                       d_prev=d_prev, novelty=nov,
                       allineamento=res.get("allineamento"), caldi=res.get("caldi"),
                       blob_area=(len(res["blob_pts"])
                                  if res.get("blob_pts") is not None else None))
            # ---- VALUTA vs golden ----
            fonte = "vlm" if res["cls"] else "golden"
            rec = oper.valuta(res, fonte=fonte)
            ordine = len(oper.storico)
            voce = oper.storico[-1] if oper.storico else {}
            tel.valutazione(res, rec, ordine, tipo=voce.get("tipo", "nuovo"))
            print(f"  k{kr} | step {rec.get('step')} | {rec.get('verdetto')} | "
                  f"cosa={(rec.get('cosa') or {}).get('cls_vista')} | "
                  f"{(rec.get('spiegazione') or '')[:70]}")
    finally:
        os.chdir(old)
        shutil.rmtree(work, ignore_errors=True)

    for h in oper.storico:                 # numeri di foto VERI, non quelli di lavoro
        h["k"] = orig.get(h.get("k"), h.get("k"))
    os.makedirs(OUT, exist_ok=True)
    json.dump({"run": runid, "commessa": cid, "nota_gate": NOTA_GATE,
               "forcemap": forcemap, "assestamenti": assest,
               "steps": steps_da_storico(oper.storico), "storico": oper.storico},
              open(os.path.join(OUT, runid + "_registro.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    righe = _righe(runid, cid, oper.storico, oper.build, rundir, assest)
    # le righe si salvano PRIMA dell'Excel: se il file è aperto in Excel il
    # salvataggio fallisce e il lavoro (VLM compreso) non va perso -> si rifa'
    # il solo foglio con --solo-excel, senza rigiocare la run.
    json.dump(righe, open(os.path.join(OUT, runid + "_righe.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
    xls = excel_run(runid, righe)
    print(f"[{runid}] {len(righe)} righe -> {xls or 'Excel NON scritto'}")
    return righe


def excel_run(runid, righe=None):
    """Scrive <run>/VLM_<runid>.xlsx. Senza `righe` le RICOSTRUISCE dal registro:
    così una modifica alle colonne si applica senza rigiocare la run (il VLM non
    si richiama). Le righe salvate potrebbero essere in un formato vecchio."""
    if righe is None:
        reg = json.load(open(os.path.join(OUT, runid + "_registro.json"),
                             encoding="utf-8"))
        golden, _ = df.load_golden(reg["commessa"])
        righe = _righe(runid, reg["commessa"], reg["storico"], golden["build"],
                       os.path.join(RUNS, runid), reg.get("assestamenti"))
        json.dump(righe, open(os.path.join(OUT, runid + "_righe.json"), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
    cid = nm.cid(runid)
    xls = os.path.join(RUNS, runid, f"VLM_{runid}.xlsx")
    try:
        scrivi_excel(xls, righe, titolo=f"RUN {runid} - commessa {cid}", con_run=False)
    except PermissionError:
        print(f"  !! {os.path.basename(xls)} e' APERTO in Excel: non riscritto."
              f"\n     Chiudilo e rifai il solo foglio (niente VLM):"
              f"\n     python src/offline/replay_vlm.py --solo-excel {runid}")
        return None
    return xls


def _rail_attesa(att):
    """Rail dichiarata dal GOLDEN per questo step, o None.

    `coords` tiene solo i fori di GRIGLIA: la rail vive solo nel testo di `holes`
    ("b44 -> +"). Che il pezzo abbia un capo su rail si deduce dal CONTO — i pin
    della classe meno i fori di griglia, lo stesso n_rail di radar_operativa — e
    solo dopo si legge il segno nel testo.

    Il conto non è un di più: senza, le POLARITÀ scritte accanto ai fori passano
    per rail. "− e11 (collettore) · + e14" è un buzzer con due fori di griglia e
    zero rail, ma contiene sia + che −. Verificato su tutto il golden: 35 step con
    un capo su rail, segno univoco in tutti e 35, e nessun falso positivo."""
    cls = att.get("cls")
    if df.PINS.get(cls, 2) - len(att.get("coords") or []) <= 0:
        return None
    segni = set(re.findall(r"[+−]", att.get("holes") or ""))
    if len(segni) != 1:
        return None
    return "+" if segni == {"+"} else "-"


def _pin_letti(h, occupati=frozenset()):
    """Coordinate dei fori dove il motore ha VISTO qualcosa.

    Sostituisce la vecchia colonna "fori proposti", che stampava sempre il pick
    CIECO di fit_pins. Misurato sulle 10 commesse: il pick decide su 7 step golden
    su 78 (bottone, optoaccoppiatore, rgb); sugli altri il motore lo spegne apposta
    e stamparlo faceva sembrare che il sistema proponesse posizioni mai sostenute.

    Il pick si stampa SOLO se ha pesato sul verdetto: non quando la classe è
    solo-golden / corpo / filo, e non quando la concordanza è MUTO, FANTASMA o
    OCCUPATO — i tre modi in cui il motore SCARTA il pick (piedino su foro muto,
    fantasma della rimozione, piedino su foro di uno step già chiuso).

    Altrimenti valgono i fori GOLDEN — ma solo quelli in cui il motore ha trovato
    segnale, non tutti quelli misurati (un foro a 0.004 contro soglia 0.07 è un
    foro vuoto: scriverlo fra i "letti" accanto a un KO POSIZIONE lo farebbe
    sembrare un errore del sistema). A verdetto OK si stampano tutti: se è OK,
    per il motore erano tutti vivi, qualunque pavimento abbia usato per dirlo.

    RAIL: si stampano dove il motore le VERIFICA davvero, cioè sui fili (controllo
    del segno atteso in _giudica). Su una resistenza il pick le legge ma nessun ramo
    le controlla, e lì restano fuori — le certifica run_qc a posteriori.

    Eccezione su OK? e KO POSIZIONE. Proprio lì i due filtri qui sopra si sommano e
    la colonna resterebbe vuota o ridotta a una rail: su quei verdetti la
    concordanza è sempre SOLO GOLDEN / FILO / CORPO (pick scartato) e i fori golden
    sono muti per definizione, è il motivo del verdetto. All'operatore che legge un
    KO POSIZIONE serve un posto dove guardare, anche sbagliato: su questi due
    verdetti si stampa il pick marcato "(ipotesi)", così resta chiaro che è una
    congettura del radar e non una lettura sostenuta dal verdetto. Se il pick è
    vuoto si ricade sui fori letti (non si peggiora mai una riga che già diceva
    qualcosa). Gli altri verdetti non cambiano.

    Quanto vale quell'ipotesi (misurato rigiocando le run offline): i fori escono
    davvero da fit_pins, si riproducono senza VLM (sola geometria + colore) e non
    sono inventati qui. Ma non sono nemmeno i fori più caldi, per due motivi:
      1. per costruzione. fit_pins cerca le gambe, cioè gli estremi del segmento
         supportato (sup_line + resolve_end), mentre il massimo di calore sta al
         centro, sul corpo (misurato: pick a 0.198 con 0.581 accanto).
      2. perché è cieco sul contesto. Il pick lavora attorno al blob e non sa quale
         pezzo è nuovo: può agganciare il LED dello step precedente.
    Soprattutto: su queste classi (FAM_SOLO_GOLDEN in radar_operativa) il radar è
    spento apposta, quindi l'ipotesi non è mai stata validata da nessuna misura.
    È un posto dove guardare, non una misura."""
    att = h.get("atteso") or {}
    rad = h.get("radar") or {}
    pins = rad.get("pins") or []
    griglia = [p for p in pins if p[:1].isalpha()]
    rail = [p for p in pins if not p[:1].isalpha()]        # '+' / '-'
    if not att.get("coords"):
        return ",".join(pins) or "-"        # step solo-rail: c'è solo ciò che vede il pick
    conc = str(rad.get("concordanza") or "")
    usa_pick = (griglia
                and conc not in ("MUTO", "FANTASMA", "OCCUPATO")
                and not conc.startswith("SOLO GOLDEN")
                and att.get("cls") not in fl.FAM_FILO
                and not any((h.get(r) or {}).get("livello")
                            for r in ("cupola", "filo", "corpo")))
    if usa_pick:
        base = griglia
    else:
        anc = h.get("ancorata") or {}
        mis = anc.get("misure") or {}
        sg = anc.get("soglia") or 0.0
        base = (list(mis) if h.get("verdetto") == "OK"
                else [k for k, v in mis.items() if float(v) >= sg])
    if att.get("cls") not in fl.FAM_FILO:
        rail = []
    letti = ",".join(base + rail) or "-"
    if usa_pick or h.get("verdetto") not in ("OK?", "KO POSIZIONE"):
        return letti
    # Fori di pezzi già montati fuori dall'ipotesi: un foro occupato non può
    # accogliere un secondo pezzo, quindi come ipotesi su dove sia finito il pezzo è
    # impossibile, e stamparla toglie credito anche al resto della riga. Stessa
    # regola della concordanza OCCUPATO in radar_operativa, applicata a ciò che si
    # stampa. Le rail non si filtrano: sono nodi, ci arrivano più pezzi.
    cieco = [p for p in pins if p in ("+", "-") or p not in occupati]
    if not any(p in ("+", "-") for p in cieco):
        # Rail del golden. Se il golden dichiara un capo su rail, un'ipotesi di soli
        # fori di griglia è anatomicamente impossibile per quel pezzo: la resistenza di
        # "b44 -> +" un capo sul + ce l'ha per costruzione. Dal pick la rail non esce
        # quasi mai (il ramo di continuità la aggancia solo se cade entro ~1.5 passi dal
        # capo della sagoma), ma è spesso la misura più alta della finestra. Si scrive:
        # è l'unico pezzo dell'ipotesi che non è una congettura.
        seg = _rail_attesa(att)
        if seg:
            cieco.append(seg)
    return f"{','.join(cieco)} (ipotesi)" if cieco else letti


def _righe(runid, cid, storico, build, rundir, assest=None):
    """Una riga per VALUTAZIONE (correzioni comprese), in ordine cronologico, più in
    coda gli step golden che nessuno scatto ha isolato (dichiarati, non inventati).

    Le viste si ritrovano per CONVENZIONE dal numero d'ordine (telemetria le scrive
    come vNNN_<vista>.png): così le righe si ricostruiscono dal solo registro.

    `assest` = {step: foto} degli assestamenti scartati (v. replay). Il link "scatto"
    di quello step punta alla foto definitiva del pezzo invece che a quella su cui è
    stato giudicato: se un pezzo viene spostato dopo il giudizio, chi apre il foglio
    vuole vedere dov'è finito."""
    assest = {int(k): v for k, v in (assest or {}).items()}
    dval = os.path.join(rundir, SUB, "02_valutazioni")
    righe, visti = [], set()
    # fori occupati dagli step già passati, accumulati in ordine CRONOLOGICO (lo
    # storico è in quest'ordine; il riordino per step avviene solo in fondo)
    coords_di = {}
    for h in storico:
        occupati = {c for s, cs in coords_di.items() if s != h.get("step") for c in cs}
        if h.get("step"):
            coords_di[h["step"]] = (h.get("atteso") or {}).get("coords") or []
        tag = f"v{h.get('ordine', 0):03d}"
        img = {v: f"{tag}_{n}.png" for v, n in
               (("blob", "blob"), ("ncc_D", "ncc_D"), ("ncc_A", "ncc_A"),
                ("cosa", "cosa"))}
        att = h.get("atteso") or {}
        cosa = h.get("cosa") or {}
        if h.get("step"):
            visti.add(h["step"])
        # Classe e colore hanno due fonti diverse e vanno tenuti separati: la classe la
        # dice il VLM, il colore lo misura OpenCV (df.blob_color); radar_analyze scarta
        # apposta la risposta colore del VLM. Unirli sotto "letto dal VLM" attribuirebbe
        # al VLM lo scambio verde/azzurro, che è della segmentazione HSV.
        letto = cosa.get("cls_vista") or ""
        # classe SCARTATA dal motore (pezzo di uno step già chiuso, v. radar_operativa
        # _classe_ancora_possibile): la risposta del VLM resta scritta — serve per
        # capire come sbaglia — ma non deve sembrare la classe con cui si è giudicato.
        if letto and cosa.get("esito") == "NON VERIFICATO":
            letto += " (scartata)"
        righe.append({
            "run": runid, "commessa": cid, "step": h.get("step"),
            # FORI ATTESI = il testo del GOLDEN, che contiene anche le RAIL
            # ("d2 -> +"): `coords` tiene solo i fori di griglia e la rail spariva.
            "fori_attesi": (att.get("holes") or ",".join(att.get("coords") or [])
                            or "-"),
            "pin_letti": _pin_letti(h, occupati),
            "verdetto": h.get("verdetto", ""),
            "cosa": letto or "(non letta)",
            "colore": cosa.get("col_vista") or "-",
            "k": h.get("k"), "tipo": h.get("tipo", ""),
            "elemento": att.get("el", ""), "fonte": cosa.get("fonte", ""),
            "spiegazione": (h.get("spiegazione", "") + (
                f" [il pezzo e' stato poi rimesso a posto: il link 'scatto' apre la "
                f"foto {assest[h['step']]}, la sua posizione DEFINITIVA, non la {h.get('k')} "
                f"su cui e' stato giudicato]"
                if h.get("step") in assest and assest[h["step"]] != h.get("k") else "")),
            "foto": [
                ("scatto", os.path.join(
                    rundir, f"{assest.get(h.get('step'), h.get('k'))}.jpeg")),
                ("diff blob", os.path.join(dval, img.get("blob") or "")),
                ("NCC foto", os.path.join(dval, img.get("ncc_D") or "")),
                ("NCC vista A", os.path.join(dval, img.get("ncc_A") or "")),
                ("crop COSA", os.path.join(dval, img.get("cosa") or "")),
            ],
        })
    for i, st in enumerate(build, 1):
        if i in visti:
            continue
        righe.append({
            "run": runid, "commessa": cid, "step": i,
            "fori_attesi": (st.get("holes") or ",".join(st.get("coords") or [])
                            or "-"),
            "pin_letti": "-", "verdetto": "NON GIUDICATO",
            "cosa": "-", "colore": "-", "k": "", "tipo": "senza scatto",
            "elemento": st.get("el", ""), "fonte": "",
            "spiegazione": "nessuno scatto della run ha isolato questo step",
            "foto": [],
        })
    # Ordine per step: in ordine cronologico, con le correzioni, lo stesso step
    # comparirebbe più volte sparso. Le righe di uno step stanno insieme e in ordine
    # di foto: leggere "step 8" significa trovare tutte le sue valutazioni una sotto
    # l'altra. La cronologia resta leggibile nella colonna `foto`.
    righe.sort(key=lambda x: (x["step"] if x["step"] is not None else 10 ** 6,
                              int(x["k"]) if str(x["k"]).isdigit() else 10 ** 6))
    return righe


# ---------------------------------------------------------------- Excel
# `spiegazione` non esce sul foglio. Resta nel registro e in
# telemetria_vlm/02_valutazioni/vNNN_rec.json, dove ci sono anche i numeri che
# hanno deciso il verdetto.
COLS = [("commessa", 11), ("step", 6), ("fori attesi", 15),
        ("pin letti", 22),
        ("verdetto", 15), ("cosa: classe letta dal VLM", 20),
        ("colore misurato da OpenCV", 16),
        ("foto", 6), ("tipo", 12), ("elemento atteso", 26), ("fonte", 7)]
FOTO = ["scatto", "diff blob", "NCC foto", "NCC vista A", "crop COSA"]
THUMB_W = 140


def _thumb(path, nome, dest):
    """Miniatura per l'Excel. Tutte in <run>/telemetria_vlm/_thumbs_xlsx: la
    cartella-run non si sporca (le foto della run restano le uniche .jpeg lì dentro)."""
    try:
        im = cv2.imread(path)
        if im is None:
            return None
        s = THUMB_W / im.shape[1]
        im = cv2.resize(im, (THUMB_W, max(1, int(im.shape[0] * s))),
                        interpolation=cv2.INTER_AREA)
        d = dest
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{nome}.png")
        cv2.imwrite(p, im)
        return p
    except Exception:
        return None


def scrivi_excel(path, righe, titolo, con_run=True, riepilogo=None):
    import openpyxl
    from openpyxl.styles import Font as F, PatternFill, Alignment, Border, Side
    from openpyxl.drawing.image import Image as XImg
    from openpyxl.utils import get_column_letter

    HEAD = PatternFill("solid", fgColor="1A2430")
    HEADF = F(name="Bahnschrift", bold=True, color="FFFFFF", size=10)
    TITLE = F(name="Bahnschrift", bold=True, size=15, color="1A2430")
    SUBF = F(name="Consolas", size=8, color="4C5966")
    KO_F = PatternFill("solid", fgColor="F9E3D5")
    OK_F = PatternFill("solid", fgColor="EAF3EC")
    GIA_F = PatternFill("solid", fgColor="FBF3D9")
    NEU_F = PatternFill("solid", fgColor="F1F3F5")
    thin = Side(style="thin", color="C9D0D6")
    BORD = Border(left=thin, right=thin, top=thin, bottom=thin)
    CEN = Alignment(horizontal="center", vertical="center", wrap_text=True)
    LFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "valutazioni"
    ws.cell(1, 1, titolo).font = TITLE
    ws.cell(2, 1, "motore completo: gate -> guardie -> blob -> COSA (VLM Gemini) -> "
                  "DOVE (NCC + rami di classe) -> arbitro correzioni -> verdetto vs "
                  "golden. Fonte di verita': il codice in 2-vlm/src").font = SUBF
    ws.cell(3, 1, NOTA_GATE).font = SUBF

    LINK = F(name="Bahnschrift", size=9, color="0A4EBF", underline="single")
    cols = ([("run", 30)] if con_run else []) + COLS
    # ogni immagine ha accanto una colonna stretta "apri" col link al file: la
    # miniatura copre la cella e il click non arriverebbe all'hyperlink
    testa_foto = []
    for f in FOTO:
        testa_foto += [(f, 21), ("apri", 6)]
    testata = 5
    for i, (h, w) in enumerate(cols + testa_foto, 1):
        c = ws.cell(testata, i, h)
        c.fill = HEAD; c.font = HEADF; c.alignment = CEN; c.border = BORD
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(testata + 1, 1).coordinate

    for j, x in enumerate(righe):
        r = testata + 1 + j
        ws.row_dimensions[r].height = 82
        vals = ([x["run"]] if con_run else []) + [
            x["commessa"], x["step"] if x["step"] is not None else "-",
            x["fori_attesi"], x.get("pin_letti", "-"), x["verdetto"], x["cosa"],
            x.get("colore", "-"),
            x["k"], x["tipo"], x["elemento"], x["fonte"]]
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v)
            c.border = BORD
            c.alignment = LFT if cols[i - 1][0] in (
                "elemento atteso", "run") else CEN
        v = str(x["verdetto"])
        fill = (KO_F if v.startswith("KO") else
                GIA_F if v in ("OK?", "DEBOLE") else
                OK_F if v.startswith("OK") else NEU_F)
        for i in range(1, len(cols) + 2 * len(FOTO) + 1):
            ws.cell(r, i).fill = fill
        disp = dict(x.get("foto") or [])
        for i, nome in enumerate(FOTO):
            col = len(cols) + 1 + 2 * i          # immagine, poi "apri"
            p = disp.get(nome) or ""
            ws.cell(r, col).border = BORD
            link = ws.cell(r, col + 1)
            link.border = BORD
            link.alignment = CEN
            if not p or os.path.isdir(p) or not os.path.exists(p):
                continue
            th = _thumb(p, f"{x['run']}_{x['step']}_{x['k']}_{nome.replace(' ', '')}",
                        os.path.join(RUNS, x["run"], SUB, "_thumbs_xlsx"))
            if th:
                ws.add_image(XImg(th), f"{get_column_letter(col)}{r}")
            link.value = "apri"
            link.hyperlink = p
            link.font = LINK

    if riepilogo:
        wr = wb.create_sheet("riepilogo", 0)
        H = ["run", "commessa", "valutazioni", "step golden", "step giudicati",
             "KO", "OK", "OK?/DEBOLE", "classe letta dal VLM",
             "classe NON letta", "scarti gate", "note"]
        for i, h in enumerate(H, 1):
            c = wr.cell(1, i, h)
            c.fill = HEAD; c.font = HEADF; c.alignment = CEN; c.border = BORD
            wr.column_dimensions[get_column_letter(i)].width = (30 if i == 1 else
                                                                34 if i == 12 else 14)
        wr.freeze_panes = "A2"
        for j, x in enumerate(riepilogo):
            for i, h in enumerate(H, 1):
                c = wr.cell(j + 2, i, x.get(h, ""))
                c.border = BORD
                c.alignment = LFT if h in ("run", "note") else CEN
    wb.save(path)
    return path


# ---------------------------------------------------------------- unione
def unione(runids=None):
    """Excel unico da tutti i *_righe.json già prodotti."""
    if not os.path.isdir(OUT):
        print("niente da unire")
        return None
    files = sorted(f for f in os.listdir(OUT) if f.endswith("_righe.json"))
    if runids:
        files = [f for f in files if f[:-len("_righe.json")] in runids]
    tutte, riep = [], []
    for f in files:
        righe = json.load(open(os.path.join(OUT, f), encoding="utf-8"))
        tutte += righe
        rid = f[:-len("_righe.json")]
        reg = os.path.join(OUT, rid + "_registro.json")
        nstep, fm = 0, False
        try:
            r = json.load(open(reg, encoding="utf-8"))
            nstep = len({s["step"] for s in r.get("steps", []) if s.get("step")})
            fm = bool(r.get("forcemap"))
        except Exception:
            pass
        giud = [x for x in righe if x["verdetto"] != "NON GIUDICATO"]
        riep.append({
            "run": rid, "commessa": righe[0]["commessa"] if righe else "",
            "valutazioni": len(giud),
            "step golden": len({x["step"] for x in righe if x["step"]}),
            "step giudicati": nstep,
            "KO": sum(1 for x in giud if str(x["verdetto"]).startswith("KO")),
            "OK": sum(1 for x in giud if x["verdetto"] == "OK"),
            "OK?/DEBOLE": sum(1 for x in giud if x["verdetto"] in ("OK?", "DEBOLE")),
            "classe letta dal VLM": sum(1 for x in giud if x["fonte"] == "vlm"),
            "classe NON letta": sum(1 for x in giud if x["cosa"] == "(non letta)"),
            "scarti gate": _conta_scarti(rid),
            "note": (("FORCEMAP: mappa con difetto minore, accettata (come nel live). "
                      if fm else "")
                     + "; ".join(sorted({x["spiegazione"] for x in righe
                                         if x["verdetto"] == "NON GIUDICATO"})))[:200],
        })
    scrivi_excel(UNICO, tutte, titolo="VLM - tutte le run di 2-vlm/data/runs",
                 con_run=True, riepilogo=riep)
    print(f"\nExcel unico: {UNICO}  ({len(tutte)} righe, {len(files)} run)")
    return UNICO


def _conta_scarti(runid):
    p = os.path.join(RUNS, runid, SUB, "01_scarti", "scatti.csv")
    try:
        import csv
        with open(p, encoding="utf-8") as f:
            return sum(1 for r in csv.DictReader(f) if r["esito"] != "ANALIZZATO")
    except Exception:
        return ""


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--solo-unione" in sys.argv:
        unione(argv or None)
        return
    if "--solo-excel" in sys.argv:
        # rifà solo il foglio dalle righe già salvate: nessuna chiamata al VLM
        for runid in (argv or sorted(d[:-len("_righe.json")] for d in os.listdir(OUT)
                                     if d.endswith("_righe.json"))):
            x = excel_run(runid)
            if x:
                print(f"[{runid}] foglio riscritto -> {x}")
        return
    runids = argv or sorted(d for d in os.listdir(RUNS)
                            if os.path.isdir(os.path.join(RUNS, d)))
    fatte = []
    for runid in runids:
        print(f"\n== replay VLM {runid} ==", flush=True)
        try:
            if replay(runid) is not None:
                fatte.append(runid)
        except SystemExit as e:
            print(f"  RUN SALTATA: {e}")
        except Exception as e:
            print(f"  RUN SALTATA: {type(e).__name__} {e}")
    if argv:
        # revisione run-per-run: l'Excel unico NON si tocca finché non sono tutte
        # validate (sennò un giro singolo lo riduce a una run sola)
        print(f"\n{len(fatte)} run rigiocate. Excel unico invariato: "
              f"a fine revisione -> python src/offline/replay_vlm.py --solo-unione")
    else:
        unione(fatte or None)


if __name__ == "__main__":
    main()
