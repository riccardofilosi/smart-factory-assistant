"""radar_live.py — programma principale del controllo in linea. Cattura scatti dal
telefono a intervallo fisso, li fa passare per la pipeline QC e mostra il
ragionamento in finestra.

Uso:
  python radar_live.py [--url http://192.168.1.2:8080/photo.jpg] [--sec 4] [--id LIVE]
  (senza --url il telefono si cerca da solo tra gli indirizzi noti: vedi URL_CANDIDATI)
  python radar_live.py --replay "<cartella-run>"   # rigioca un run salvato, senza telefono
  python radar_live.py --replay "<cartella>" --commessa F1   # collaudo OPERATIVA offline

All'avvio (live) si sceglie la FASE: radar (taratura) o OPERATIVA (montaggio di una
commessa: confronto col golden, report di ragionamento nel terminale, allarme su
errore SENZA blocco - si avanza sempre allo step successivo).

Avvio: 1) RIQUADRO board (parte dal salvato, INVIO conferma)  2) scatto 0 board VUOTA
(INVIO)  3) mappa mostrata (INVIO)  4) loop live con LOG a destra (foto in arrivo,
esito gate, countdown). La finestra "radar live" resta SEMPRE aperta.
Alla rilevazione si apre la finestra ANALISI separata (chiudibile con la X); click su
una riga del log apre la foto in un'altra finestra chiudibile. Il radar non si blocca mai.
Tasti: frecce = cambia foro dell'analisi; click nell'analisi = seleziona foro; S = salva
pannello; Q = esci. In replay: SPAZIO = prossima foto.
"""
import argparse
import collections
import datetime
import json
import os
import sys
import threading
import time
import numpy as np
import cv2

# indirizzi noti del telefono (IP Webcam), in ordine di prova: il DHCP può
# cambiarli. Con --url si salta la ricerca.
URL_CANDIDATI = ["http://192.168.1.2:8080/photo.jpg",
                 "http://192.168.1.3:8080/photo.jpg"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
# motore QC: resta in src/probe/, importato in sola lettura
PROBE = os.path.join(ROOT, "src", "probe")
sys.path.insert(0, PROBE)
sys.path.insert(0, HERE)
import radar_capture as rc
import radar_analyze as ra
import radar_panels as rp
import radar_gate as rg
import radar_operativa as ro
import radar_ui as ui
import nomi as nm
import telemetria as tm
import differential as df
import build_map as bm
import overlay_grid as og
import read_holes as rh                                # per il "terminale vivo": rh.VERBOSE

WIN = "radar live"
ANW = "analisi"                                       # finestra separata, chiudibile con la X
ARROW_L = (2424832, 65361)                            # freccia sinistra: Win, Linux/Qt
ARROW_R = (2555904, 65363)                            # freccia destra
ARROW_UP = (2490368, 65362)                           # su (alias sinistra: scorri candidati)
ARROW_DOWN = (2621440, 65364)                         # giu (alias destra)
CALIB_PATH = os.path.join(HERE, "radar_calib.json")   # ritaglio board salvato (frazioni)


def _term_key_pressed():
    """True se hai premuto un tasto NEL TERMINALE (non nella finestra); consuma il buffer.
    Serve a INTERROMPERE la rotazione chiavi del VLM mentre cerca. Solo Windows (msvcrt)."""
    try:
        import msvcrt
    except ImportError:
        return False
    hit = False
    while msvcrt.kbhit():
        msvcrt.getch()
        hit = True
    return hit


CLASSI = list(df.DESCR)          # lista UFFICIALE dei componenti (nomi validi per l'input a mano)


def _match_classe(raw):
    """Mappa il testo che scrivi a una classe VALIDA (il nome più lungo prima: 'fotoresistenza'
    prima di 'resistenza'). None se non riconosciuto -> il sistema salta, niente crash."""
    w = raw.lower()
    for cls in sorted(CLASSI, key=len, reverse=True):
        if cls in w:
            return cls
    return None


def _chiedi_classe(reader):
    """Chiede la classe mostrando la lista valida, normalizza e valida. `reader(prompt)->str`
    legge la riga SENZA congelare la finestra. Ritorna una classe valida o None (invio a
    vuoto o nome non riconosciuto = salto)."""
    ui.say("  componenti: " + ", ".join(CLASSI))
    raw = reader("che componente c'e' sulla board? (INVIO = salta) >>")
    if not raw:
        return None
    cls = _match_classe(raw)
    if cls is None:
        ui.say(f"  '{raw}' non riconosciuto -> salto. Scrivi uno dei nomi della lista.")
    else:
        ui.say(f"  -> classe: {cls}")
    return cls


def _scegli_modalita():
    """Menu di avvio in finestra. Ritorna (vlm_on, interruttibile).
    Durante la sessione il tasto V commuta comunque VLM on/off."""
    c = ui.menu(WIN, "CLASSE COMPONENTE: chi la dice?",
                [("1", "VLM + giudizio  - il VLM classifica; SPAZIO in finestra lo interrompe e dici tu"),
                 ("2", "solo VLM        - solo il VLM, nessuna interruzione"),
                 ("3", "solo giudizio   - NIENTE VLM: dici tu ogni componente")])
    if c == "1":
        ui.say("  -> VLM + giudizio");  return True, True
    if c == "2":
        ui.say("  -> solo VLM");        return True, False
    ui.say("  -> solo giudizio (niente VLM)");  return False, False


def _scegli_secondi(default):
    """Intervallo tra gli scatti, scelto in finestra (secondi, anche decimali, INVIO).
    Il motore separa un componente per diff: due componenti nello stesso intervallo
    non vengono distinti e uno step salta. Corto = un pezzo per scatto; lungo =
    rischio di due pezzi in uno scatto. Range 0.3-30 s."""
    sugg = float(default) if default and 0.3 <= float(default) <= 30 else 2.0

    def render(buf):
        h, w = 250, 900
        fr = np.full((h, w, 3), 18, np.uint8)
        cv2.putText(fr, "INTERVALLO tra gli scatti (secondi)", (24, 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2)
        cv2.putText(fr, f"scrivi il numero (es. 1  o  1.5) - INVIO = {sugg:g}s", (28, 88),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        cv2.putText(fr, "REGOLA: UN componente per scatto, aspetta il click", (28, 122),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 220, 120), 1)
        cv2.putText(fr, "corto = piu' scatti scartati ma niente salti; lungo = 2 pezzi in uno scatto",
                    (28, 152), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
        cv2.putText(fr, ">> " + (buf or "") + "_", (24, h - 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        return fr

    while True:
        c = ui.read_line(WIN, render).strip().replace(",", ".")
        if c == "":
            ui.say(f"  -> {sugg:g} secondi (default)")
            return sugg
        try:
            v = float(c)
        except ValueError:
            ui.say(f"  '{c}' non e' un numero, riprova"); continue
        if not (0.3 <= v <= 30):
            ui.say(f"  {v}s fuori range (0.3-30), riprova"); continue
        ui.say(f"  -> {v:g} secondi")
        return v


def _beep_ko():
    """Allarme acustico su KO (allarme senza blocco). Solo Windows."""
    try:
        import winsound
        winsound.Beep(880, 350)
    except Exception:
        pass


def _scegli_fase():
    """Menu iniziale IN FINESTRA: radar puro o operativa su commessa.
    Ritorna l'id commessa (operativa) o None (radar)."""
    c = ui.menu(WIN, "FASE",
                [("1", "radar     - taratura: ragionamento, nessun golden"),
                 ("2", "operativa - montaggio COMMESSA: confronto col golden, allarme su errore")])
    if c == "1":
        ui.say("  -> radar")
        return None
    return _scegli_commessa()


def _scegli_commessa():
    """Scelta della commessa IN FINESTRA dalla lista di golden-data.json."""
    elenco = ro.lista_commesse()
    if not elenco:
        sys.exit("golden-data.json non trovato: impossibile la modalita' operativa")
    righe = [f"{cid}: {nome}" for cid, nome in elenco]

    def render(buf):
        h = 150 + 30 * len(righe)
        fr = np.full((h, 980, 3), 18, np.uint8)
        cv2.putText(fr, "COMMESSA - scrivi l'id e INVIO", (24, 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        for i, r in enumerate(righe):
            cv2.putText(fr, r, (40, 90 + i * 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
        cv2.putText(fr, ">> " + buf.upper() + "_", (24, h - 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
        return fr

    while True:
        c = ui.read_line(WIN, render).upper()
        for cid, nome in elenco:
            if c == cid.upper():
                ui.say(f"  -> {cid}: {nome}")
                return cid
        ui.say(f"  id '{c}' non valido, riprova (es. F1)")


PONTE = os.path.join(ROOT, "data", "ponte")   # stato per la webapp: cartella temporanea,
                                              # vive solo col radar acceso


STOP_FLAG = os.path.join(PONTE, "_stop.flag")  # sentinella scritta dalla webapp quando
                                              # l'operatore passa al collaudo


def _pulisci_ponte():
    """Svuota la cartella-ponte: radar spento = nessuno stato per la webapp
    (i pallini restano neutri). Chiamata all'avvio e alla chiusura della run.
    Toglie anche la sentinella di stop, altrimenti la run dopo morirebbe subito."""
    try:
        if os.path.isdir(PONTE):
            for f in os.listdir(PONTE):
                if f.endswith(".json") or f == "_stop.flag":
                    os.remove(os.path.join(PONTE, f))
    except Exception:
        pass


def _stop_da_webapp():
    """True se la webapp ha chiesto lo stop (POST /api/vlm/stop -> _stop.flag).
    Il montaggio è finito: l'operatore è passato al collaudo, altri scatti sarebbero
    solo rumore. Blindata come la telemetria: un errore di lettura non ferma la run."""
    try:
        return os.path.exists(STOP_FLAG)
    except Exception:
        return False


def _salva_ponte(oper):
    """Copia dello stato nella cartella che la webapp legge (stessa cadenza del
    registro). L'archivio vero resta in data/registri: questo è solo il semaforo."""
    try:
        os.makedirs(PONTE, exist_ok=True)
        with open(os.path.join(PONTE, f"{oper.cid}.json"), "w", encoding="utf-8") as f:
            json.dump(oper.dump(), f, ensure_ascii=False)
    except Exception as e:
        print(f"[ponte NON salvato: {str(e)[:50]}]", flush=True)


def _salva_registro(dati, path):
    """Registro su disco: sempre UTF-8 (un carattere non cp1252, come la Omega del
    trimmer, troncherebbe il JSON), e un errore di salvataggio non ferma la
    sessione: si logga e si riprova al prossimo step."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dati, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[registro NON salvato: {type(e).__name__} {str(e)[:60]}]", flush=True)


def _salva_pixel(oper, nome="pixel.json"):
    """Sidecar POSIZIONALE della sessione: `pixel.json` nella cartella-run, accanto alle foto.
    Una voce per OGNI valutazione (chiave = ordine nello storico, non lo step): mappa completa
    dei fori registrata su quella foto, blob, pin scelti, legscore.

    Sta nella cartella-run e non in data/registri perché senza le foto non serve a
    niente e perché data/registri è versionato: la mappa intera per valutazione pesa
    ~17 KB. JSON compatto, da leggere a macchina.

    Come il registro: un errore di salvataggio si logga e non ferma la sessione."""
    if not getattr(oper, "pixel", None):
        return
    try:
        with open(nome, "w", encoding="utf-8") as f:
            json.dump(oper.pixel, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"[{nome} NON salvato: {type(e).__name__} {str(e)[:60]}]", flush=True)


def _load_calib():
    try:
        return json.load(open(CALIB_PATH, encoding="utf-8"))["crop"]
    except Exception:
        return None


def _save_calib(box):
    json.dump({"crop": [round(v, 4) for v in box]}, open(CALIB_PATH, "w", encoding="utf-8"))


def _auto_bbox(raw):
    """Rettangolo della board (frazioni) da board_rect_mask, o None se non isolabile."""
    m = df.board_rect_mask(raw)
    if m is None:
        return None
    ys, xs = np.nonzero(m)
    if not len(xs):
        return None
    h, w = raw.shape[:2]
    px, py = int(0.02 * w), int(0.03 * h)
    return [max(0, int(xs.min()) - px) / w, max(0, int(ys.min()) - py) / h,
            min(w, int(xs.max()) + px) / w, min(h, int(ys.max()) + py) / h]


def _mappa_analisi(img):
    """La mappa esattamente come la costruirà l'analisi: maschera board, modello, e se
    la board è storta oltre DESKEW_MIN la foto si raddrizza e il modello si rifà
    sulla ruotata (differential.frame(0)). Ritorna (img giudicata, model, markers,
    probs, angolo).

    Il tasto V e il gate d'avvio devono giudicare la stessa immagine dell'analisi:
    con due controlli diversi (mappa non raddrizzata vs raddrizzata) i verdetti
    divergevano su tutte le board oltre 1.3 gradi."""
    m = df.board_rect_mask(img)
    src = cv2.bitwise_and(img, img, mask=m) if m is not None else img
    model, markers = bm.build_model(src, verbose=False)
    ang = df.marker_angle(markers)
    if abs(ang) > df.DESKEW_MIN:
        M = cv2.getRotationMatrix2D((src.shape[1] / 2, src.shape[0] / 2), ang, 1.0)
        src = cv2.warpAffine(src, M, (src.shape[1], src.shape[0]), flags=cv2.INTER_CUBIC)
        model, markers = bm.build_model(src, verbose=False)
    return src, model, markers, df.map_health(model, markers), ang


def _angolo_txt(ang):
    return (f"board {ang:+.2f} gradi"
            + (" (RADDRIZZATA)" if abs(ang) > df.DESKEW_MIN else " (dritta)"))


def _verify_visual(raw, box):
    """VERIFICA VISIVA del ritaglio: ritorna (overlay, messaggio). L'overlay mostra il
    ritaglio REALE con i marker gialli CERCHIATI e i punti della mappa sui fori — così
    si VEDE cosa il sistema ha preso e come ha letto la mappa (non solo un verdetto).
    Se il deskew scatta, l'overlay è la foto RADDRIZZATA: si guarda la stessa immagine
    che l'analisi giudichera', non un'altra."""
    img = rc.rescale2048(rc.apply_calib(raw, box))
    msg = []
    try:
        over, model, markers, probs, ang = _mappa_analisi(img)
        over = over.copy()
        pos = og.positions(model)
        for p in pos.values():
            cv2.circle(over, p, 2, (0, 220, 0), -1)
        for u, v, x, y in markers[:4]:                # i 4 ANGOLI (build_model: corners in testa)
            cv2.circle(over, (int(x), int(y)), 22, (0, 255, 255), 3)
        pitch = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) if (0, 1) in pos and (0, 0) in pos else 0
        msg += [f"passo {pitch}px", _angolo_txt(ang)]
        msg.append("*** MAPPA OK ***" if not probs else f"DEGRADATA: {probs[0][:38]}")
    except BaseException as e:
        over = img.copy()
        msg.append(f"mappa KO: {str(e)[:30]}")
    return over, "   |   ".join(msg)


def calibrate(url, init=None):
    """Fase di calibrazione: uno scatto, l'operatore definisce il ritaglio della board
    (auto board_rect_mask + ritocco col mouse), verifica la mappa (V), salva (INVIO).
    Con `init` (riquadro salvato) la finestra parte da quello: INVIO se va ancora bene.
    Ritorna il rettangolo in frazioni; il ritaglio va applicato IDENTICO a ogni scatto."""
    raw = rc.grab_raw(url)
    if raw is None:
        sys.exit("scatto di calibrazione fallito: telefono raggiungibile?")
    h, w = raw.shape[:2]
    ds = 1000.0 / max(h, w)
    dw, dh = int(w * ds), int(h * ds)
    base = cv2.resize(raw, (dw, dh))
    auto = _auto_bbox(raw)
    st = {"box": (list(init) if init else (list(auto) if auto else [0.05, 0.05, 0.95, 0.95])),
          "drag": None,
          "msg": ("riquadro SALVATO caricato: INVIO se va bene, o ridisegna" if init
                  else "V = verifica mappa   (l'auto e' gia' proposto)")}

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            st["drag"] = (x, y)
        elif ev == cv2.EVENT_MOUSEMOVE and st["drag"] is not None:
            x0, y0 = st["drag"]
            st["box"] = [min(x0, x) / dw, min(y0, y) / dh, max(x0, x) / dw, max(y0, y) / dh]
        elif ev == cv2.EVENT_LBUTTONUP:
            st["drag"] = None

    win = "calibrazione"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        disp = base.copy()
        b = st["box"]
        cv2.rectangle(disp, (int(b[0] * dw), int(b[1] * dh)),
                      (int(b[2] * dw), int(b[3] * dh)), (0, 255, 0), 2)
        for i, t in enumerate(["CALIBRAZIONE - ritaglio board",
                               "trascina col mouse = ridisegna | R = auto | V = verifica | INVIO = salva | Q = esci",
                               st["msg"]]):
            cv2.putText(disp, t, (14, 26 + i * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(disp, t, (14, 26 + i * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
        cv2.imshow(win, disp)
        k = cv2.waitKey(30) & 0xFF
        if k in (13, 10):
            break
        elif k == ord('r') and auto:
            st["box"] = list(auto); st["msg"] = "ritaglio auto ripristinato"
        elif k == ord('v'):
            over, st["msg"] = _verify_visual(raw, st["box"])   # VERIFICA VISIVA
            vv = cv2.resize(over, (1200, int(over.shape[0] * 1200 / over.shape[1])))
            cv2.rectangle(vv, (0, 0), (vv.shape[1], 40), (0, 0, 0), -1)
            cv2.putText(vv, st["msg"], (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow("verifica: ritaglio + marker + mappa", vv)
        elif k == ord('q'):
            cv2.destroyWindow(win); sys.exit("calibrazione annullata")
    cv2.destroyAllWindows()
    _save_calib(st["box"])
    print(f"[calibrazione salvata: crop={[round(v, 3) for v in st['box']]} -> {CALIB_PATH}]")
    return st["box"]


def _map_ok(img):
    """La board vuota deve produrre una mappa sana. Ritorna (ok, messaggio).
    Un ritaglio sbagliato può far fallire build_model con sys.exit: qui si cattura e
    si trasforma in messaggio, il programma non muore su uno scatto storto."""
    try:
        _, model, markers, probs, ang = _mappa_analisi(img)
    except BaseException as e:
        return False, f"MAPPA NON COSTRUIBILE ({str(e)[:44]}) - rifai la calibrazione (--recalib)"
    if probs:
        storta = abs(ang) > df.DESKEW_MIN
        return False, (f"MAPPA DEGRADATA ({_angolo_txt(ang)}): " + "; ".join(probs)
                       + (" -- la board e' storta: RADDRIZZALA e ri-scatta (R)" if storta else ""))
    pos = og.positions(model)
    pitch = abs(pos[(0, 1)][0] - pos[(0, 0)][0]) if (0, 1) in pos and (0, 0) in pos else 0
    warn = "" if 27 <= pitch <= 33 else f" [ATTENZIONE passo={pitch}px lontano da 30: QC_CLS forse da ricalibrare]"
    return True, f"mappa OK, passo {pitch}px, {_angolo_txt(ang)}{warn}"

def _live_view(img, entries, next_in, hit=None, attesi=None, banner=None, input_line=None,
               prompt=None):
    """Vista live a finestra unica: frame in alto, console sotto (eco del terminale +
    input in finestra), colonna log stretta a destra.
    Righe log CLICCABILI via `hit`. Operativa: `attesi` = fori golden dello step
    corrente ("monta qui"), `banner` = esito ultima rilevazione (testo, colore).
    `input_line`/`prompt`: riga di input attiva mostrata nella console."""
    H, CONS_H, SIDE_W = 820, 300, 300
    img_h = H - CONS_H
    s = min(img_h / img.shape[0], 1040.0 / img.shape[1])
    view = np.zeros((H, int(img.shape[1] * s), 3), np.uint8)
    view[:int(img.shape[0] * s)] = cv2.resize(img, (view.shape[1], int(img.shape[0] * s)))
    if attesi:                                        # operativa: "monta qui" (fori golden step corrente)
        for (x, y) in attesi:
            cv2.circle(view, (int(x * s), int(y * s)), max(6, int(10 * s)), (0, 255, 255), 2)
    if banner:                                        # operativa: righe guida/esito sopra la console
        rows_b = banner if isinstance(banner, list) else [banner]
        h0 = img_h - 40 * len(rows_b) - 6
        cv2.rectangle(view, (0, h0), (view.shape[1], img_h), (0, 0, 0), -1)
        for i, (txt, col) in enumerate(rows_b):
            cv2.putText(view, txt, (12, h0 + 30 + i * 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    view[img_h:] = ui.console_pane(view.shape[1], CONS_H, input_line=input_line, prompt=prompt)
    side = np.zeros((H, SIDE_W, 3), np.uint8)
    cv2.putText(side, "LOG", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    t = "scatto..." if next_in is None else f"prossimo tra {max(0.0, next_in):.1f}s"
    cv2.putText(side, t, (12, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.putText(side, "V=VLM  R=riapri  G=vai a step  F=forza  Q=esci", (12, H - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (140, 140, 140), 1)
    rows = []
    for i, e in enumerate(list(entries)[-27:]):
        m = e["msg"]
        col = ((0, 0, 255) if "KO" in m else
               (0, 220, 0) if ("ANALIZZO" in m or "classe" in m or "OK" in m) else
               (0, 140, 255) if ("MOVIMENTO" in m or "NON" in m) else (210, 210, 210))
        y = 88 + i * 24
        cv2.putText(side, f"{e['t']} {m}"[:44], (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        rows.append((y - 16, y + 8, e))
    if hit is not None:
        hit["x0"] = view.shape[1]
        hit["rows"] = rows
    return np.hstack([view, side])


def _shot0_window(url, calib, crop):
    """Scatto 0 (baseline board VUOTA) a FINESTRA, non console: mostra l'anteprima già
    ritagliata, INVIO scatta, R ri-scatta, Q esce. Ritorna lo scatto o None."""
    win = "scatto 0 - board VUOTA (baseline)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    while True:
        img = rc.grab(url, calib=calib, crop_frac=crop)
        if img is None:
            print("[scatto 0: telefono non raggiungibile]")
            if (cv2.waitKey(600) & 0xFF) == ord('q'):
                cv2.destroyWindow(win); return None
            continue
        disp = img.copy()
        for i, t in enumerate(["SCATTO 0 = board VUOTA (baseline del confronto)",
                               "INVIO = scatta   |   R = ri-scatta   |   C = ricalibra inquadratura   |   Q = esci"]):
            cv2.putText(disp, t, (14, 36 + i * 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(disp, t, (14, 36 + i * 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow(win, disp)
        k = cv2.waitKey(0) & 0xFF                     # blocca: niente polling continuo del telefono
        if k in (13, 10):
            cv2.destroyWindow(win); return img
        if k == ord('c'):                             # telefono spostato: nuovo riquadro, senza uscire
            cv2.destroyWindow(win); return "recalib"
        if k == ord('q'):
            cv2.destroyWindow(win); return None
        # qualsiasi altro tasto (R): rigrab


def _scatto0(url, calib, crop):
    """Scatto 0 con ricalibrazione AL VOLO (tasto C): se il telefono è stato spostato
    si ridisegna il riquadro e si torna allo scatto, senza riavviare il programma.
    Ritorna (img0 | None, calib aggiornato)."""
    while True:
        r = _shot0_window(url, calib, crop)
        if isinstance(r, str):                        # "recalib"
            calib = calibrate(url, init=calib)
            continue
        return r, calib


def _trova_url(url=None):
    """Indirizzo del telefono. Con --url si usa quello e basta; senza, si prova ogni
    candidato con uno scatto grezzo e si tiene il primo che risponde (l'IP assegnato
    dal DHCP può cambiare). Nessuno risponde -> il primo, così l'errore lo dice la
    calibrazione con il suo messaggio."""
    if url:
        return url
    for u in URL_CANDIDATI:
        if rc.grab_raw(u, timeout=3) is not None:
            print(f"[telefono] {u}")
            return u
    print(f"[telefono] nessun candidato risponde, provo {URL_CANDIDATI[0]}")
    return URL_CANDIDATI[0]


def run(url, sec, runid, replay, novlm=False, force_cls=None, crop=0.0, forcemap=False,
        recalib=False, salvascarti=False, commessa=None):
    calib = None
    tel = None                            # telemetria: creata dopo la mappa (solo live)
    registro_path = None                  # dove si salva il registro di sessione (solo live)
    vlm0, interrut0 = (not novlm), False   # modalità di default (replay o ripiego)
    if replay:
        rundir = replay
        oper = ro.Operativa(commessa) if commessa else None   # collaudo operativa su run salvato
        frames = sorted(int(f[:-5]) for f in os.listdir(rundir)
                        if f.endswith(".jpeg") and f[:-5].isdigit())
    else:
        cid_op = commessa or _scegli_fase()    # menu FASE (radar / operativa+commessa)
        oper = ro.Operativa(cid_op) if cid_op else None
        if oper:
            # Nome run: <commessa>.<n>, es. D1.3 (v. nomi.py). Si calcola prima di
            # creare la cartella: `nuovo` conta quelle già su disco.
            runid = nm.nuovo(os.path.join(ROOT, "data", "runs"), oper.cid)
        vlm0, interrut0 = _scegli_modalita()   # menu di avvio
        sec = _scegli_secondi(sec)             # intervallo tra gli scatti, scelto in finestra
        # Calibrazione: il riquadro si conferma a ogni sessione. La finestra parte dal
        # riquadro salvato, INVIO se va ancora bene.
        calib = calibrate(url, init=_load_calib())
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        # con commessa il nome è già univoco (D1.3); senza commessa (fase radar) resta
        # il timestamp, che lì è l'unica chiave che distingue una sessione dall'altra
        nome = runid if oper else f"{runid}_{stamp}"
        rundir = os.path.join(ROOT, "data", "runs", nome)
        rundir = os.path.abspath(rundir)
        os.makedirs(rundir, exist_ok=True)
        dati_dir = os.path.join(ROOT, "data", "registri")
        os.makedirs(dati_dir, exist_ok=True)
        registro_path = os.path.join(dati_dir, f"{nome}_registro.json")
        _pulisci_ponte()               # ponte temporaneo: si parte sempre puliti
        img0, calib = _scatto0(url, calib, crop)      # scatto 0 a finestra (INVIO, C=ricalibra)
        if img0 is None:
            sys.exit("scatto 0 annullato / telefono non raggiungibile.")
        cv2.imwrite(os.path.join(rundir, "0.jpeg"), img0)
        frames = [0]

    old = os.getcwd()
    os.chdir(rundir)
    try:
        img0 = cv2.imread("0.jpeg")
        ok, msg = _map_ok(img0)
        ui.say(f"[avvio] {msg}")
        # Conferma solo su mappa degradata: con mappa OK si parte subito. Niente
        # rilancio con --forcemap: si decide qui vedendo l'overlay.
        while not replay and not ok:
            over, mmsg = _verify_visual(img0, None)
            vv = cv2.resize(over, (1200, int(over.shape[0] * 1200 / over.shape[1])))
            cv2.rectangle(vv, (0, 0), (vv.shape[1], 42), (0, 0, 0), -1)
            cv2.putText(vv, f"MAPPA DEGRADATA:  {mmsg}"[:110], (10, 29),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 140, 255), 2)
            hint = "INVIO = forza (difetto minore)   |   R = ri-scatta   |   C = ricalibra   |   Q = esci"
            cv2.rectangle(vv, (0, vv.shape[0] - 34), (vv.shape[1], vv.shape[0]), (0, 0, 0), -1)
            cv2.putText(vv, hint, (10, vv.shape[0] - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.imshow("mappa scatto 0 DEGRADATA", vv)
            kk = cv2.waitKey(0) & 0xFF
            cv2.destroyWindow("mappa scatto 0 DEGRADATA")
            if kk in (13, 10):                        # forza: come --forcemap, deciso a video
                forcemap = True
                break
            if kk == ord('q'):
                cv2.destroyAllWindows(); return
            if kk in (ord('r'), ord('c')):            # RI-SCATTA vero (C = prima ricalibra)
                if kk == ord('c'):
                    calib = calibrate(url, init=calib)
                nuovo, calib = _scatto0(url, calib, crop)
                if nuovo is None:
                    cv2.destroyAllWindows(); return
                img0 = nuovo
                cv2.imwrite("0.jpeg", img0)
                ok, msg = _map_ok(img0)
                print(f"[ri-scatto] {msg}")
        if not ok and not forcemap:
            sys.exit("run interrotto: mappa non sana. Rilancia con --recalib (ritaglio migliore) "
                     "oppure --forcemap se il difetto e' minore (es. 1 foro rail).")
        df.FORCEMAP = forcemap        # con --forcemap frame(0) non si ferma su mappa degradata minore
        # Telemetria: osservatore blindato, solo live (in replay la cartella-run è
        # storica e non va sporcata). Ogni chiamata è no-op su errore.
        if not replay:
            tel = tm.Telemetria(rundir, cid=(oper.cid if oper else None))
            tel.fase_mappa(img0)
        rh.VERBOSE = not replay       # terminale vivo: i passi della rotazione chiavi Gemini
        sess = ra.Session()
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        if oper is not None:                     # titolo coerente con la fase scelta
            cv2.setWindowTitle(WIN, f"OPERATIVA {oper.cid} - {oper.golden['name']}")
        # vlm_on: True = classe dal VLM; False = la chiede all'operatore. Tasto V commuta.
        # interruptible: True = un tasto nel terminale ferma la rotazione VLM (modo 1).
        state = {"sel": None, "region": [], "posx": {}, "res": None,
                 "vlm_on": vlm0, "interruptible": interrut0, "banner": None,
                 "inp": None, "inp_prompt": None}

        def _abort_vlm():
            """Stop del VLM mentre cerca: SPAZIO nella finestra o un tasto nel terminale
            (ripiego). Pompa la GUI, così la finestra resta viva. In chiusura forzata
            interrompe comunque: aspettare l'ultima rotazione chiavi può costare minuti
            con l'operatore che ha già finito di montare."""
            if state.get("chiudo"):
                return True
            if not state["interruptible"]:
                return False
            if (cv2.waitKey(1) & 0xFF) == 32:
                return True
            return _term_key_pressed()
        rh.ABORT = _abort_vlm if not replay else None

        n = frames[-1]
        prev_raw = img0
        last_valid = img0
        replay_i = 1
        log = collections.deque(maxlen=40)
        state_view = {"img": img0, "nshot": 0, "next_in": None}
        # Cattura in parallelo. Con scatto e analisi nello stesso giro, durante una
        # chiamata VLM lunga (decine di secondi) nessuno fotografa la board e più
        # montaggi finiscono in un frame solo: un solo step consumato, run sfasata.
        # Un thread scatta e basta (gate + k.jpeg + coda); analisi, valutazione e GUI
        # restano nel thread principale: il motore non è toccato da due thread e
        # nessun frame va perso.
        coda = collections.deque()
        coda_lock = threading.Lock()
        stop_cattura = threading.Event()
        hit = {"x0": 10**9, "rows": []}
        posx0 = {**sess.pos0, **sess.rpos0}

        def _lv(next_in):
            """Vista live con gli extra operativi: overlay attesi (guida sulla mappa dello
            scatto 0; la misura vera usa la mappa registrata del frame corrente) + banner
            a 2 righe: 'MONTA step N' sempre, ultimo verdetto sotto (quando c'è)."""
            rows = None
            if oper:
                st_ = oper.step_atteso()
                guida = (f"MONTA step {oper.step_i + 1}/{len(oper.build)}: " + ro._ascii(st_["el"])
                         if st_ else "COMMESSA COMPLETATA")
                rows = [(guida, (0, 255, 255))]
                if state["banner"]:                       # tupla singola o lista di righe
                    b = state["banner"]
                    rows.extend(b if isinstance(b, list) else [b])
            arretrati = len(coda)
            if arretrati:                                 # l'analisi è indietro: si vede
                rows = (rows or []) + [(f"in coda: {arretrati} foto da analizzare",
                                        (0, 200, 255))]
            return _live_view(state_view["img"], log, next_in, hit,
                              attesi=(oper.attesi_px(posx0) if oper else None),
                              banner=rows, input_line=state.get("inp"),
                              prompt=state.get("inp_prompt"))

        def _oper_ctx():
            """Contesto operativa per il pannello: record dell'ultima valutazione (dallo
            storico: vale anche per le correzioni)."""
            if oper and oper.storico:
                return {"rec": oper.storico[-1]}
            return {"rec": None}

        def _oper_txt():
            """Riga operativa per il pannello analisi (ultimo verdetto registrato)."""
            if oper and oper.records and oper.records[-1].get("step") is not None:
                r = oper.records[-1]
                return ro._ascii(f"OPERATIVA step {r['step']}: atteso {r['atteso']['el']} "
                                 f"-> {r['verdetto']}")
            return None
        if not replay and salvascarti:
            os.makedirs("scatti", exist_ok=True)   # scarti su disco solo con --salvascarti

        def L(msg, img=None):
            e = {"t": datetime.datetime.now().strftime("%H:%M:%S"), "msg": msg, "img": img}
            log.append(e)
            ui.say(f"[{e['t']}] {msg}")           # eco anche nella console in finestra

        def _win_open(name):
            try:
                return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) >= 1
            except cv2.error:
                return False

        def on_mouse_an(ev, x, y, flags, param):
            """Click nella finestra ANALISI = seleziona il foro più vicino."""
            if ev == cv2.EVENT_LBUTTONDOWN and state["region"]:
                posx = state["posx"]
                near = min(state["region"], key=lambda uv: (posx[uv][0]-x)**2+(posx[uv][1]-y)**2)
                state["sel"] = near
                show_analysis()

        def show_analysis(force=False):
            """Finestra analisi separata: si apre alla rilevazione (force), si riaggiorna
            solo se ancora aperta; l'operatore la chiude con la X quando vuole. Il radar
            live resta sempre la finestra principale."""
            if state["res"] is None:
                return
            if not force and not _win_open(ANW):
                return
            try:
                panel = rp.render(state["res"], state["sel"], oper_txt=_oper_txt(), **_oper_ctx())
                if not _win_open(ANW):
                    cv2.namedWindow(ANW, cv2.WINDOW_NORMAL)
                    cv2.setMouseCallback(ANW, on_mouse_an)
                cv2.imshow(ANW, panel)
                ncand = len(state["region"])
                cv2.setWindowTitle(ANW, f"ANALISI foto {state['res']['k']}  "
                                        f"(CLICK un foro o frecce = scorri {ncand} candidati, "
                                        "S = salva, X = chiudi)")
            except Exception as e:
                L(f"finestra analisi non disegnabile ({str(e)[:40]}) - radar continua")

        def wait_tick(seconds):
            """Attesa a tick da 100ms col countdown in finestra. Gestisce anche i tasti
            dell'analisi (frecce = cambia foro, S = salva). True se Q premuto."""
            t0 = time.time()
            while True:
                left = seconds - (time.time() - t0)
                if left <= 0:
                    return False
                # il countdown vero è quello del catturatore: `left` qui è solo il tick
                cv2.imshow(WIN, _lv(state_view.get("next_in") if not replay else left))
                key = cv2.waitKeyEx(100)
                _process_log_click()                      # click sul log: apertura reattiva
                low = key & 0xFF
                if key != -1 and low != ord('r') and state.get("r_arm"):
                    state["r_arm"] = False                # qualunque altro tasto disarma
                    state["banner"] = None
                    L("riapertura annullata")
                if low == ord('q'):
                    return True
                elif low == ord('v'):                     # commuta VLM auto <-> classe a mano
                    state["vlm_on"] = not state["vlm_on"]
                    L("VLM ON (classe dal VLM)" if state["vlm_on"]
                      else "VLM OFF: la classe la chiedo a te in finestra")
                elif low in (ord('1'), ord('2'), ord('3')) and tel and state.get("gt_ord"):
                    # GT live: giudizio a caldo sull'ultima valutazione, mentre
                    # l'operatore ricorda cosa ha fatto. Finisce nell'Excel.
                    tag = {ord('1'): "giusto", ord('2'): "errore_vero",
                           ord('3'): "falso_allarme"}[low]
                    tel.gt_live(state["gt_ord"], tag)
                    L(f"GT live v{state['gt_ord']}: {tag}")
                elif (key in ARROW_L or key in ARROW_UP) and state["region"]:
                    i = state["region"].index(state["sel"]) if state["sel"] in state["region"] else 0
                    state["sel"] = state["region"][(i - 1) % len(state["region"])]
                    show_analysis()
                elif (key in ARROW_R or key in ARROW_DOWN) and state["region"]:
                    i = state["region"].index(state["sel"]) if state["sel"] in state["region"] else 0
                    state["sel"] = state["region"][(i + 1) % len(state["region"])]
                    show_analysis()
                elif low == ord('s') and state["res"] is not None:
                    fn = f"radar_{state['res']['k']}.png"
                    try:
                        cv2.imwrite(fn, rp.render(state["res"], state["sel"], oper_txt=_oper_txt(), **_oper_ctx()))
                        L(f"salvato {fn}", img=fn)
                    except Exception as e:
                        L(f"salvataggio pannello fallito ({str(e)[:40]})")
                elif low == ord('g') and oper is not None:
                    # Resync: un frame consumato male desincronizza il contatore e
                    # tutto va in KO CLASSE a valle. L'operatore dichiara lo step fisico
                    # corrente e il sistema si riallinea, senza riavviare.
                    tot = len(oper.build)

                    def _rend(buf):
                        state["inp"], state["inp_prompt"] = buf, f"su quale step sei? (1-{tot}, INVIO annulla) >>"
                        return _lv(None)
                    raw = ui.read_line(WIN, _rend, "")
                    state["inp"] = state["inp_prompt"] = None
                    if raw.isdigit() and 1 <= int(raw) <= tot:
                        oper.step_i = int(raw) - 1
                        state["banner"] = None
                        if registro_path:
                            _salva_registro(oper.dump(), registro_path)
                            _salva_ponte(oper)
                            _salva_pixel(oper)                 # sidecar posizionale, stessa cadenza del registro
                        L(f"RESYNC: ora sei allo step {oper.step_i + 1} "
                          f"({ro._ascii(oper.step_atteso()['el'])})")
                    else:
                        L("resync annullato")
                elif low == ord('f'):
                    # Forza analisi (es. gate sordo su un jumper corto): il prossimo
                    # scatto passa il gate comunque. Esce subito dal countdown.
                    state["force"] = True
                    L("FORZATO: il prossimo scatto viene analizzato comunque")
                    return False
                elif low == ord('r') and oper is not None and oper.records:
                    # Riapri step (workflow correzione) con doppia conferma: una R
                    # premuta per errore cancellerebbe un verdetto in silenzio, con
                    # slittamento a cascata degli step successivi. R arma, secondo R esegue.
                    if not state.get("r_arm"):
                        state["r_arm"] = True
                        stp = oper.records[-1].get("step") or "?"
                        state["banner"] = (f"RIAPRIRE step {stp}? premi ancora R (altro tasto = annulla)",
                                           (0, 200, 255))
                    else:
                        state["r_arm"] = False
                        last = oper.records.pop()
                        if last.get("step") is not None:
                            oper.step_i -= 1
                        if registro_path:
                            _salva_registro(oper.dump(), registro_path)
                            _salva_ponte(oper)
                            _salva_pixel(oper)                 # sidecar posizionale, stessa cadenza del registro
                        state["banner"] = (f"STEP {oper.step_i + 1} RIAPERTO - in attesa della correzione",
                                           (0, 200, 255))
                        L(f"step {oper.step_i + 1} RIAPERTO: sistemalo, il prossimo scatto lo rivaluta")

        def on_mouse(ev, x, y, flags, param):
            """Click sul LOG = PRENOTA l'apertura (istantaneo, niente lavoro pesante qui:
            il callback gira sul thread della GUI). Ci pensa il loop principale."""
            if ev != cv2.EVENT_LBUTTONDOWN or x < hit["x0"]:
                return
            for y0, y1, e in hit["rows"]:
                if y0 <= y <= y1 and e.get("img"):
                    hit["open"] = e
                    return
        cv2.setMouseCallback(WIN, on_mouse)

        def _process_log_click():
            """Apre la foto prenotata dal click: lettura + RIDUZIONE a ~1150px (finestra
            leggera e reattiva, non il PNG intero da 2000px) + porta davanti."""
            e = hit.pop("open", None)
            if not e:
                return
            im = cv2.imread(e["img"])
            if im is None:
                return
            s = min(1.0, 1150.0 / im.shape[1])
            if s < 1.0:
                im = cv2.resize(im, (int(im.shape[1] * s), int(im.shape[0] * s)),
                                interpolation=cv2.INTER_AREA)
            im = cv2.copyMakeBorder(im, 40, 0, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            cv2.putText(im, f"{e['t']}  {e['msg']}"[:110], (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            w = "foto dal log"
            cv2.namedWindow(w, cv2.WINDOW_NORMAL)
            cv2.imshow(w, im)
            cv2.setWindowTitle(w, f"{e['t']}  {e['msg'][:44]}")
            try:
                cv2.setWindowProperty(w, cv2.WND_PROP_TOPMOST, 1)
            except cv2.error:
                pass

        def _ana(k, nv, fc):
            """analyze BLINDATO: qualunque errore (classe strana, geometria impossibile) viene
            LOGGATO e il radar prosegue. VlmInterrupted (BaseException) passa oltre di suo."""
            try:
                return sess.analyze(k, novlm=nv, force_cls=fc)
            except Exception as e:
                L(f"foto {k}: ERRORE analisi ({type(e).__name__}: {str(e)[:50]}) - salto")
                return None

        def _cattura_loop():
            """Thread di cattura: scatta a intervallo fisso, applica il gate, salva
            k.jpeg e mette il frame in coda. Non tocca né il motore (`sess`, `oper`) né
            la GUI: fa solo cattura, gate e disco. La telemetria è l'unico oggetto
            condiviso e la sua `scatto` è sotto lock."""
            nonlocal n, prev_raw, last_valid
            while not stop_cattura.is_set():
                t0 = time.time()
                if _stop_da_webapp():          # collaudo iniziato: qui si vede sempre, anche
                    _ferma_cattura("STOP dalla webapp (collaudo iniziato)")   # se il
                    break                      # thread principale è dentro un'analisi lunga
                cur = rc.grab(url, calib=calib, crop_frac=crop)
                if cur is None:
                    L("telefono NON raggiungibile")
                else:
                    state_view["nshot"] += 1
                    state_view["img"] = cur
                    nshot = state_view["nshot"]
                    ora = datetime.datetime.now().strftime("%H:%M:%S")
                    sp = None
                    if salvascarti:                   # scarti su disco SOLO se richiesto
                        sp = f"scatti/{nshot:04d}.jpeg"
                        cv2.imwrite(sp, cur)
                    d_prev = rg.frame_diff(cur, prev_raw)
                    gate_ok = rg.should_analyze(cur, prev_raw, last_valid)
                    nov = (rg.novelty(cur, last_valid) if last_valid is not None else None)
                    forced = state.pop("force", False)
                    gate_missed = forced and not gate_ok   # F ha salvato uno step che il
                    validate = gate_ok or forced           # gate avrebbe perso: va nei dati
                    prev_raw = cur
                    if not validate:
                        # il terzo motivo si chiede solo se gli altri due non spiegano
                        # già lo scarto: è l'unico che costa un passaggio a piena
                        # risoluzione
                        if d_prev >= rg.STILL:
                            why, motivo = f"MOVIMENTO d={d_prev:.3f}", "movimento"
                        elif rg.mano(cur):
                            why, motivo = "mano nel campo", "mano"
                        else:
                            why, motivo = "ferma ma uguale", "ferma_ma_uguale"
                        if tel:
                            tel.scatto(nshot, "SCARTO_GATE", motivo=motivo,
                                       d_prev=d_prev, novelty=nov, img=cur, ora=ora)
                        L(f"#{nshot} scarto: {why}", img=sp)
                    else:
                        n += 1
                        k = n
                        cv2.imwrite(f"{k}.jpeg", cur)
                        last_valid = cur
                        with coda_lock:
                            coda.append({"k": k, "img": cur, "nshot": nshot, "ora": ora,
                                         "d_prev": d_prev, "nov": nov,
                                         "gate_missed": gate_missed})
                            arretrati = len(coda)
                        L(f"#{nshot} VALIDA -> foto {k} in coda"
                          + (f" ({arretrati} da analizzare)" if arretrati > 1 else ""),
                          img=f"{k}.jpeg")
                while not stop_cattura.is_set():           # attesa fino al prossimo tick
                    left = sec - (time.time() - t0)
                    state_view["next_in"] = max(0.0, left)
                    if left <= 0:
                        break
                    time.sleep(min(0.1, left))
            state_view["next_in"] = None

        def _ferma_cattura(motivo):
            """Q (o stop dalla webapp): la cattura si ferma subito, ma le foto già in
            coda si analizzano lo stesso: sono step montati davvero. Un secondo Q
            durante lo smaltimento chiude e basta."""
            if stop_cattura.is_set():                      # già in chiusura: esci davvero
                state["chiudo"] = True
                L("chiusura forzata: le foto rimaste in coda NON verranno analizzate")
                return
            stop_cattura.set()
            with coda_lock:
                restano = len(coda)
            if restano:
                L(f"{motivo}: restano {restano} foto in coda - le analizzo "
                  f"(Q = chiudi subito e buttale)")
                state["banner"] = [(f"CHIUSURA: {restano} foto ancora da analizzare",
                                    (0, 200, 255)),
                                   ("Q = chiudi subito (quelle foto non verranno giudicate)",
                                    (150, 150, 150))]
            else:
                state["chiudo"] = True
                L(f"{motivo}: chiudo")

        def _prossimo_dalla_coda():
            """Prossimo frame validato, tenendo viva la finestra (tasti, log, analisi).
            None = fine run. Sostituisce lo scatto in linea: qui non si aspetta più il
            telefono, ci pensa il thread di cattura."""
            while True:
                if wait_tick(0.05):                        # un tick di UI: Q e tasti vivi
                    _ferma_cattura("Q")
                if not stop_cattura.is_set() and _stop_da_webapp():
                    print("[stop] la webapp e' passata al collaudo: nessun altro scatto.",
                          flush=True)
                    _ferma_cattura("STOP dalla webapp (collaudo iniziato)")
                if state.get("chiudo"):                    # chiusura forzata: si esce subito
                    return None
                with coda_lock:                            # i controlli stanno prima del
                    if coda:                               # prelievo: con la coda sempre
                        return coda.popleft()              # piena non verrebbero mai eseguiti
                if stop_cattura.is_set():                  # coda svuotata dopo la chiusura
                    return None

        def _read_line_live(prompt):
            """Riga digitata nella finestra: la console sotto l'immagine mostra prompt e
            testo, i tasti arrivano da cv2 (finestra a fuoco).
            INVIO conferma, ESC annulla. I click sul log restano attivi."""
            state["inp"], state["inp_prompt"] = "", prompt
            try:
                while True:
                    cv2.imshow(WIN, _lv(None))
                    k = cv2.waitKeyEx(30)
                    _process_log_click()              # i click sul log funzionano anche qui
                    if k == -1:
                        continue
                    low = k & 0xFF
                    if low in (13, 10):
                        return state["inp"].strip()
                    if low == 27:
                        return ""
                    if low in (8, 127):
                        state["inp"] = state["inp"][:-1]
                    elif 32 <= low < 127:
                        state["inp"] += chr(low)
            finally:
                state["inp"], state["inp_prompt"] = None, None

        if not replay:                     # la cattura parte e da qui non si ferma più
            threading.Thread(target=_cattura_loop, name="cattura", daemon=True).start()
            L(f"cattura avviata: uno scatto ogni {sec:g}s, l'analisi non la ferma")

        while True:
            if replay:
                if replay_i >= len(frames):
                    print("[replay finito]"); break
                k = frames[replay_i]; replay_i += 1
                cur = cv2.imread(f"{k}.jpeg")
                validate = True
            else:
                dato = _prossimo_dalla_coda()             # scatto e gate stanno nel thread
                if dato is None:
                    break                                 # il finally fa GT + Excel come prima
                _process_log_click()                      # click arrivato durante l'analisi
                k = dato["k"]
                cur = dato["img"]
                d_prev = dato["d_prev"]
                nov = dato["nov"]
                gate_missed = dato["gate_missed"]
                L(f"foto {k}: ANALIZZO (scatto #{dato['nshot']})", img=f"{k}.jpeg")

            if replay:
                gate_missed = False
                fc = force_cls
                if oper is not None and fc is None and novlm:
                    # collaudo operativa --novlm: la classe viene dal golden (fit_pins gira
                    # offline e resta cieco sul DOVE: nessun foro golden gli arriva)
                    st_ = oper.step_atteso()
                    fc = df.KIND2CLS.get(st_["kind"], st_["kind"]) if st_ else None
                res = _ana(k, novlm, fc)
            elif not state["vlm_on"]:
                # Manuale (VLM scavalcato): prima si rileva il blob senza VLM; se c'è un
                # pezzo si chiede la classe all'operatore (con la lista) e si rifà con
                # quella: geometria/pin senza VLM.
                res = _ana(k, True, None)
                if res is not None and res["blob_box"] is not None:
                    cls_man = _chiedi_classe(_read_line_live)
                    if cls_man:
                        res = _ana(k, True, cls_man)
            else:
                try:
                    res = _ana(k, novlm, force_cls)
                except rh.VlmInterrupted:
                    if state.get("chiudo"):        # chiusura: non si chiede più niente
                        L(f"chiusura: foto {k} non analizzata")
                        break
                    # SPAZIO in finestra (o tasto nel terminale) mentre cercava le chiavi:
                    # stop VLM, classe dall'operatore (dalla lista), geometria/pin lo stesso.
                    L("VLM interrotto da te -> dimmi la classe")
                    cls_man = _chiedi_classe(_read_line_live)
                    res = _ana(k, True, cls_man)
            if res is None:                                  # errore in analisi: già loggato, prosegui
                if replay:
                    continue
                if tel:
                    tel.scatto(dato["nshot"], "ERRORE_ANALISI", img=cur, ora=dato["ora"])
                continue
            if not replay and res["blob_box"] is None:
                # Il gate localizzato può accendersi su un micro-spostamento della board;
                # qui l'analisi ha già allineato e non ha trovato nessuna macchia: niente
                # componente nuovo, si logga e si va avanti, senza pannello.
                # Eccezione: con un KO aperto lo spostamento di +-1 colonna non produce
                # blob (sagome sovrapposte) ma i fori dell'attesa parlano: il frame è la
                # correzione, si giudica.
                if (oper is not None and not res.get("frame_ko")
                        and oper.correzione_in_attesa(res) is not None):
                    L(f"#{state_view['nshot']} niente blob MA correzione attesa: giudico")
                elif (oper is not None and not res.get("frame_ko")
                        and oper.attesi_caldi(res)):
                    # pezzo bianco/translucido (rgb): niente blob ma i fori attesi
                    # dello step parlano sopra la soglia di classe
                    L(f"#{state_view['nshot']} niente blob MA fori attesi caldi: giudico")
                else:
                    if tel:                                 # scarto guardie vs semplice no-blob
                        tel.scatto(dato["nshot"],
                                   "SCARTO_GUARDIA" if res.get("frame_ko") else "NO_BLOB",
                                   motivo=res.get("frame_ko") or "nessun blob",
                                   d_prev=d_prev, novelty=nov,
                                   allineamento=res.get("allineamento"),
                                   caldi=res.get("caldi"), img=cur, ora=dato["ora"])
                    L(f"#{dato['nshot']} nessuna novita' (assestamento) {res['note']}",
                      img=f"{k}.jpeg")
                    continue
            state["region"] = res["region"]
            state["posx"] = {**res["pos"], **res["rpos"]}
            state["sel"] = res["pins"][0] if res["pins"] else (res["region"][0] if res["region"] else None)
            state["res"] = res
            if not replay:
                v = None
                if (oper is not None and oper.assestamento_sospetto(res)
                        and oper.correzione_in_attesa(res) is None):
                    # Blob senza pezzo nuovo (es. bottone mosso): step non consumato.
                    # Ma se la zona tocca una correzione attesa (KO aperto) non è un
                    # assestamento: è l'operatore che sistema, passa a valuta.
                    stp = oper.step_i + 1
                    if tel:
                        tel.scatto(dato["nshot"], "ASSESTAMENTO",
                                   motivo=f"step {stp} resta aperto",
                                   d_prev=d_prev, novelty=nov,
                                   allineamento=res.get("allineamento"),
                                   caldi=res.get("caldi"), img=cur, ora=dato["ora"])
                    L(f"foto {k}: assestamento sospetto (blob ma zero pin, attesi muti) "
                      f"- step {stp} resta APERTO", img=f"{k}.jpeg")
                    state["banner"] = (f"ASSESTAMENTO? step {stp} resta in attesa", (0, 200, 255))
                    continue
                if tel:                                      # scatto promosso: i suoi numeri
                    tel.scatto(dato["nshot"], "ANALIZZATO", motivo=f"foto {k}",
                               d_prev=d_prev, novelty=nov,
                               allineamento=res.get("allineamento"),
                               caldi=res.get("caldi"),
                               blob_area=(len(res["blob_pts"])
                                          if res.get("blob_pts") is not None else None),
                               ora=dato["ora"])
                if oper is not None:                         # operativa: verdetto prima del
                    fonte = ("vlm" if state["vlm_on"] else "utente") if res["cls"] else "golden"
                    v = oper.valuta(res, fonte=fonte)        # pannello, così il PNG lo mostra
                    if gate_missed:                          # dati buoni MA gate fallito: registrato
                        v["gate_miss"] = True
                        L(f"step {v['step']}: GATE MISS (salvato con F)")
                    if tel:                                  # artefatti valutazione + aggancio GT live
                        ordine = len(oper.storico)
                        tel.valutazione(res, v, ordine,
                                        tipo=("correzione" if v.get("correzione_di")
                                              else "fuori_golden" if v.get("step") is None
                                              else "nuovo"))
                        state["gt_ord"] = ordine
                    ui.say(v["report"])                      # report: terminale + console in finestra
                    ko = v["verdetto"].startswith("KO")
                    stp = v["step"] or "-"
                    gt_hint = ("GT: 1=giusto  2=errore beccato  3=falso allarme",
                               (150, 150, 150))
                    if ko:
                        # invito a correggere ben visibile: dopo un KO l'operatore
                        # corregge d'istinto, ma senza R la run slitta
                        state["banner"] = [(f"STEP {stp}: {v['verdetto']}", (0, 0, 255)),
                                           ("correggi -> premi R (2 volte) | lascia cosi' -> avanza",
                                            (0, 200, 255)), gt_hint]
                        _beep_ko()
                    else:
                        state["banner"] = [(f"STEP {stp}: {v['verdetto']}",
                                            (0, 200, 255) if v["verdetto"] in ("DEBOLE", "OK?")
                                            else (0, 220, 0)), gt_hint]
                fn = f"radar_{k}.png"                        # pannello salvato: dal log si riapre
                try:
                    cv2.imwrite(fn, rp.render(res, state["sel"], oper_txt=_oper_txt(), **_oper_ctx()))
                except Exception as e:
                    L(f"foto {k}: ERRORE pannello ({str(e)[:50]})"); fn = None
                if registro_path and oper is None:           # registro di sessione persistito
                    _salva_registro(sess.registry, registro_path)  # (operativa: oper.dump() sotto)
                nreg = len(sess.registry)
                L(f"foto {k}: +{res['cls']} pin={[df.name(*u) for u in res['pins']]} "
                  f"[registro: {nreg} componenti] {res['note']}", img=fn)
                if v is not None:
                    L(f"STEP {v['step'] or '-'}: {v['verdetto']}", img=fn)
                    if registro_path:
                        _salva_registro(oper.dump(), registro_path)
                        _salva_ponte(oper)
                        _salva_pixel(oper)                 # sidecar posizionale, stessa cadenza del registro
                    if oper.finita():
                        print(oper.riepilogo_txt(), flush=True)
                        L("COMMESSA COMPLETATA - riepilogo a video e nel terminale")
                        try:                                 # tabella verde/rossa
                            cv2.imshow("riepilogo commessa", rp.render_riepilogo(
                                oper.cid, oper.golden["name"], oper.records))
                        except Exception as e:
                            L(f"riepilogo non disegnabile ({str(e)[:40]})")
                show_analysis(force=True)                    # finestra separata, chiudibile
                continue
            else:
                print(f"foto {k}: classe={res['cls']} pin={[df.name(*u) for u in res['pins']]} {res['note']}")
                if oper is not None:                         # collaudo operativa su run salvato
                    if res["blob_box"] is None:              # nessuna novità: lo step NON si
                        print(f"foto {k}: nessun blob - step {oper.step_i + 1} resta aperto")
                    elif (oper.assestamento_sospetto(res)
                          and oper.correzione_in_attesa(res) is None):   # stesso guard del live
                        print(f"foto {k}: assestamento sospetto - step {oper.step_i + 1} resta aperto")
                    else:                                    # consuma (come il guard del live)
                        v = oper.valuta(res, fonte=("golden" if novlm else "vlm"))
                        print(v["report"], flush=True)
                        if oper.finita():
                            print(oper.riepilogo_txt(), flush=True)
                            try:
                                cv2.imshow("riepilogo commessa", rp.render_riepilogo(
                                    oper.cid, oper.golden["name"], oper.records))
                            except Exception:
                                pass
                # replay: passo-passo con la finestra analisi, SPAZIO = prossima foto
                show_analysis(force=True)
                while True:
                    key = cv2.waitKeyEx(50)
                    low = key & 0xFF
                    if low == ord(' '):
                        break
                    elif low == ord('q'):
                        return
                    elif key in ARROW_L and res["region"]:
                        i = res["region"].index(state["sel"]) if state["sel"] in res["region"] else 0
                        state["sel"] = res["region"][(i - 1) % len(res["region"])]
                        show_analysis(force=True)
                    elif key in ARROW_R and res["region"]:
                        i = res["region"].index(state["sel"]) if state["sel"] in res["region"] else 0
                        state["sel"] = res["region"][(i + 1) % len(res["region"])]
                        show_analysis(force=True)
    finally:
        stop_cattura.set()             # il thread di cattura muore prima del finale
        rh.ABORT = None                # sgancia la tastiera del terminale dal VLM
        rh.VERBOSE = False
        if not replay:
            _pulisci_ponte()           # radar spento = webapp senza stato (pallini neutri)
        if oper is not None and replay and oper.pixel:
            # In replay il registro non si tocca (registro_path resta None): i verdetti
            # veri sono quelli della run live, e in replay la classe verrebbe dal golden invece
            # che dall'operatore. Si salva però il sidecar posizionale con un altro nome,
            # per raccogliere la geometria dei rami senza perdere l'originale.
            _salva_pixel(oper, "pixel_replay.json")
            print(f"[replay] pixel_replay.json salvato ({len(oper.pixel)} valutazioni) "
                  f"- pixel.json originale intatto")
        if oper is not None and not replay and registro_path and oper.records:
            try:                       # wizard GT in finestra (anche run parziale):
                gt = ui.gt_wizard(oper.records, rundir, oper.cid)   # pannelli + caselle pin
                if gt:
                    ui.salva_gt(gt, registro_path)
                    runid = os.path.basename(registro_path).replace("_registro.json", "")
                    try:               # score automatico: registro_errori.csv + xlsx
                        import score_oper as so
                        rows = so.score_run(runid)
                        so.genera_xlsx(so.aggiorna_registro(rows))
                        nerr = sum(1 for r in rows if r.get("err_montaggio"))
                        miss = sum(1 for r in rows if r["esito"] == "MISS")
                        print(f"[score] {runid}: {nerr} errori montaggio, {miss} MISS "
                              f"-> registro_errori.csv + andamento_errori.xlsx")
                    except Exception as e:
                        print(f"[score] non calcolato ({e}) - lancia: "
                              f"python src/radar/score_oper.py {runid}")
                else:
                    print("GT saltato (Q). Recupero: python src/radar/score_oper.py <runid>")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"GT non raccolto ({e}) - recupero: "
                      f"python src/radar/score_oper.py <runid> --wizard")
        if tel is not None:            # Excel della run: anche su run parziale
            tel.chiudi(oper)
        os.chdir(old)
        cv2.destroyAllWindows()

def solo_cattura(url, sec, cid, crop=0.0):
    """Modo cattura-sola: scatta a intervallo fisso e salva K.jpeg nella cartella run,
    senza analisi. Modo di servizio, per raccogliere frame grezzi senza giudizio:
    l'assistenza all'operatore la fa il radar normale. Il consumatore
    (consuma_coda.py) si lancia a mano.
    Fine: Q, oppure la webapp che passa al collaudo (_stop.flag nel ponte). In
    entrambi i casi si scrive _fine.flag, sentinella per il consumatore concorrente."""
    sec = _scegli_secondi(sec)
    calib = calibrate(url, init=_load_calib())
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    runsdir = os.path.join(ROOT, "data", "runs")
    runid = nm.nuovo(runsdir, cid) if cid else f"CATTURA_{stamp}"
    rundir = os.path.abspath(os.path.join(runsdir, runid))
    os.makedirs(rundir, exist_ok=True)
    img0, calib = _scatto0(url, calib, crop)
    if img0 is None:
        sys.exit("scatto 0 annullato / telefono non raggiungibile.")
    cv2.imwrite(os.path.join(rundir, "0.jpeg"), img0)
    print(f"[cattura-sola] cartella {rundir}  (0.jpeg salvata). Intervallo {sec}s. Q = fine.")
    _pulisci_ponte()                  # ponte pulito: niente _stop.flag rimasto da prima
    win = "cattura-sola (Q=fine)"
    k = 1
    try:
        while True:
            t0 = time.time()
            fine = False
            while True:                                   # countdown a tick, niente analisi
                left = sec - (time.time() - t0)
                if left <= 0:
                    break
                panel = np.full((160, 640, 3), 30, np.uint8)
                cv2.putText(panel, f"CATTURA-SOLA  commessa {cid or '-'}", (16, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 1)
                cv2.putText(panel, f"scatto #{k} tra {left:0.1f}s   (gia' {k-1} in coda)", (16, 84),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 220, 120), 2)
                cv2.putText(panel, "Q = fine assemblaggio (scrive _fine.flag)", (16, 128),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 1)
                cv2.imshow(win, panel)
                if (cv2.waitKey(100) & 0xFF) == ord('q'):
                    fine = True
                    break
                if _stop_da_webapp():                 # collaudo iniziato dalla webapp
                    print("[stop] la webapp e' passata al collaudo: chiudo la cattura.")
                    fine = True
                    break
            if fine:
                break
            img = rc.grab(url, calib=calib, crop_frac=crop)
            if img is None:
                print(f"[k{k}] scatto fallito (telefono?) - ritento al prossimo tick")
                continue
            cv2.imwrite(os.path.join(rundir, f"{k}.jpeg"), img)
            print(f"[k{k}] salvato -> coda")
            k += 1
    finally:
        open(os.path.join(rundir, "_fine.flag"), "w").close()
        cv2.destroyAllWindows()
        print(f"[fine] {k-1} scatti in coda + _fine.flag. Consuma con:\n"
              f"  python src/radar/consuma_coda.py \"{rundir}\" --commessa {cid or '<ID>'} --concorrente")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None,
                    help="URL dello scatto del telefono; senza, si cerca tra URL_CANDIDATI")
    ap.add_argument("--sec", type=float, default=6.0)
    ap.add_argument("--id", default="LIVE")
    ap.add_argument("--replay", default=None)
    ap.add_argument("--novlm", action="store_true",
                    help="niente VLM: radar cieco sulla classe (prova offline veloce)")
    ap.add_argument("--forceclass", default=None,
                    help="forza la classe (es. bottone) al posto del VLM: vedi geometria/pin offline")
    ap.add_argument("--crop", type=float, default=0.0,
                    help="ritaglia la fascia sopra/sotto (frazione, es. 0.15) se il nastro di "
                         "fissaggio entra nelle rail e ne falsa il conteggio")
    ap.add_argument("--forcemap", action="store_true",
                    help="procedi anche con mappa degradata MINORE (es. 1 foro rail): il radar "
                         "non emette verdetti, un foro rail in meno non conta")
    ap.add_argument("--recalib", action="store_true",
                    help="(obsoleto: il riquadro si fa a ogni sessione, parte dal salvato)")
    ap.add_argument("--salvascarti", action="store_true",
                    help="salva su disco anche gli scatti SCARTATI (scatti/) per rivedere il "
                         "gate; default: cartelle pulite, solo le foto analizzate")
    ap.add_argument("--commessa", default=None,
                    help="modalita' OPERATIVA sulla commessa (es. F1): salta il menu fase. "
                         "Con --replay = collaudo operativa su un run salvato")
    ap.add_argument("--solo-cattura", dest="solo_cattura", action="store_true",
                    help="cattura differita: scatta a intervallo fisso e salva le foto SENZA "
                         "analizzare (poi le elabora consuma_coda.py)")
    a = ap.parse_args()
    url = a.url if a.replay else _trova_url(a.url)   # in replay il telefono non serve
    if a.solo_cattura:
        # senza --commessa si chiede col menu di sempre: serve al nome run e al
        # consumatore (senza golden non c'è niente da confrontare)
        solo_cattura(url, a.sec, a.commessa or _scegli_fase(), a.crop)
        return
    run(url, a.sec, a.id, a.replay, a.novlm, a.forceclass, a.crop, a.forcemap, a.recalib,
        a.salvascarti, a.commessa)

if __name__ == "__main__":
    main()
