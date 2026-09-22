"""radar_operativa.py — modalità operativa: confronto live col golden della commessa.

DOVE a due strati:
  strato 1 = verifica ancorata al golden (footprint atteso -> misure mirate + vicini +-1)
  strato 2 = radar cieco (fit_pins senza golden, già in res["pins"]) = controprova
Il golden alimenta la verifica, mai la ricerca libera. Allarme senza blocco: si
avanza sempre allo step successivo. Primitivi di differential in sola lettura."""
import json
import os
import numpy as np
import cv2
import differential as df
import corpo as cp
import filo as fl
import cupola as cu

# famiglie di verifica
FAM_COLONNE = {"bottone", "optoaccoppiatore", "rgb"}   # coppie di colonna dal golden
# Regola solo-golden: per queste classi decide esclusivamente lo stato dei fori
# golden — cambiati = OK, non cambiati = KO. Niente percorso, niente sfida, niente
# radar cieco. Ombre mitigate: un foro in zona non-evidenza (corpo/ombra) deve
# scaldare il doppio per contare. La fotoresistenza è trattata come una resistenza.
FAM_SOLO_GOLDEN = {"resistenza", "condensatore", "diodo", "led", "fotoresistenza"}
T_LED_BASE = 0.10      # per il led la soglia di classe non basta (misurato: led
                       # giusto 0.55/0.71, led errato 0.003) -> pavimento dedicato
MONO_PIEDINO = (0.12, 0.25)   # banda vs àncora del piedino scavalcato nell'esterno:
                              # fori quieti <= 0.089, piedini 0.137-0.180, archi di
                              # reoforo >= 0.256, misurati su tutte le run
USA_MONO_PIEDINO = True    # Il ground truth non è un giudice per questo guasto: registra
                           # in quali fori sta il pezzo, non quanto è calzato. Una
                           # resistenza premuta troppo a fondo occupa nominalmente i suoi
                           # due fori (errore_montaggio=False) ma i reofori si piegano e
                           # arrivano oltre, ed è proprio quello che questa banda misura.
                           # Caso di riferimento verificato a mano: resistenza
                           # "schiacciata troppo", esterno 0.14 nella banda -> KO
                           # POSIZIONE corretto. Su 27 run scatta 5 volte; 3 casi
                           # restano da verificare a vista, non col GT.
T_FILO_NUDO = 0.05     # golden davvero nudo per la controprova collare del cavetto
                       # (misurato: cavetto dentro 0.069-0.254, cavetto accanto 0.045)
T_LED_NUDO = 0.055     # pavimento del capo nudo del led (misurato: gamba nuda presente
                       # 0.069-0.24, foro vuoto 0.003-0.05)
FAM_CORPO = {"trimmer", "buzzer"}                       # verdetto dalla geometria del corpo
SOSP_MARG = 2.0        # vicino "batte" il golden se score_vicino >= thr e >= SOSP_MARG*score_golden
M_SFIDA = 2.0          # sfidante golden resistenza: una coppia traslata coerente del
                       # footprint golden declassa solo se batte il piazzamento golden
                       # di >= M_SFIDA, e solo su fori non del pezzo corrente né di un
                       # pezzo già montato. Plateau misurato 1.8-2.5: lo sconto locale
                       # fa il lavoro, non la soglia.
SOGLIA_ATTESA = 0.08   # conferma del registro attese: il disturbo di una correzione
                       # filo è sottile (0.12-0.14) e sta sotto SOGLIA_SIGN 0.15 (tarata
                       # sul bottone, 0.19). Step fermi misurati <= 0.039: lo 0.08 sta
                       # nel vuoto. Qui si può scendere perché la conferma arriva dopo
                       # trigger bbox + due-ipotesi, non da sola.
SOGLIA_SIGN = 0.15     # watchdog correzioni: disturbo (legscore max sui fori golden di
                       # uno step chiuso, prev vs ora) sopra cui lo scostamento è
                       # significativo (l'urto sta sotto). Misurato: step fermi
                       # 0.004-0.031, correzione vera 0.19. Il vero discriminante è però
                       # l'override "pezzo nuovo" (la vicinanza fa 0.19 come una
                       # correzione).
# Check colore: classi dove il colore identifica il pezzo + nomi come in df.HUES.
# Dichiarato: se blob_color non vede saturazione (ritorna '') il check passa in silenzio.
USA_CORPO = True        # ramo corpo (buzzer, trimmer): interruttore per l'A/B
USA_FILO = True         # ramo filo (jumper, cavetti): interruttore per l'A/B
USA_CUPOLA = False      # led: spento. La cupola è larga quasi due fori e copre i fori
                        # del golden anche quando il led è montato altrove: misurato,
                        # 2 montaggi sbagliati su 3 passavano da segnalati a OK.
                        # Né il massimo (troppo permissivo) né il minimo (il capo nudo
                        # vale 0.00-0.24, non passerebbe mai). Vedi cupola.py.
CLS_COLORE = {"led", "jumper"}
COLORI = ("rosso", "arancione", "giallo", "verde", "azzurro", "blu", "viola", "bianco")
T_COL_SOST = 120        # controprova della sostituzione dopo KO COLORE: pixel saturi
                        # del colore atteso nel disco di 2 passi attorno al golden.
                        # Sostituzioni vere misurate: 243-3648; pezzo del KO mai tolto
                        # e pezzo nuovo altrove: 53 px saturi in tutto nel disco.
                        # 120 sta nel vuoto fra 53 e 243.
# Coppie di tinte che la fotocamera confonde: non far scattare KO COLORE tra queste
# (es. cavetto verde rigido letto "azzurro"). Le bande HSV sono adiacenti (verde
# 36-85, azzurro 85-105, blu 90-130) e il verde del kit è spento, quindi scivola da
# una banda all'altra fra due scatti dello stesso cavo a board immobile. filo.py
# dichiara già il verde fuori portata della segmentazione per tinta
# (COL_FUORI_SCOPO): la stessa fisica che squalifica la sagoma squalifica il colore.
# Effetto misurato su 27 run: 2 righe, entrambe falsi allarmi secondo il GT.
COL_CONFONDIBILI = ({"verde", "azzurro"}, {"azzurro", "blu"}, {"verde", "blu"})


def lista_commesse():
    """[(id, nome), ...] da golden-data.json (stessi path di df.load_golden)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (df._cerca_golden(),):
        if p and os.path.exists(p):
            data = json.load(open(p, encoding="utf-8"))
            return [(c["id"], c["name"]) for c in data]
    return []


def colore_verdetto(v):
    """Semaforo: il verdetto viaggia anche come colore nel registro — l'operatore in
    webapp vede un pallino, non la sigla "OK?".
      verde  = pulito (PULITI di score_oper)     giallo = controlla a occhio (OK?/DEBOLE)
      rosso  = KO, sempre col motivo             grigio = non valutabile"""
    v = str(v)
    if v in ("OK", "OK FORTE"):
        return "verde"
    if v.startswith("KO"):
        return "rosso"
    if v in ("OK?", "DEBOLE"):
        return "giallo"
    return "grigio"


def _ascii(s):
    """Console cp1252-safe (la Omega del golden fa fallire la stampa): i simboli del
    golden in ASCII. Solo per il report a terminale, il registro JSON resta originale."""
    s = str(s).replace("→", "->").replace("·", ".").replace("–", "-")
    s = s.replace("Ω", "Ohm").replace("Ω", "Ohm").replace("−", "-")
    return s.encode("ascii", "replace").decode()


def _cls_ok(cls_att):
    """Classi che valgono come 'quella attesa' quando il VLM risponde.

    I FILI sono l'unico caso: il vocabolario del VLM ha due parole (df.DESCR —
    'jumper' = rigido coi capi nudi, 'cavetto' = flessibile con le guaine nere) ma il
    golden ne scrive una sola, kind='jumper', anche per i 7 cavetti flessibili. Un VLM
    che risponde 'cavetto' davanti a un cavetto ha ragione, e usciva KO CLASSE.
    Il motore a posteriori l'equivalenza ce l'ha da sempre (df.qc_main:
    okset = {cls} | FREE2): questa è la stessa regola, stessa fonte (df.FREE2).
    Rigido vs flessibile continua a deciderlo la parola 'flessibile' nel testo del
    golden (fl.flessibile), che è ciò che sceglie il ramo del DOVE.
    Eccezione misurata: golden che dice 'rigido' in chiaro + VLM 'cavetto' + guaina
    nera trovata al capo della sagoma -> KO CLASSE. Sta nel ramo filo di _giudica,
    non qui: l'equivalenza resta, la boccia la fisica."""
    return {cls_att} | (df.FREE2 if cls_att in df.FREE2 else set())


def _px_colore(img, p, r, col):
    """Pixel SATURI della tinta `col` (df.HUES) nel disco di raggio r attorno a p.
    Misura statica sull'immagine intera, non sui pixel cambiati: serve alla
    controprova della sostituzione (il pezzo del KO mai rimosso non cambia mai nel
    diff, ma sta lì e si vede). None = tinta fuori tavola."""
    rngs = df.HUES.get(col)
    if not rngs:
        return None
    x0, y0 = max(0, int(p[0] - r)), max(0, int(p[1] - r))
    sub = img[y0:int(p[1] + r), x0:int(p[0] + r)]
    if sub.size == 0:
        return 0
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    yy, xx = np.mgrid[y0:y0 + sub.shape[0], x0:x0 + sub.shape[1]]
    sel = (((xx - p[0]) ** 2 + (yy - p[1]) ** 2) <= r * r) & (S > 100) & (V > 60)
    return int(sum(int(((H >= lo) & (H <= hi) & sel).sum()) for lo, hi in rngs))


def _vicini(uv, posx, esclusi):
    """4-vicini di GRIGLIA di un foro atteso (riga +-1, colonna +-1), solo se in mappa
    e non a loro volta attesi. Serve al sospetto +-1 (leakage del patch 23x23)."""
    u, v = uv
    if not isinstance(u, int):
        return []
    cand = [(u, v - 1), (u, v + 1), (u - 1, v), (u + 1, v)]
    return [c for c in cand if c in posx and c not in esclusi]


class Operativa:
    """Stato della commessa: golden, step corrente, registro. Un'istanza per sessione."""

    def __init__(self, cid):
        self.golden, self.gpath = df.load_golden(cid)   # sys.exit se id sconosciuto
        self.cid = self.golden["id"]
        self.build = self.golden["build"]
        self.step_i = 0                                 # 0-based: prossimo step atteso
        self.records = []                               # STATO: un record per step, l'ultimo vince
        self.storico = []                               # tutte le valutazioni, in ordine, mai sostituite.
        # Due liste: una correzione sostituisce il record dello step (records[m-1] = rec)
        # e la valutazione precedente sparirebbe dal registro, spesso proprio il caso
        # interessante (il pezzo nella posizione giusta bocciato prima che l'operatore
        # lo spostasse). `records` è lo stato (score_oper e il wizard GT ci contano
        # sopra), `storico` tiene tutto per l'elaborazione offline.
        self.pixel = {}                                 # ordine -> dati posizionali (sidecar)
        self._filo_corrente = None                      # sagoma del ramo filo dell'ultimo giudizio
        self._colore_da_cupola = False                  # il col_vista corrente viene dal
                                                        # fallback cupola (vedi _colore_cupola)
        self._prior = {}                                # step -> fori del pezzo montato bene.
                                                        # Dopo un KO la zona non va memorizzata
                                                        # (quel pezzo si muoverà) e la correzione
                                                        # sostituisce la zona vecchia.
        self.attese = {}                                # registro correzioni attese:
                                                        # step KO -> {fori, bbox}. La voce non
                                                        # scade: la correzione può arrivare
                                                        # molti frame dopo.
        self.board_ok = None                            # àncora: gray dell'ultima board
                                                        # accettata (prev del primo giudizio,
                                                        # poi aggiornata a ogni verdetto non-KO).
                                                        # Il solo-golden misura la presenza
                                                        # contro di lei, mai il cambiamento
                                                        # contro il prev.

    # ---------- interrogazioni ----------
    def step_atteso(self):
        """Lo step golden corrente (dict) o None se la commessa è finita."""
        return self.build[self.step_i] if self.step_i < len(self.build) else None

    def finita(self):
        return self.step_i >= len(self.build)

    def attesi_uv(self, st):
        """Fori attesi di GRIGLIA dello step (le rail non stanno in coords)."""
        return [df.parse_hole(h) for h in st.get("coords", [])]

    def attesi_px(self, posx):
        """Posizioni pixel dei fori attesi dello step corrente (per l'overlay 'monta qui')."""
        st = self.step_atteso()
        if st is None:
            return []
        return [posx[uv] for uv in self.attesi_uv(st) if uv in posx]

    # ---------- strato 1: verifica ancorata ----------
    def _punteggio_classe(self, cls_att, exp, S):
        """(punteggio, dettaglio) della verifica ancorata sulla struttura di classe.
        bottone/opto/rgb: il punteggio è il legscore del piedino più debole fra i fori
        attesi — identico al pick cieco di fit_pins, così i due percorsi producono lo
        stesso numero. Il dettaglio resta per colonna, che è come l'operatore legge il
        pezzo. Resto = media geometrica dei capi (un capo a zero azzera tutto: mai
        promozione, regola dura)."""
        if not exp:
            return 0.0, "nessun foro atteso in mappa"
        if cls_att in FAM_COLONNE:
            col = {}
            for uv in exp:
                col.setdefault(uv[1], []).append(S.get(uv, 0.0))
            det = "  ".join(f"col {v+1}: " + " ".join(f"{x:.3f}" for x in ss)
                            for v, ss in sorted(col.items()))
            peggiore = min(S.get(uv, 0.0) for uv in exp)
            return peggiore, det + f"   -> piedino piu' debole {peggiore:.3f}"
        p = df.pair_score([S.get(uv, 0.0) for uv in exp])
        return p, f"media geometrica dei {len(exp)} capi: {p:.3f}"

    # Righe di fori che il corpo copre sotto la riga dei piedini, misurate sui montaggi
    # corretti dell'archivio. Il buzzer è rigido: la distanza fra i suoi piedini e il
    # bordo del corpo è una proprietà del pezzo, non della commessa.
    RIGHE_COPERTE = {"buzzer": 2}      # 2 su entrambi i montaggi corretti dell'archivio

    def _righe_coperte(self, cls_att, res, geo, attesi):
        """Quante righe di fori il corpo copre SOTTO la riga dei piedini attesa.

        Misura strutturale e discreta: un pezzo montato una riga più in basso lascia
        libera una riga in meno, e questo si conta, non si tara. Sostituisce, per il
        buzzer, il residuo continuo di `_verifica_corpo`, che misurava 1.09 passi contro
        una tolleranza di 1.5 e lasciava passare uno spostamento di due righe. Quel
        residuo passa dall'offset per-commessa `OFF_CORPO`, tarato su una sola run, e il
        suo errore è dello stesso ordine dello spostamento da rilevare (1.09 misurati
        contro 1.55 reali). Qui l'offset non entra: si usano il cerchio del corpo e le
        righe della mappa.
        Ritorna (coperte, attese) o None se non applicabile."""
        att = self.RIGHE_COPERTE.get(cls_att)
        if att is None or not geo or geo.get("r") is None:
            return None
        posx = {**res["pos"], **res["rpos"]}
        righe = {}
        for uv, p in posx.items():
            if not isinstance(uv[0], str):
                righe.setdefault(uv[0], []).append(p[1])
        if not righe:
            return None
        y_riga = {u: float(np.median(v)) for u, v in righe.items()}
        u_pin = [uv[0] for uv in attesi if uv[0] in y_riga]
        if not u_pin:
            return None
        y_pin = y_riga[u_pin[0]]
        bordo = geo["centro"][1] + geo["r"]              # bordo BASSO del corpo
        sotto = [u for u, y in y_riga.items() if y > y_pin]   # righe sotto quella dei pin
        if len(sotto) < att + 1:                          # non c'è spazio per contare
            return None
        coperte = sum(1 for u in sotto if y_riga[u] <= bordo)
        return coperte, att

    D_RIMOZIONE = 12.0  # salita media di luminosità (HSV V) nel blob = pezzo TOLTO

    def _rimozione_sospetta(self, res, st_bersaglio, base_g0=None):
        """Questo scatto è la RIMOZIONE di un pezzo, non il montaggio di uno?

        Il motore ha un modello monotono: i pezzi arrivano e basta. Il `legscore` misura
        quanto è cambiato, non in che verso, e togliere un pezzo cambia esattamente gli
        stessi pixel che metterlo aveva cambiato (misurato: montaggio `f19=0.34
        +t21=0.38 -t22=0.13`, rimozione `f19=0.35 +t21=0.40 -t22=0.13`). Senza questo
        test la rimozione diventa il montaggio dello step successivo e ne esce un KO
        POSIZIONE su un componente che l'operatore non ha ancora toccato.

        Il verso però si vede: un pezzo su board bianca la scurisce, toglierlo la
        schiarisce. Sul patch attorno a un foro: 200.7 vuoto → 187.0 col pezzo (−13.6)
        → 200.7 dopo la rimozione (+14.5), cioè torna al valore esatto di prima.
        Sull'archivio, misurando la variazione media di V nel blob su 181 frame
        giudicati, ne superano +12 soltanto tre, e sono tutti eventi veri di "il pezzo
        se n'è andato da qui": una resistenza tolta (+53), un LED sparito (+46), un
        trimmer spostato (+38). Sotto di loro si scende a +9.8.

        Spostamento != rimozione, e i pixel del blob non li separano (schiariti 70.8% /
        82.3% / 91.8%, con lo spostamento in mezzo alle due rimozioni). Li separa il
        bersaglio: se il pezzo si è spostato ed è atterrato dove il golden lo vuole, i
        fori attesi di quello step sono vivi (il trimmer spostato è la correzione di uno
        step precedente e va giudicato normalmente). Se invece nessun foro atteso è
        vivo, non è arrivato niente: è una rimozione e basta."""
        if res.get("blob_pts") is None or not len(res["blob_pts"]) or st_bersaglio is None:
            return False
        a = np.asarray(res["blob_pts"]).astype(int)
        h, w = res["img"].shape[:2]
        a = a[(a[:, 0] >= 0) & (a[:, 1] >= 0) & (a[:, 0] < w) & (a[:, 1] < h)]
        if not len(a):
            return False
        v1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32)
        v0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32)
        if float((v1[a[:, 1], a[:, 0]] - v0[a[:, 1], a[:, 0]]).mean()) < self.D_RIMOZIONE:
            return False
        posx = {**res["pos"], **res["rpos"]}
        exp = [uv for uv in self.attesi_uv(st_bersaglio) if uv in posx]
        if not exp:
            return True                      # solo-rail: nessun appiglio, resta rimozione
        # Due riferimenti in OR, e servono tutti e due perché il pezzo tolto può
        # appartenere allo step bersaglio oppure a un altro:
        #   vs PREV     "in questo scatto qui non è arrivato niente"
        #   vs BASE_G0  "questo posto è tornato com'era prima che il pezzo esistesse"
        # Misurato sui tre casi di rimozione dell'archivio:
        #   resistenza tolta, bersaglio = step con il suo LED ancora montato
        #             vs prev 0.032 freddo  | vs base 0.304 caldo   -> prende il prev
        #   LED tolto, bersaglio = il suo step
        #             vs prev 0.677 caldo   | vs base 0.005 freddo  -> prende la base
        #             (vs prev è caldo proprio perché è la rimozione ad accenderlo:
        #              con un riferimento solo questo caso non si prende)
        #   trimmer spostato che atterra sui suoi golden
        #             vs prev 0.624 caldo   | vs base 0.640 caldo   -> non scatta, e
        #             infatti è una correzione riuscita, non una rimozione
        # Un pezzo arrivato davvero non è freddo contro nessuno dei due.
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        thr, _ = df.cls_thr(df.KIND2CLS.get(st_bersaglio["kind"], st_bersaglio["kind"]))
        if not any(df.legscore(g0, g1, posx, *uv) >= thr for uv in exp):
            return True
        if base_g0 is None:
            return False
        gb = base_g0.astype(np.float32)
        return not any(df.legscore(gb, g1, posx, *uv) >= thr for uv in exp)

    M_RAIL = 1.25       # margine minimo fra il segno vinto e l'opposto sulla stessa striscia

    def _verifica_solo_rail(self, res, posx, thr, holes_txt):
        """Step con ENTRAMBI i capi su rail (es. 'da − a −'): giudizio dai NODI RAIL.

        `coords` è vuoto e il DOVE ancorato non ha appigli di griglia, ma i nodi rail
        si misurano come tutti gli altri fori (misurato: un cavetto 'da − a −' accende
        `-t = 0.86` in alto e `-b = 0.69` in basso contro un fondo di 0.02). Dire
        "non verificabile" quando il segnale c'è è una rinuncia gratuita.

        Metodo: per ogni striscia (alta e bassa) si prende il nodo più caldo; il suo
        segno è il capo misurato su quella striscia. Si confronta il multinsieme dei
        segni misurati con quello che il golden dichiara nel testo `holes`.
        Il margine conta: per arrivare alla fila esterna il filo attraversa quella
        interna e la scalda quasi uguale (misurato: `-b = 0.687` contro `+b = 0.670`,
        il 2.5%). Sotto M_RAIL il segno di quella striscia non è una chiamata sicura e
        si dichiara la riserva invece di inventare un verdetto.
        Ritorna (verdetto, spiegazione) o None se non c'è segnale sulle rail."""
        segni_att = ["+" if c == "+" else "-" for c in (holes_txt or "")
                     if c in "+-−"]
        if not segni_att:
            return None
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        ga = g0 if self.board_ok is None else self.board_ok.astype(np.float32)
        best = {}                       # striscia -> {segno: (valore, nodo)}
        for uv in res.get("rpos", {}):
            if not isinstance(uv[0], str) or len(uv[0]) < 2:
                continue
            segno, striscia = uv[0][0], uv[0][1:]
            v = max(df.legscore(g0, g1, posx, *uv), df.legscore(ga, g1, posx, *uv))
            b = best.setdefault(striscia, {})
            if v > b.get(segno, (0.0, None))[0]:
                b[segno] = (v, uv)
        capi = []                       # (striscia, segno, valore, margine)
        for striscia, per_segno in best.items():
            segno, (v, uv) = max(per_segno.items(), key=lambda kv: kv[1][0])
            if v < thr:
                continue
            opp = per_segno.get("+" if segno == "-" else "-", (0.0, None))[0]
            capi.append((striscia, segno, v, (v / opp) if opp > 0 else float("inf")))
        if not capi:
            return None
        misurati = sorted(s for _, s, _, _ in capi)
        det = "  ".join(f"{s}{st_}={v:.2f} (x{m:.2f} sull'opposto)"
                        for st_, s, v, m in sorted(capi))
        # Il margine viene prima del confronto. Su una striscia dove il segno vinto
        # supera l'opposto dell'1% (misurato: x1.01) il segno non è determinato, e
        # bocciare lì significa bocciare il rumore. Si dichiara la riserva sia quando i
        # segni coincidono sia quando no: si decide solo dove la misura decide davvero.
        incerti = [st_ for st_, _, _, m in capi if m < self.M_RAIL]
        if incerti:
            return ("OK?",
                    f"step solo-rail: i nodi rail dicono '{','.join(misurati)}', il "
                    f"golden vuole '{','.join(segni_att)}' — {det}. Sulla striscia "
                    f"{','.join(incerti)} il margine sull'opposto e' sotto "
                    f"{self.M_RAIL}: il filo attraversa l'altra fila per arrivarci e la "
                    f"scalda quasi uguale, quindi il segno li' NON e' determinato - "
                    f"CONTROLLA A OCCHIO")
        if misurati != sorted(segni_att):
            return ("KO POSIZIONE",
                    f"step solo-rail: il golden vuole i capi su "
                    f"'{','.join(segni_att)}' ma i nodi rail dicono "
                    f"'{','.join(misurati)}', con margine netto su entrambe le "
                    f"strisce — {det}")
        return ("OK",
                f"step solo-rail: entrambi i capi sulle rail attese "
                f"('{','.join(segni_att)}') con margine netto — {det}")

    R_COLORE = 1.5      # passi: raggio del disco attorno a ogni foro golden

    def _colore_sul_pezzo(self, res, exp, posx, pitch, cls_att=None):
        """Colore misurato sul pezzo in giudizio: dischi attorno ai fori golden.

        `blob_color` sul riquadro del blob non basta: il riquadro può contenere un
        cavetto di un altro step che si è mosso (oggetto sbagliato) o il LED della
        colonna accanto, già montato (due oggetti). Entrambi i casi dell'archivio
        erano falsi KO COLORE con lo strato ancorato che confermava il montaggio.
        Qui il colore si misura dove il golden dice che il pezzo deve essere, quindi
        non può venire da un pezzo diverso.
        Il disco da solo non basta, misurato: a 1.5 passi attorno ai fori golden, senza
        altri filtri, sull'archivio uscirebbero 9 KO COLORE tutti falsi, lo stesso step
        su run diverse: la firma del LED vicino, che sta a una colonna e finisce dentro
        il disco. Serve la seconda condizione: solo i pixel cambiati, cioè
        l'intersezione col blob. Il pezzo nuovo ha cambiato i suoi pixel, il vicino
        già montato no. Misurato sull'archivio con questa forma: 82 step con colore
        atteso, 62 misurati e tutti concordi col golden, 20 non misurabili, zero KO
        COLORE.
        Raggio 1.5 passi: sotto 1.3 la cupola del LED non si prende (è spostata rispetto
        al foro e il disco prende board bianca e reoforo).
        Ritorna il nome del colore o None (= non misurabile: il COSA non giudica il
        colore, invece di inventarlo).
        Fallback cupola per i soli LED: quando la cupola, l'unica parte colorata, pende
        oltre i dischi, qui uscirebbe None e l'errore di colore passerebbe (verde dove
        serve giallo, blu dove serve verde: due casi veri in archivio).
        Vedi _colore_cupola."""
        self._colore_da_cupola = False
        blob = res.get("blob_pts")
        if not exp or blob is None or not len(blob):
            return None
        img = res["img"]
        h, w = img.shape[:2]
        cambiati = np.zeros((h, w), bool)
        b = np.asarray(blob).astype(int)
        b = b[(b[:, 0] >= 0) & (b[:, 1] >= 0) & (b[:, 0] < w) & (b[:, 1] < h)]
        if not len(b):
            return None
        cambiati[b[:, 1], b[:, 0]] = True
        vicino = np.zeros((h, w), bool)
        R = int(self.R_COLORE * (pitch or 30))
        for uv in exp:
            x, y = int(posx[uv][0]), int(posx[uv][1])
            y0, y1 = max(0, y - R), min(h, y + R + 1)
            x0, x1 = max(0, x - R), min(w, x + R + 1)
            if y0 >= y1 or x0 >= x1:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            vicino[y0:y1, x0:x1] |= (xx - x) ** 2 + (yy - y) ** 2 <= R * R
        ys, xs = np.nonzero(cambiati & vicino)
        col = (df.blob_color(img, np.stack([xs, ys], 1)) or None) if len(xs) else None
        if col is None and cls_att == "led":
            col = self._colore_cupola(res, exp, posx, pitch)
        return col

    def _colore_cupola(self, res, exp, posx, pitch):
        """Colore della cupola del LED appena montato, quando i dischi tacciono.

        Misurato su tutte le 39 righe LED giudicabili dell'archivio: la componente
        satura del blob più vicina al golden legge il colore giusto ovunque, ma solo
        con due porte, entrambe su un vuoto misurato:
          - AREA >= 600 px      cupole vere 718-4530; spurie (pastello blu della
                                rail, riflessi) 122-518
          - DISTANZA <= 2.5 p   cupola del pezzo 0.0-2.1 passi; cupola del LED
                                vicino 2.9-3.3 (il caso dei blob fusi)
        Il vincolo al blob resta: il pezzo nuovo ha cambiato i suoi pixel, il vicino
        già montato no. Setta _colore_da_cupola per il check confondibili (le cupole
        verdi/blu si leggono giuste 12/12: la deriva verde->blu è dei cavetti spenti,
        non delle cupole sature)."""
        blob = res.get("blob_pts")
        if blob is None or not len(blob) or not exp:
            return None
        img = res["img"]
        h, w = img.shape[:2]
        b = np.asarray(blob).astype(int)
        b = b[(b[:, 0] >= 0) & (b[:, 1] >= 0) & (b[:, 0] < w) & (b[:, 1] < h)]
        if not len(b):
            return None
        m = np.zeros((h, w), np.uint8)
        m[b[:, 1], b[:, 0]] = 255
        m = cv2.dilate(m, np.ones((25, 25), np.uint8))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        sat = (((S > 100) & (V > 60)) & (m > 0)).astype(np.uint8)
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(sat)
        gpx = [posx[uv] for uv in exp if uv in posx]
        best = None
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] < 600:
                continue
            ys, xs = np.where(lbl == i)
            d = min(float(np.hypot(xs - g[0], ys - g[1]).min()) for g in gpx)
            if d > 2.5 * (pitch or 30):
                continue
            if best is None or d < best[0]:
                hh = H[ys, xs]
                nome, bn = None, 0
                for c, rngs in df.HUES.items():
                    cnt = sum(int(((hh >= lo) & (hh <= hi)).sum()) for lo, hi in rngs)
                    if cnt > bn:
                        bn, nome = cnt, c
                best = (d, nome)
        if best and best[1]:
            self._colore_da_cupola = True
            return best[1]
        return None

    def _verifica_corpo(self, cls_att, res, pitch, attesi_pxl):
        """Classi da corpo (trimmer/buzzer/cap verticale): sotto il corpo il legscore
        non dimostra nulla -> verdetto dalla geometria (corpo_base congelato, offset
        per-commessa). Ritorna (esito, righe_report) o None se detector non agganciato."""
        cb = df.corpo_base(cls_att, self.cid, res["img"], res["blob_box"], pitch)
        if cb is None or not attesi_pxl:
            return None
        met, base, ripiego = cb[:3]
        self._corpo_geo = cb[3] if len(cb) > 3 else None
        cx = sum(p[0] for p in attesi_pxl) / len(attesi_pxl)
        cy = sum(p[1] for p in attesi_pxl) / len(attesi_pxl)
        resd = ((base[0] - cx) ** 2 + (base[1] - cy) ** 2) ** 0.5 / pitch
        esito = "LI" if resd <= 0.7 else ("DEBOLE" if resd <= df.CORPO_TOL else "NON_LI")
        righe = [f"  corpo agganciato: {met}" + ("  [offset di RIPIEGO k radiale]" if ripiego else ""),
                 f"  residuo base-vs-golden: {resd:.2f} passi (tolleranza {df.CORPO_TOL})"]
        return esito, righe

    # ---------- guardia assestamento ----------
    def assestamento_sospetto(self, res):
        """True se il frame ha un blob ma nessuna evidenza di componente nuovo:
        radar cieco senza alcun pin (nemmeno rail) e fori attesi tutti muti.
        Firma dell'assestamento di un pezzo già montato (es. bottone mosso mentre si
        prepara la resistenza: consumerebbe lo step). Lo step resta aperto.
        Vale anche quando i fori attesi sono muti anche contro l'àncora, il VLM legge
        la classe di uno step rimasto aperto in KO e il blob sta sulla zona di quello
        step: quel pezzo è ancora in mano, il frame non è suo."""
        self.assestamento_di = None      # step a cui appartiene il pezzo mosso, se noto
        st = self.step_atteso()
        if st is None or res["blob_box"] is None:
            return False
        attesi = self.attesi_uv(st)
        posx = {**res["pos"], **res["rpos"]}
        exp = [uv for uv in attesi if uv in posx]
        if not exp:
            return False                              # solo-rail: niente appigli, non giudico
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        cls_att = df.KIND2CLS.get(st["kind"], st["kind"])
        _, weak = df.cls_thr(cls_att)
        if any(df.legscore(g0, g1, posx, *uv) >= weak for uv in exp):
            return False                              # segnale sui fori attesi: c'è un pezzo
        # Pezzo di uno step ancora aperto. Il VLM legge la classe di uno step rimasto in
        # KO: quel pezzo è ancora in mano all'operatore, e i fori attesi di questo step
        # sono muti (appena verificato qui sopra). Non è il montaggio nuovo: è traffico
        # sullo step aperto. Gli arbitri non lo prendono perché l'attesa di quello step
        # non sa dove stava il pezzo (es. pick tutto su rail: pins_ko tiene i soli fori
        # di griglia e la voce contiene il solo golden, che non si muove). Sotto
        # SOGLIA_ATTESA e sotto SOGLIA_SIGN correzione_in_attesa e _arbitro_correzione
        # tacciono entrambi, il puntatore apre lo step successivo su un frame che mostra
        # ancora quello aperto e ne esce un KO CLASSE inventato.
        # È la stessa impossibilità di _classe_ancora_possibile, che lì non scatta
        # apposta: gli step aperti in KO restano "possibili" perché il pezzo può tornare
        # in mano. Vero, ma proprio per questo il frame è loro.
        #
        # Due condizioni oltre alla classe, dettate dall'archivio (la sola classe
        # scartava anche un KO POSIZIONE vero e un OK vero):
        #  1. Muti anche contro l'àncora, non solo contro il prev. Il test qui sopra è un
        #     diff prev-vs-ora: se il pezzo è entrato in un frame saltato, il prev non lo
        #     vede più mentre l'àncora sì, e va giudicato.
        #  2. Il blob deve stare sulla zona dello step aperto (stesso trigger geometrico
        #     di correzione_in_attesa, tolleranza 1.5 passi). La classe da sola non
        #     distingue "il pezzo vecchio si muove" da "il pezzo nuovo è della stessa
        #     classe di uno step aperto".
        # Misurato sul replay completo di tutte le run: scatta su 2 frame, entrambi KO
        # CLASSE falsi. Nient'altro si muove.
        #
        # Anche gli step chiusi con OK. Il pezzo che l'operatore rimette a posto può
        # appartenere a uno step già chiuso bene (misurato: una fotoresistenza uscita OK
        # e poi spostata due volte, senza che arrivi niente di nuovo). `attese` è vuoto,
        # nessun arbitro ha un candidato, e i frame verrebbero giudicati contro lo step
        # successivo coi suoi fori muti: KO POSIZIONE inventati. La zona di uno step
        # chiuso non sta in `attese` (la voce esce a verdetto non-KO) ma in
        # `self._prior`, che `_accumula_prior` riempie a ogni giudizio coi fori sotto la
        # sagoma del blob più i golden: è esattamente "dove il pezzo sta".
        # `_classe_ancora_possibile` è la stessa guardia del COSA: se nessuno step dal
        # corrente in poi, né aperto in KO, è di quella classe, ogni esemplare è già
        # montato e fermo, quindi il blob non può essere un pezzo nuovo.
        cls_vista = res.get("cls")
        if res["blob_box"] is not None:
            bx = res["blob_box"]
            gg = int(1.5 * (res.get("pitch") or 30))
            # Ripiego geometrico per la sola attribuzione (non decide niente): se il blob
            # tocca la zona di uno solo degli step chiusi, il pezzo mosso è il suo. Serve
            # ai frame in cui il VLM tace. Se i candidati sono due o zero non si
            # attribuisce: meglio nessun link che il link sbagliato.
            tocca = [m for m, zona in self._prior.items()
                     if isinstance(m, int) and 1 <= m <= self.step_i
                     and any(bx[0] - gg <= posx[uv][0] <= bx[2] + gg
                             and bx[1] - gg <= posx[uv][1] <= bx[3] + gg
                             for uv in zona if uv in posx)]
            self.assestamento_di = tocca[0] if len(tocca) == 1 else None
        if cls_vista is not None and res["blob_box"] is not None:
            ga = g0 if self.board_ok is None else self.board_ok.astype(np.float32)
            if all(df.legscore(ga, g1, posx, *uv) < weak for uv in exp):
                for m, att in self.attese.items():
                    st_m = self.build[m - 1]
                    ab = att.get("bbox")
                    if ab is None or cls_vista not in _cls_ok(
                            df.KIND2CLS.get(st_m["kind"], st_m["kind"])):
                        continue
                    if not (bx[2] < ab[0] - gg or bx[0] > ab[2] + gg
                            or bx[3] < ab[1] - gg or bx[1] > ab[3] + gg):
                        return True
                if not self._classe_ancora_possibile(cls_vista, self.step_i + 1):
                    for m, zona in self._prior.items():
                        if not isinstance(m, int) or m > self.step_i or m in self.attese:
                            continue
                        st_m = self.build[m - 1]
                        if cls_vista not in _cls_ok(
                                df.KIND2CLS.get(st_m["kind"], st_m["kind"])):
                            continue
                        if any(bx[0] - gg <= posx[uv][0] <= bx[2] + gg
                               and bx[1] - gg <= posx[uv][1] <= bx[3] + gg
                               for uv in zona if uv in posx):
                            # A chi appartiene il pezzo che si sta muovendo. Il frame si
                            # scarta, ma lo step m è stato giudicato su uno scatto
                            # precedente a questo spostamento: chi legge il foglio deve
                            # poter aprire l'ultima foto del pezzo, non la prima.
                            self.assestamento_di = m
                            return True
        # "Nemmeno rail": il test deve guardare tutti i pin, non solo i fori di griglia.
        # Un pick fatto di sole rail passerebbe per "zero pin ovunque" e il frame
        # verrebbe scartato: es. cavetto montato da rail a rail invece che da griglia a
        # rail, golden muto perché il pezzo non è nel suo foro, pick ["-","-"] perché è
        # tutto sulle rail, e la somma delle due cose è identica a un urto. Lo step
        # uscirebbe NON GIUDICATO mentre lo scatto c'era e mostrava un errore.
        # Misurato sull'archivio: 4 frame su 13 scartati passano a giudicati, tutti con
        # pick di sole rail e tutti KO POSIZIONE.
        if not res["pins"]:
            return True                               # zero pin ovunque: solo un urto
        pins_grid = [uv for uv in res["pins"] if isinstance(uv[0], int)]
        # pin su fori GIÀ occupati (golden degli step chiusi) = è il pezzo VECCHIO
        # che si è mosso, non uno nuovo (i fori occupati non possono accogliere pin)
        occupati = set()
        for r in self.records:
            if r.get("step") is not None:
                occupati |= {df.parse_hole(h) for h in r.get("atteso", {}).get("coords", [])}
        return any(uv in occupati for uv in pins_grid)

    # ---------- valutazione di una rilevazione ----------
    def _sfida_golden(self, exp, g0, g1, posx, pitch, blob_pts, ghost=frozenset()):
        """DOVE reale della resistenza: al posto del pick cieco (geodetica
        che sbaglia il foro di +-1 e declassa i montaggi corretti a 'OK?'), si verifica se
        una COPIA TRASLATA COERENTE del footprint golden batte il piazzamento golden di
        >= M_SFIDA. Lo sfidante può vincere SOLO su fori non appartenenti al pezzo corrente
        (corpo/ombra) né a un pezzo già montato: quel calore è leakage del vicino, non un
        pin alternativo (la golden conosce il layout). Chiamato solo con esito1==LI (i fori
        golden sono già caldi -> gold non degenere). Ritorna (conc, coppia_sfidante)."""
        fp = [uv for uv in exp if isinstance(uv[0], int)]
        if not fp:
            return "MUTO", None
        excl = (set(df.zone_non_evidenza(g0, g1, posx, blob_pts, pitch).keys())
                | self.prior_zone | set(ghost)) - set(fp)

        def geo(hs):
            vals = [max(df.legscore(g0, g1, posx, *h), 1e-3) for h in hs]
            return float(np.prod(vals) ** (1.0 / len(vals)))

        gold = geo(fp)
        best, best_hs = 0.0, None
        for dr in (-1, 0, 1):
            for dc in range(-3, 4):
                if dr == 0 and dc == 0:
                    continue
                hs = [(r + dr, c + dc) for (r, c) in fp]
                if any(h not in posx for h in hs) or any(h in excl for h in hs):
                    continue
                v = geo(hs)
                if v > best:
                    best, best_hs = v, hs
        if best >= M_SFIDA * max(gold, 1e-3):
            return "DISCORDE", best_hs
        return "CONCORDE", None

    @property
    def prior_zone(self):
        """Unione delle zone dei pezzi montati BENE (i KO non entrano più)."""
        out = set()
        for z in self._prior.values():
            out |= z
        return out

    def _accumula_prior(self, st, res, posx, step_num=None, verdetto=""):
        """Lo step processato diventa 'pezzo noto' per i successivi, ma solo se il
        verdetto non è KO: un pezzo bocciato si muoverà, memorizzarlo falsa gli
        sconti. La correzione sostituisce la zona dello step."""
        if step_num is not None and str(verdetto).startswith("KO"):
            self._prior.pop(step_num, None)
            return
        zona = set()
        for h in st.get("coords", []):
            try:
                u = df.parse_hole(h)
            except Exception:
                u = None
            if u and u in posx:
                zona.add(u)
        blob = res.get("blob_pts")
        if blob is not None and len(blob):
            sil, sx, sy = df._sil_of(blob)
            sil = cv2.dilate(sil, np.ones((9, 9), np.uint8))
            for u, p in posx.items():
                x_, y_ = int(p[0]) - sx, int(p[1]) - sy
                if 0 <= y_ < sil.shape[0] and 0 <= x_ < sil.shape[1] and sil[y_, x_]:
                    zona.add(u)
        self._prior[step_num if step_num is not None else len(self._prior) + 1000] = zona

    def _arbitro_correzione(self, res):
        """Watchdog correzioni: decide se questo scatto è una correzione di uno step
        già chiuso invece del montaggio del passo atteso. Ritorna il numero 1-based
        dello step toccato, o None (flusso normale).
        Regole: il pezzo nuovo scavalca (blob della classe attesa -> avanza); solo uno
        scostamento significativo conta (l'urto no). Un blob di classe diversa da
        quella attesa, che coincide con la classe di uno step chiuso i cui fori golden
        risultano disturbati >= SOGLIA_SIGN (prev vs ora), = riposizionamento di quello
        step."""
        if res["blob_box"] is None or not self.records:
            return None
        st = self.step_atteso()
        cls_att = df.KIND2CLS.get(st["kind"], st["kind"])
        cls_vista = res["cls"]
        # stessa equivalenza del COSA (jumper/cavetto = una sola classe nel golden):
        # senza, un 'cavetto' letto su uno step dichiarato 'jumper' sembrerebbe una
        # classe diversa da quella attesa e aprirebbe la caccia alla correzione
        if cls_vista is None or cls_vista in _cls_ok(cls_att):   # pezzo nuovo atteso
            return None
        posx = {**res["pos"], **res["rpos"]}
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        best = None
        for rec in self.records:
            m = rec.get("step")
            if m is None:
                continue
            st_m = self.build[m - 1]
            if cls_vista not in _cls_ok(df.KIND2CLS.get(st_m["kind"], st_m["kind"])):
                continue                                 # il blob non è di quella classe
            uvs = [uv for uv in self.attesi_uv(st_m) if uv in posx]
            if not uvs:
                continue
            d = max(df.legscore(g0, g1, posx, *uv) for uv in uvs)
            if d >= SOGLIA_SIGN and (best is None or d > best[1]):
                best = (m, d)
        if best is None:
            return None
        # Due ipotesi anche qui, come in correzione_in_attesa: l'arbitro non deve
        # dichiarare la correzione sulla sola base della classe letta dal VLM, senza
        # chiedersi se il pezzo atteso è comparso. Caso misurato: il VLM legge "jumper"
        # su una resistenza (nel riquadro c'era anche un cavetto già montato, urtato
        # dalla mano), l'arbitro trova uno step chiuso di classe jumper coi golden
        # disturbati (0.45) e dichiara la correzione, mentre i golden dello step atteso
        # erano la cosa più calda del frame (0.74). Il puntatore si congela e tutti gli
        # step successivi slittano di uno: 6 KO su 12 valutazioni su una run montata
        # bene. Con la guardia quel frame resta un KO CLASSE isolato, un falso allarme
        # singolo invece di una run distrutta.
        # Stessa condizione dell'altra guardia: il montaggio nuovo vince se i golden
        # dello step atteso sono caldi sopra la soglia di classe e almeno quanto il
        # disturbo che ha fatto scattare la correzione.
        exp = [uv for uv in self.attesi_uv(st) if uv in posx]
        if exp:
            thr, _ = df.cls_thr(cls_att)
            s_exp = max(df.legscore(g0, g1, posx, *uv) for uv in exp)
            if s_exp >= thr and s_exp >= best[1]:
                return None
        return best[0]

    def _aggiorna_attese(self, step_num, rec, res):
        """Dopo OGNI giudizio su uno step: KO -> la zona entra (o resta) nel registro
        attese; non-KO -> la voce esce (correzione riuscita o step a posto).
        Fori sospetti = pin del radar cieco (dove il pezzo È) + golden (dove DEVE
        finire): la correzione disturba gli uni o gli altri."""
        if rec.get("step") is None:
            return
        # `OK?` e `DEBOLE` tengono lo step aperto. Questa condizione e la gemella in
        # `_aggiorna_ancora` rispondono a due domande diverse:
        #     "lo step è a posto?"                   -> no, è dubbio -> resta aperto
        #     "posso usare questa board come àncora?" -> sì, il pezzo c'è -> avanza
        # Il dubbio è proprio la situazione in cui l'operatore va a correggere: se il
        # dubbio chiudesse la voce d'attesa, la correzione vera non avrebbe più uno
        # step aperto a cui attaccarsi e ricadrebbe sullo step successivo come KO
        # POSIZIONE falso (misurato). Lo stesso vale per DEBOLE (stessa famiglia:
        # giallo, "controlla a vista"): senza questa regola il controllo "pin su fori
        # già occupati" di assestamento_sospetto scarterebbe il frame della correzione
        # e lo step resterebbe DEBOLE per sempre su una board dove il pezzo non c'era.
        # L'àncora invece avanza lo stesso: `OK?` significa "il pezzo c'è, non so se è
        # nel foro giusto", quindi la board è un riferimento valido.
        if str(rec.get("verdetto", "")) in ("OK?", "DEBOLE") \
                or str(rec.get("verdetto", "")).startswith("KO"):
            # pins_ko = dove il pezzo ERA al momento del KO, tenuti separati dai golden:
            # alla correzione diventano il FANTASMA della rimozione (vedi valuta).
            pins_ko = {uv for uv in res.get("pins", []) if isinstance(uv[0], int)}
            prev_att = self.attese.get(step_num) or {}
            if not pins_ko:
                # KO senza blob (es. rgb bianco): il pick non esiste, ma il fantasma
                # della posizione vecchia resta valido. Sostituirlo col vuoto perde la
                # memoria e la correzione buona esce OK? col pick agganciato al ghost.
                pins_ko = set(prev_att.get("pins_ko", set()))
            fori = pins_ko | set(self.attesi_uv(self.build[step_num - 1]))
            # base_g0 = la board di prima che il pezzo esistesse (il prev del primo
            # KO dello step: sui KO successivi si conserva, perché il loro prev
            # contiene il pezzo nella posizione sbagliata precedente). Serve al
            # rigiudizio solo-golden: misurato, la rimozione accendeva un golden a
            # 0.070 (soglia 0.07) contro 0.002 reali -> OK falso.
            base = prev_att.get("base_g0")
            if base is None:
                try:
                    base = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY)
                except Exception:
                    base = None
            # dove_ok: il KO era di COLORE/CLASSE con i golden già caldi: il pezzo
            # stava nel posto giusto. La correzione sarà una sostituzione sul posto,
            # cieca per il diff (es. led verde -> rosso negli stessi fori).
            mis = (rec.get("ancorata") or {}).get("misure") or {}
            sg = (rec.get("ancorata") or {}).get("soglia") or 0.07
            self.attese[step_num] = {"fori": fori,
                                     "bbox": res.get("blob_box") or prev_att.get("bbox"),
                                     "pins_ko": pins_ko, "base_g0": base,
                                     "dove_ok": (rec.get("verdetto") in ("KO COLORE", "KO CLASSE")
                                                 and bool(mis)
                                                 and all(v >= sg for v in mis.values())),
                                     # cosa_ok: la classe vista era giusta in almeno un
                                     # KO dello step -> le correzioni muovono lo stesso
                                     # pezzo, la classe non cambia (es. resistenza blu
                                     # riletta 'jumper'). Appiccicoso sui KO successivi
                                     # (un KO col VLM muto non azzera il flag).
                                     # KO COLORE conta: la classe era stata vista giusta
                                     # (un led riletto 'jumper' alla correzione del
                                     # colore darebbe un KO CLASSE su un pezzo già
                                     # riconosciuto).
                                     "cosa_ok": ((rec.get("cosa") or {}).get("esito")
                                                 in ("OK", "KO COLORE")
                                                 or prev_att.get("cosa_ok", False))}
        else:
            self.attese.pop(step_num, None)

    def attesi_caldi(self, res):
        """Frame senza blob ma con segnale di classe sui fori attesi dello step
        corrente: il pezzo bianco/translucido (rgb: cupola bianca su board bianca)
        cambia pochi pixel e non forma blob, ma i fori d'ingresso parlano (0.14-0.20
        misurati). Se i fori attesi superano la soglia di classe, il frame va
        giudicato, non scartato come assestamento.

        La promozione usa la stessa misura del giudizio. Un foro atteso solo caldo è
        la firma del leakage, non del pezzo: un urto che sposta di poco un componente
        vicino scalda un golden dello step a 0.125 col compagno morto (0.022); il
        frame vuoto verrebbe promosso, il giudizio concluderebbe "sotto soglia"
        (DEBOLE) e consumerebbe lo step su una board dove il pezzo non c'era. Qui si
        usa la media geometrica dei fori attesi, lo stesso combinatore del punteggio
        di classe (regola dura: un capo a zero azzera), così promozione e verdetto
        misurano la stessa cosa. Misurato: 5 frame senza blob giudicati in tutto
        l'archivio; i 3 con un pezzo vero passano (0.152, 0.831, 1.31), il frame
        vuoto (0.052) scende a scarto NO_BLOB.

        Mai su un frame bocciato dalle guardie: una foto sfocata rompe la
        registrazione (allineamento -1 contro ALLIN_MIN 30) e ogni legscore diventa
        inattendibile; senza questa porta ne uscirebbero un "OK" e una "correzione" su
        una mappa che non è più sulla board. Il contratto delle guardie di
        radar_analyze vale anche qui. Stessa porta in correzione_in_attesa."""
        if res.get("frame_ko"):
            return False
        st = self.step_atteso()
        if st is None:
            return False
        posx = {**res["pos"], **res["rpos"]}
        exp = [uv for uv in self.attesi_uv(st) if uv in posx]
        if not exp:
            return False
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        thr, _ = df.cls_thr(df.KIND2CLS.get(st["kind"], st["kind"]))
        vals = [max(df.legscore(g0, g1, posx, *uv), 1e-3) for uv in exp]
        return float(np.prod(vals) ** (1.0 / len(vals))) >= thr

    def _classe_ancora_possibile(self, cls_vista, step_num):
        """Un pezzo di classe `cls_vista` può ancora ARRIVARE sulla board?

        Sono candidati lo step in giudizio, quelli che vengono dopo, e quelli rimasti
        APERTI con un KO (il pezzo può tornare in mano all'operatore). Se nessuno di
        loro è di quella classe, ogni esemplare è già montato e fermo: la risposta
        del VLM sta descrivendo il passato."""
        for i, st in enumerate(self.build, 1):
            if i < step_num and i not in self.attese:
                continue
            if cls_vista in _cls_ok(df.KIND2CLS.get(st["kind"], st["kind"])):
                return True
        return False

    def correzione_in_attesa(self, res):
        """Arbitro posizionale: questo frame è la correzione di uno step in KO?
        Trigger = il blob tocca la zona attesa (bbox allargata); conferma = il
        disturbo prev-vs-ora sui fori sospetti supera SOGLIA_SIGN. Indipendente
        dalla classe (copre: classe uguale, VLM muto, fine commessa).
        Ritorna il numero 1-based dello step, o None.
        Senza blob: il micro-spostamento di +-1 colonna sovrappone sagoma vecchia e
        nuova e il rilevatore non vede nessuna macchia; il frame della correzione
        arriva qui con blob_box None. Il trigger bbox non esiste: decide la sola
        conferma sui fori dell'attesa (SOGLIA_ATTESA).
        Mai su un frame bocciato dalle guardie: mappa rotta = legscore inattendibile,
        la stessa porta di attesi_caldi."""
        if res.get("frame_ko"):
            return None
        if not self.attese:
            return None
        posx = {**res["pos"], **res["rpos"]}
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        bx = res["blob_box"]
        pitch = res.get("pitch") or 30
        g = int(1.5 * pitch)                       # tolleranza: il pezzo si sposta di poco
        best = None
        for m, att in self.attese.items():
            ab = att.get("bbox")
            if ab is not None and bx is not None:
                tocca = not (bx[2] < ab[0] - g or bx[0] > ab[2] + g
                             or bx[3] < ab[1] - g or bx[1] > ab[3] + g)
                # il blob della correzione può stare sui fori GOLDEN (dove il pezzo
                # è finito) anche lontano dal bbox del KO: la zona attesa include
                # entrambi -> il trigger passa anche se il disturbo è sui golden
                if not tocca:
                    gold = [posx[uv] for uv in att["fori"] if uv in posx]
                    tocca = any(bx[0] - g <= p[0] <= bx[2] + g
                                and bx[1] - g <= p[1] <= bx[3] + g for p in gold)
                if not tocca:
                    continue
            d = max((df.legscore(g0, g1, posx, *uv)
                     for uv in att["fori"] if uv in posx), default=0.0)
            if d >= SOGLIA_ATTESA and (best is None or d > best[1]):
                best = (m, d)
        if best is None:
            return None
        # Due ipotesi (v. SOGLIA_SIGN: la vicinanza fa 0.19 come una correzione
        # vera). Se il frame spiega bene lo step atteso (fori golden dello step
        # atteso sopra la soglia di classe) vince "montaggio nuovo": il puntatore
        # avanza e l'attesa resta nel registro per il frame giusto.
        st = self.step_atteso()
        if st is not None:
            exp = [uv for uv in self.attesi_uv(st) if uv in posx]
            if exp:
                cls_att = df.KIND2CLS.get(st["kind"], st["kind"])
                thr, _ = df.cls_thr(cls_att)
                s_exp = max(df.legscore(g0, g1, posx, *uv) for uv in exp)
                # Il VLM ha voce in capitolo. Il confronto `s_exp >= best` mette il
                # calore sui golden dello step atteso contro il calore massimo sulla
                # zona di una correzione: ma quella zona include i pin del pick al
                # momento del KO, che possono cadere sul foro di un pezzo già montato.
                # Misurato: il montaggio nuovo (0.567) perdeva per 2 millesimi contro un
                # foro di un altro step finito nella zona del KO (0.569) e diventava
                # "correzione"; da lì tutti gli step slittavano. Filtrare la zona sui
                # fori occupati è stato scartato: dove i pezzi si montano addosso
                # toglierebbe segnale vero e farebbe KO a catena.
                # Qui decide il COSA, e solo quando è decisivo: il VLM ha letto una
                # classe che è quella dello step atteso e non è quella dello step in
                # correzione -> è un pezzo nuovo, non lo spostamento di quello vecchio.
                # VLM muto o classe uguale a quella in correzione: regola invariata.
                cls_v = res.get("cls")
                cls_m = df.KIND2CLS.get(self.build[best[0] - 1]["kind"],
                                        self.build[best[0] - 1]["kind"])
                vlm_dice_nuovo = (cls_v is not None
                                  and cls_v in _cls_ok(cls_att)
                                  and cls_v not in _cls_ok(cls_m))
                # Tutti i golden accesi insieme. Il tiebreak del VLM qui sopra è cieco
                # quando i due step hanno la stessa classe (es. tre resistenze uguali
                # di seguito): `vlm_dice_nuovo` non può scattare e decide il solo
                # confronto fra massimi, che perde quando il pezzo nuovo montato nella
                # riga accanto accende un foro dello step in correzione più dei propri
                # golden (misurato: 0.356 contro 0.215). La foto viene presa come
                # correzione e da lì tutti gli step slittano di uno.
                # Il segnale che il confronto fra massimi butta via: i golden dello step
                # atteso sono accesi tutti e due (0.21 e 0.11, soglia 0.07) e prima
                # erano spenti. La correzione di uno step precedente non può accendere
                # insieme tutti i fori dello step successivo: quello è un montaggio
                # nuovo, qualunque cosa dicano i massimi.
                # Servono almeno due fori di griglia: con un foro solo la condizione
                # degenera in `s_exp >= thr`, già richiesto, e ogni correzione su uno
                # step monoforo (cavetti verso le rail) verrebbe scambiata per un
                # montaggio nuovo.
                tutti_vivi = (len(exp) >= 2
                              and all(df.legscore(g0, g1, posx, *uv) >= thr
                                      for uv in exp))
                # Il pezzo nuovo può essere montato male. Con `s_exp >= thr` in AND
                # obbligatorio, per essere riconosciuto come nuovo il pezzo dovrebbe
                # essere nel foro giusto, e chi lo monta storto non lo è mai: il golden
                # resta freddo, il guardiano non scatta, e il frame viene preso come
                # correzione di uno step precedente rimasto aperto in KO, i cui golden si
                # scaldano perché il pezzo si infila lì accanto (misurato). Quello step
                # si chiude OK su un frame che mostra un altro componente e tutto slitta.
                # Quando `vlm_dice_nuovo` è vero il calore non serve: il VLM ha letto una
                # classe che è quella dello step atteso e non è quella dello step che
                # verrebbe corretto. Una correzione del LED non può somigliare a una
                # resistenza. Il caso della resistenza spostata e riletta 'jumper' non è
                # toccato: lì la classe letta non è quella dello step atteso, quindi
                # `vlm_dice_nuovo` è già falso.
                # Dove atterra il pezzo. Con classi uguali fra step atteso e step in
                # correzione `vlm_dice_nuovo` è muto per costruzione e decide il solo
                # confronto fra massimi, che perde per un urto: il pezzo atterra nella
                # zona dello step atteso ma l'inserimento scuote il vicino e un foro
                # dell'attesa legge 0.101 >= SOGLIA_ATTESA. Il fatto misurabile è
                # l'atterraggio: una correzione vera atterra sui fori del suo step.
                # Misurato su tutte le correzioni d'archivio a classi uguali: blob ->
                # fori della correzione max 0.78 passi; il falso caso sta a 3.03 dalla
                # zona in attesa e a 0.97 dai golden attesi. Porte nel vuoto: il blob
                # tocca l'atteso (<=1.5 passi) e manca la correzione (>=2.0). I casi a
                # classi diverse restano fuori per la porta di classe.
                atterra_altrove = False
                bp = res.get("blob_pts")
                if (bp is not None and len(bp) and cls_v is not None
                        and cls_v in _cls_ok(cls_att) and cls_v in _cls_ok(cls_m)):
                    def _dmin(fori):
                        vals = [float(np.hypot(bp[:, 0] - posx[uv][0],
                                               bp[:, 1] - posx[uv][1]).min())
                                for uv in fori if uv in posx]
                        return min(vals) if vals else None
                    d_att_ = _dmin(exp)
                    d_corr_ = _dmin(self.attese[best[0]]["fori"])
                    atterra_altrove = (d_att_ is not None and d_corr_ is not None
                                       and d_att_ <= 1.5 * pitch
                                       and d_corr_ >= 2.0 * pitch)
                if vlm_dice_nuovo or atterra_altrove \
                        or (s_exp >= thr and (s_exp >= best[1] or tutti_vivi)):
                    return None
        return best[0]

    def valuta(self, res, fonte="vlm"):
        """Confronta la rilevazione (dizionario di radar_analyze) con lo step atteso e
        avanza (sempre: allarme senza blocco). Prima interroga l'arbitro correzioni: se
        lo scatto è la modifica di uno step già chiuso, riverifica quello step e non
        avanza il puntatore."""
        st = self.step_atteso()
        if self.board_ok is None:
            # board di partenza della run: il prev del PRIMO giudizio. Regola operativa:
            # la run parte con la zona degli step ancora da montare PULITA.
            try:
                self.board_ok = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY)
            except Exception:
                self.board_ok = None
        # Correzioni prima di tutto: l'arbitro posizionale guarda il registro attese e
        # vale anche a commessa finita (altrimenti una correzione dopo l'ultimo step
        # finirebbe FUORI GOLDEN). L'arbitro a classe resta come secondo sguardo (solo
        # con uno step aperto).
        m = self.correzione_in_attesa(res)
        if m is None and st is not None:
            m = self._arbitro_correzione(res)
        # Pezzo tolto. Prima di giudicare qualunque cosa: se questo scatto è una
        # rimozione, non è il montaggio di nessuno step. Il puntatore non avanza, il
        # registro attese non si tocca, l'àncora resta dov'è. Si dichiara e basta.
        bersaglio = self.build[m - 1] if m is not None else st
        if self._rimozione_sospetta(res, bersaglio,
                                    (self.attese.get(m) or {}).get("base_g0")
                                    if m is not None else None):
            n_b = (m if m is not None else
                   (self.step_i + 1 if st is not None else None))
            rec = {"step": None, "k": res["k"], "verdetto": "RIMOSSO",
                   "atteso": {"el": (bersaglio or {}).get("el", ""),
                              "cls": df.KIND2CLS.get((bersaglio or {}).get("kind"),
                                                     (bersaglio or {}).get("kind")),
                              "coords": [], "holes": (bersaglio or {}).get("holes", "")},
                   "cosa": {"cls_vista": res.get("cls"), "col_vista": None,
                            "col_atteso": None, "fonte": fonte, "esito": "NON VERIFICATO"},
                   "spiegazione": (f"PEZZO TOLTO dalla board: nel riquadro la luminosita' "
                                   f"SALE (il pezzo scuriva, toglierlo schiarisce) e "
                                   f"nessun foro atteso"
                                   + (f" dello step {n_b}" if n_b else "")
                                   + " e' vivo: non e' arrivato niente. Lo scatto non "
                                     "consuma nessuno step"),
                   "report": (f"foto {res['k']}: RIMOZIONE dichiarata"
                              + (f" (zona dello step {n_b})" if n_b else ""))}
            rec["colore"] = colore_verdetto(rec["verdetto"])
            self._storicizza(rec, "rimozione", res)
            return rec
        if m is not None:                                # correzione di uno step chiuso
            # Fantasma: i pin memorizzati al KO sono la posizione vecchia del pezzo.
            # La rimozione li accende per forza nel diff della correzione (subito o
            # molti step dopo, la voce non scade), quindi non sono una controprova: si
            # passano a _giudica da ignorare, e la voce esce dal registro in
            # _aggiorna_attese a correzione riuscita.
            att_m = self.attese.get(m, {})
            ghost = (set(att_m.get("pins_ko", set()))
                     - set(self.attesi_uv(self.build[m - 1])))
            rec = self._giudica(self.build[m - 1], res, fonte, m,
                                ghost_ko=frozenset(ghost),
                                base_g0=att_m.get("base_g0"),
                                dove_ok_ko=att_m.get("dove_ok", False),
                                cosa_ok_ko=att_m.get("cosa_ok", False),
                                correzione=True)
            rec["correzione_di"] = m
            self._regole_osservabilita(rec, res, self.build[m - 1])
            # zona del KO precedente nel record: nel diff della correzione quei fori
            # si accendono per la rimozione del pezzo; le viste li disegnano grigi
            # invece che caldi, altrimenti sembrano residui inspiegati
            att_m = self.attese.get(m) or {}
            rec["rimozione"] = {"bbox": att_m.get("bbox"),
                                "fori": [df.name(*uv) for uv in ghost]}
            self.records[m - 1] = rec                    # sostituisce il verdetto di quello step
            self._storicizza(rec, "correzione", res)     # ...ma NON nello storico: lì resta tutto
            self._aggiorna_attese(m, rec, res)           # correzione riuscita -> voce rimossa
            self._accumula_prior(self.build[m - 1], res, {**res["pos"], **res["rpos"]},
                                 step_num=m, verdetto=rec.get("verdetto", ""))
            self._aggiorna_ancora(rec, res)
            return rec                                   # puntatore fermo: il passo corrente è ancora da fare

        if st is None:                                   # oltre il golden: dichiarato, niente verdetto
            rec = {"step": None, "k": res["k"], "verdetto": "FUORI GOLDEN",
                   "colore": colore_verdetto("FUORI GOLDEN"),
                   "report": (f"foto {res['k']}: inserimento FUORI GOLDEN "
                              f"(la commessa {self.cid} ha {len(self.build)} step, tutti gia' visti)")}
            self.records.append(rec)
            self._storicizza(rec, "fuori_golden", res)
            return rec

        rec = self._giudica(st, res, fonte, self.step_i + 1)
        self._regole_osservabilita(rec, res, st)
        self.records.append(rec)
        self._storicizza(rec, "nuovo", res)
        self._accumula_prior(st, res, {**res["pos"], **res["rpos"]},
                             step_num=self.step_i + 1,
                             verdetto=rec.get("verdetto", ""))
        self.step_i += 1                                 # avanza SEMPRE (allarme senza blocco)
        self._aggiorna_attese(rec["step"], rec, res)     # KO -> zona nel registro attese
        if rec.get("_giallo_classe"):
            self.attese.pop(rec["step"], None)           # giallo di osservabilità: step CHIUSO
        self._aggiorna_ancora(rec, res)
        return rec

    # ---- regole di osservabilità ----
    R_CUPOLA_PX = 5     # disco a misura di FORO (il quadratino scuro è ~7-9 px)
    T_CUPOLA_PX = 25    # pixel saturi DENTRO il foro oltre cui la cupola lo copre

    def _regole_osservabilita(self, rec, res, st):
        """Due regole nate dalla revisione fotografica dell'archivio (36 giudizi LED).

        LED — cupola DENTRO il foro golden: se il foro atteso è vivo ma il suo
        interno è colore-cupola, la misura non distingue la gamba dal corpo che
        lo copre: il pezzo è probabilmente a posto ma NON È OSSERVABILE, e un
        OK pieno prometterebbe un'osservazione che non esiste -> OK?, controlla
        a vista, step chiuso (giallo di classe, non attesa di correzione).
        Misurato sull'archivio: 7 montaggi su 36 hanno la cupola nel foro
        (56-81 px saturi nel disco da 5 px); nei fori liberi il conteggio è 0-9.

        OPTO — verso dalla serigrafia (df.verso_opto: baricentro dell'inchiostro
        dentro il corpo, 15/15 sull'archivio). Unico componente del kit con
        l'orientamento leggibile da sopra. Capovolto -> KO POLARITA: i fori sono
        quelli giusti, ma un opto girato non funziona, e un giallo chiuderebbe lo
        step senza aspettare la correzione. Rosso e step aperto (niente
        _giallo_classe): il prior viene scartato e l'àncora non avanza, perché
        l'operatore deve rigirare il pezzo e la board cambia. Dritto -> nota nella
        spiegazione."""
        if str(rec.get("verdetto")) not in ("OK", "OK FORTE") or st is None:
            return
        cls_att = df.KIND2CLS.get(st.get("kind"), st.get("kind"))
        posx = {**res["pos"], **res["rpos"]}
        attesi = [uv for uv in self.attesi_uv(st) if uv in posx]
        if cls_att == "led" and attesi:
            hsv = cv2.cvtColor(res["img"], cv2.COLOR_BGR2HSV)
            r = self.R_CUPOLA_PX
            peggio = 0
            for uv in attesi:
                x, y = posx[uv]
                sub = hsv[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
                if sub.size == 0:
                    continue
                m = (sub[:, :, 1] > 100) & (sub[:, :, 2] > 60)
                peggio = max(peggio, int(m.sum()))
            if peggio >= self.T_CUPOLA_PX:
                rec["verdetto"] = "OK?"
                rec["colore"] = colore_verdetto("OK?")
                rec["_giallo_classe"] = True
                rec["spiegazione"] = (f"la cupola copre l'interno del foro golden "
                                      f"({peggio} px di colore nel disco da {r} px): la "
                                      f"gamba non e' osservabile e la misura non distingue "
                                      f"gamba e corpo - CONTROLLA A OCCHIO. "
                                      + str(rec.get("spiegazione", "")))
        if cls_att == "optoaccoppiatore" and attesi:
            pts = [posx[uv] for uv in attesi]
            centro = (sum(p[0] for p in pts) / len(pts),
                      sum(p[1] for p in pts) / len(pts))
            try:
                gray = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY)
            except Exception:
                gray = None
            verso, cos = df.verso_opto(gray, centro, res["pitch"])
            if verso == "dritto":
                rec["spiegazione"] = str(rec.get("spiegazione", "")) +                     f" | verso opto: montato dritto (cos {cos:+.2f})"
            elif verso == "capovolto":
                rec["verdetto"] = "KO POLARITA"
                rec["colore"] = colore_verdetto("KO POLARITA")
                rec["spiegazione"] = (f"verso opto: CAPOVOLTO, la serigrafia e' girata "
                                      f"di 180 gradi (cos {cos:+.2f}) - GIRA IL PEZZO. "
                                      + str(rec.get("spiegazione", "")))
            else:
                rec["spiegazione"] = str(rec.get("spiegazione", "")) +                     f" | verso opto: non leggibile (cos {cos:+.2f})"

    def _aggiorna_ancora(self, rec, res):
        """Verdetto non-KO = board accettata: diventa la nuova àncora. Stessa
        condizione di _aggiorna_attese (la voce esce dal registro correzioni), e le due
        vanno tenute allineate: accettare la board di uno step che resta aperto fa
        inghiottire il pezzo dall'àncora, e al frame dopo i suoi golden risultano muti
        -> KO POSIZIONE falso (misurato su tre run provando a declassare lo
        scavalcamento a OK?)."""
        if not str(rec.get("verdetto", "")).startswith("KO"):
            try:
                self.board_ok = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY)
            except Exception:
                pass

    def _storicizza(self, rec, tipo, res):
        """Copia la valutazione nello storico append-only e mette da parte i dati POSIZIONALI
        della stessa valutazione in self.pixel, con la stessa chiave `ordine`.
        `tipo`: nuovo | correzione | fuori_golden.

        I pixel non stanno nel registro (lo gonfierebbero e lì servono i verdetti): finiscono
        nel sidecar <run>_pixel.json. Si salva la mappa INTERA registrata su quella foto, non
        solo i fori della finestra: così l'elaborazione offline può ridisegnare qualunque
        zona senza rigiocare la pipeline."""
        ordine = len(self.storico) + 1
        self.storico.append({**{k: v for k, v in rec.items() if k != "report"},
                             "tipo": tipo, "ordine": ordine})
        posx = {**res["pos"], **res["rpos"]}
        self.pixel[ordine] = {
            "k": res["k"], "step": rec.get("step"), "tipo": tipo,
            "verdetto": rec.get("verdetto"),
            "blob_box": (list(res["blob_box"]) if res["blob_box"] is not None else None),
            "pins": [df.name(*uv) for uv in res["pins"]],
            "fori": {df.name(*uv): [int(p[0]), int(p[1])] for uv, p in posx.items()},
            "legscore": {df.name(*uv): round(float(v), 4) for uv, v in res["legscore"].items()},
            "filo": self._filo_corrente,
        }
        self._filo_corrente = None

    def _giudica(self, st, res, fonte, step_num, ghost_ko=frozenset(), base_g0=None,
                 dove_ok_ko=False, cosa_ok_ko=False, correzione=False):
        """Cuore della valutazione di UNO step: COSA + DOVE ancorato + sfidante + verdetto,
        report e record. NON muta step_i/records/prior_zone (lo fa valuta). step_num = numero
        1-based dello step giudicato (corrente o, in correzione, quello toccato).
        ghost_ko = fori della posizione del KO precedente (solo in correzione): il diff li
        accende per la RIMOZIONE del pezzo, non perché il pezzo ci sia -> ignorati come
        controprova (radar cieco, vicini +-1, sfidante)."""
        posx = {**res["pos"], **res["rpos"]}
        pitch = res["pitch"]
        g0 = cv2.cvtColor(res["prev"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(res["img"], cv2.COLOR_BGR2GRAY).astype(np.float32)
        cls_att = df.KIND2CLS.get(st["kind"], st["kind"])
        thr, weak = df.cls_thr(cls_att)
        attesi = self.attesi_uv(st)
        exp = [uv for uv in attesi if uv in posx]
        n_rail = df.PINS.get(cls_att, 2) - len(attesi)   # capi su rail: dichiarati non verificati

        # --- COSA (classe + colore per led/jumper: un led verde al posto del giallo
        # deve uscire KO COLORE, quindi il colore si confronta col golden) ---
        cls_vista = res["cls"]
        col_vista = self._colore_sul_pezzo(res, exp, posx, pitch, cls_att)
        col_atteso = next((c for c in COLORI if c in st["el"].lower()), None)
        nota_cosa = ""
        if cls_vista is None:
            cosa = "NON VERIFICATO"
        elif cls_vista not in _cls_ok(cls_att):          # FILI: jumper == cavetto
            if cosa_ok_ko:
                # Correzione di un KO in cui la classe era già stata vista giusta:
                # l'operatore sposta lo stesso pezzo, la classe non può cambiare.
                # Il VLM sul frame di correzione è solo conferma: se contraddice
                # (es. resistenza blu riletta 'jumper blu') si annota e basta.
                cosa = "OK"
                nota_cosa = (f" [VLM sulla correzione dice '{cls_vista}': ignorato, "
                             f"il pezzo era gia' riconosciuto '{cls_att}' al KO]")
            elif not self._classe_ancora_possibile(cls_vista, step_num):
                # Pezzo già montato, non uno nuovo. La domanda a una parola sceglie
                # l'oggetto più vistoso del ritaglio, e su un cavetto flessibile il
                # blob arcua su mezza board (misurato: riquadro al 16% del fotogramma
                # contro una mediana dell'1.1%), quindi dentro ci sta l'intera
                # commessa e il VLM può rispondere con la classe di un pezzo montato
                # molti passi prima: KO CLASSE inventato.
                # Il golden sa che quella risposta è impossibile: se di quella classe
                # ce n'è uno solo e il suo step è chiuso, non può essere il pezzo
                # appena arrivato. Non è il registro del VLM a dirlo (lì il pezzo può
                # non risultare nemmeno, se la classe non era stata letta) ma il
                # manuale. Si dichiara la classe non letta: il DOVE giudica lo stesso e
                # il motore sa gestire il silenzio, mentre un KO CLASSE falso no.
                cosa = "NON VERIFICATO"
                nota_cosa = (f" [VLM dice '{cls_vista}', ma tutti i pezzi di quella "
                             f"classe sono su step gia' chiusi: sta rileggendo un pezzo "
                             f"vecchio, non e' il nuovo -> classe non letta]")
            else:
                cosa = "KO"
        elif (cls_att in CLS_COLORE and col_atteso and col_vista
              and col_vista != col_atteso
              # verde/blu non è confondibile quando il colore viene dalla cupola
              # (es. LED blu vero dove il golden vuole verde). La deriva verde->blu
              # è misurata sui cavetti spenti; le cupole sature si leggono giuste
              # 12/12 (verdi 5/5, blu 7/7).
              and ({col_vista, col_atteso} not in COL_CONFONDIBILI
                   or (self._colore_da_cupola
                       and {col_vista, col_atteso} == {"verde", "blu"}))):
            cosa = "KO COLORE"
        else:
            cosa = "OK"

        if not attesi:
            # step solo-rail (es. cavetto da - a -): nessun foro di griglia nel golden,
            # ma i nodi rail si misurano come tutti gli altri fori: vedi
            # _verifica_solo_rail.
            sr = self._verifica_solo_rail(res, posx, thr, st.get("holes", ""))
            radar_dice = ",".join(df.name(*uv) for uv in res["pins"]) or "-"
            v_dove = (f"  {sr[1]}" if sr else
                      "  nessun segnale sui nodi rail: non verificabile")
            rep = "\n".join([
                "=" * 60,
                f"STEP {step_num}/{len(self.build)} - commessa {self.cid} - foto {res['k']}",
                _ascii(f"ATTESO: {st['el']} in {st.get('holes', '?')}"),
                "=" * 60,
                f"COSA\n  visto: {cls_vista or '(non letta)'} (fonte: {fonte})        -> CLASSE "
                + ("KO" if cosa == "KO" else cosa),
                "",
                "DOVE - nodi rail (il golden non ha fori di griglia)",
                _ascii(v_dove),
                "",
                "DOVE - radar cieco (controprova indipendente)",
                f"  senza golden il radar dice: {radar_dice}",
                "",
                "VERDETTO: " + ("KO CLASSE" if cosa == "KO" else
                                "KO COLORE" if cosa == "KO COLORE" else
                                sr[0] if sr else "NON VERIFICABILE (solo rail)"),
                "=" * 60])
            rec = {"step": step_num, "k": res["k"],
                   "atteso": {"el": st["el"], "cls": cls_att, "coords": [],
                              "holes": st.get("holes", "")},
                   "cosa": {"cls_vista": cls_vista, "col_vista": col_vista,
                            "col_atteso": col_atteso, "fonte": fonte, "esito": cosa},
                   "ancorata": {"misure": {}, "punteggio_classe": None, "soglia": thr,
                                "esito": "SOLO_RAIL", "vicini_sospetti": []},
                   "radar": {"pins": [df.name(*uv) for uv in res["pins"]],
                             "concordanza": "RAIL: capi misurati sui nodi" if sr else "-"},
                   "verdetto": ("KO CLASSE" if cosa == "KO" else
                                "KO COLORE" if cosa == "KO COLORE" else
                                sr[0] if sr else "NON VERIFICABILE"),
                   "spiegazione": (sr[1] if sr else
                                   "step solo-rail: nessun segnale sui nodi rail"),
                   "report": rep}
            rec["colore"] = colore_verdetto(rec["verdetto"])
            return rec

        # --- DOVE strato 1: misure sui fori ATTESI (il golden è l'ipotesi) ---
        S = {uv: df.legscore(g0, g1, posx, *uv) for uv in exp}
        S_mis = S                     # misura che decide (in correzione: base pre-KO)
        nota_base = ""
        corpo = None
        if cls_att in FAM_CORPO or (cls_att == "condensatore"
                                    and (self.cid, "condensatore") in df.OFF_CORPO):
            corpo = self._verifica_corpo(cls_att, res, pitch,
                                         [posx[uv] for uv in exp]) if res["blob_box"] else None
        if corpo is not None:
            esito1, righe_dove = corpo
            punteggio, det = None, None
        else:
            if base_g0 is not None and cls_att in FAM_COLONNE:
                # Correzione del footprint rigido: spostando di +-1 le sagome si
                # sovrappongono e il diff col frame prima sottostima i piedini rimasti
                # coperti (misurato: 0.020 nel diff, 0.146 reali). Contro la board
                # pre-KO la misura è pulita nei due sensi: il fantasma non esiste e il
                # piedino coperto due volte si vede.
                try:
                    S_mis = {uv: df.legscore(base_g0.astype(np.float32), g1, posx, *uv)
                             for uv in exp}
                    nota_base = "   [misura contro la board pre-KO]"
                except Exception:
                    S_mis = S
            punteggio, det = self._punteggio_classe(cls_att, exp, S_mis)
            esito1 = ("LI" if punteggio >= thr else
                      "DEBOLE" if punteggio >= weak else "NON_LI")
            righe_dove = [f"  {df.name(*uv)}: segnale {S_mis[uv]:.3f}  (soglia classe {thr})  "
                          f"{'CALDO' if S_mis[uv] >= thr else 'debole' if S_mis[uv] >= weak else 'MUTO'}"
                          for uv in exp]
            righe_dove.append(f"  {det}{nota_base}")

        # sospetto +-1: golden muto ma un vicino caldo che lo batte con margine
        sospetti = []
        if corpo is None and esito1 != "LI":
            for uv in exp:
                for nb in _vicini(uv, posx, set(exp)):
                    if nb in ghost_ko:
                        continue          # posizione del KO precedente: è la rimozione
                    sn = df.legscore(g0, g1, posx, *nb)
                    if sn >= thr and sn >= SOSP_MARG * max(S.get(uv, 0.0), 1e-6):
                        sospetti.append((df.name(*nb), sn))
        if sospetti and esito1 == "NON_LI":
            esito1 = "LI1"                              # probabile +-1, dichiarato

        # --- DOVE strato 2: radar cieco (controprova, mai contaminato dal golden) ---
        pick_muto = None                 # fori MUTI dentro il pick (v. sotto)
        pick_occupato = None             # fori di step CHIUSI dentro il pick (v. sotto)
        pins_grid = [uv for uv in res["pins"] if isinstance(uv[0], int)]
        fantasma = [uv for uv in pins_grid if uv in ghost_ko]
        pins_grid = [uv for uv in pins_grid if uv not in ghost_ko]
        if base_g0 is not None and pins_grid:
            # Fantasma misurato: la memoria pins_ko non basta quando i KO precedenti
            # erano senza pick (rail-only, no-blob). Ma la fisica basta: un pick freddo
            # contro la board pre-KO non è un pezzo (lì ora non c'è niente), è la
            # rimozione dentro il diff. Si sposta tra i fantasmi.
            try:
                gb = base_g0.astype(np.float32)
                morti = [uv for uv in pins_grid
                         if df.legscore(gb, g1, posx, *uv) < weak]
                if morti:
                    fantasma += morti
                    pins_grid = [uv for uv in pins_grid if uv not in morti]
            except Exception:
                pass
        match = len(set(pins_grid) & set(exp))
        if fantasma and not pins_grid:
            conc = "FANTASMA"
        elif not pins_grid:
            conc = "MUTO"
        elif exp and match == len(exp):
            conc = "CONCORDE"
        elif fantasma and match == len(pins_grid):
            # correzione: i pick sopravvissuti al filtro cadono tutti nei golden;
            # il resto del pick era la rimozione. Nessuna contraddizione.
            conc = "FANTASMA"
        elif match > 0:
            conc = f"PARZIALE {match}/{len(exp)}"
        else:
            conc = "DISCORDE"
        # Pick con un foro muto = non è una controprova. Il pick può contraddire
        # l'ancorato solo se la posizione che propone è fisicamente possibile: un
        # piedino in un foro dove non è cambiato niente non esiste. Caso misurato:
        #     golden   0.39  0.52  0.09   (somma 1.00, tutti vivi)
        #     pick     MUTO  0.91  0.18   (somma 1.09, ma il primo è vuoto)
        # La terna del pick vince la somma solo perché il foro centrale è il centro
        # del corpo (0.91): l'àncora slitta di una colonna e si porta dietro un foro
        # morto. fit_pins se ne accorge già e stampa "[terna incerta ... VERIFICA]",
        # ma restituisce la terna lo stesso e quella declasserebbe il montaggio a
        # "OK?". Qui il pick perde il diritto di contraddire, non di esistere: resta
        # in `radar_dice` e nel ramo KO POSIZIONE (golden muti + pezzo visto altrove),
        # dove il verdetto lo decidono comunque i golden.
        if pins_grid and conc.startswith(("PARZIALE", "DISCORDE")):
            muti = [uv for uv in pins_grid if df.legscore(g0, g1, posx, *uv) < weak]
            if muti:
                conc = "MUTO"
                pick_muto = ", ".join(df.name(*uv) for uv in muti)
        # Occupato: la seconda impossibilità, stessa logica del foro muto qui sopra.
        # Un piedino su un foro che un pezzo di uno step già chiuso occupa non è una
        # posizione possibile: lì dentro un secondo pezzo non ci entra (misurato: il
        # pick propone per il transistor una terna che include il foro della
        # resistenza dello step precedente, ancora al suo posto: è la resistenza
        # vecchia riletta, non una controprova). Come sopra: il pick perde il diritto
        # di contraddire, non di esistere.
        #
        # Lo step in giudizio va escluso: su un frame di correzione il suo record è
        # già in self.records da un KO precedente, e senza esclusione la guardia
        # squalificherebbe il pick usando i fori del pezzo che sta valutando
        # (misurato: un OK? corretto diventava OK).
        #
        # Non è un filtro sulla ricerca: vietare di cercare nei fori occupati peggiora
        # sempre (misurato tre volte). Qui si cerca come prima e si pesa il risultato
        # dopo.
        if pins_grid and conc.startswith(("PARZIALE", "DISCORDE")):
            occupati = set()
            for r in self.records:
                if r.get("step") is not None and r["step"] != step_num:
                    occupati |= {df.parse_hole(h)
                                 for h in (r.get("atteso", {}).get("coords") or [])}
            presi = [uv for uv in pins_grid if uv in occupati]
            if presi:
                conc = "OCCUPATO"
                pick_occupato = ", ".join(df.name(*uv) for uv in presi)
        # Resistenza: sui montaggi che l'ancorato conferma (esito1==LI), il pick cieco
        # sbaglia il foro di +-1 e declassa il corretto a "OK?". Si sostituisce la
        # concordanza con lo sfidante golden (coppia traslata coerente + sconto locale).
        res_sfida = (cls_att == "resistenza" and esito1 == "LI"
                     and cls_att not in FAM_SOLO_GOLDEN)   # solo-golden: sfida spenta
        if res_sfida:
            conc, _ = self._sfida_golden(exp, g0, g1, posx, pitch, res["blob_pts"],
                                         ghost=ghost_ko)
        radar_dice = ",".join(df.name(*uv) for uv in res["pins"]) or "-"

        # --- DOVE strato 2-ter: il corpo (corpo.py) ---
        # buzzer e trimmer: i piedini stanno sotto il corpo e non sono osservabili.
        # Il trimmer copre 8-12 fori, il buzzer 13-18, tutti ugualmente caldi: il pick
        # vaga fra quelli e trascina il verdetto (stesso montaggio, 4 verdetti diversi).
        # Qui si guarda il corpo e basta.
        liv_corpo = det_corpo = rif = None
        if USA_CORPO and cls_att in cp.FAM_CORPO and res["blob_pts"] is not None:
            Sc = {uv: df.legscore(g0, g1, posx, *uv) for uv in exp}
            liv_corpo, det_corpo = cp.verifica(Sc, exp)
            # riferimento memorizzato: solo pezzi bassi (il buzzer è alto, la parallasse
            # gli cambia la faccia fra uno scatto e l'altro). Assente = si salta.
            if cls_att in cp.FAM_RIFERIMENTO and exp and liv_corpo == "COPERTO":
                # Il riferimento può solo peggiorare un COPERTO, mai riabilitare uno
                # SCOPERTO (un golden nudo a 0.01 uscirebbe OK? se il riferimento
                # sovrascrivesse lo SCOPERTO con DIVERSO).
                rif = cp.confronta_riferimento(res.get("img"), posx, exp[len(exp)//2],
                                               self.cid, step_num)
                if rif and rif[1] >= cp.T_RIF and abs(rif[0]) > 0.5:
                    # riconosciuto altrove: non è un dubbio, è una localizzazione
                    # (misurato: golden coperti dal corpo largo ma picco a -1.0, NCC 0.78)
                    liv_corpo = "SPOSTATO"
                    det_corpo = (f"il riferimento memorizzato RICONOSCE il pezzo a "
                                 f"{rif[0]:+.1f} colonne da dove dovrebbe "
                                 f"(somiglianza {rif[1]:.2f})")
                elif rif and rif[1] < cp.T_RIF:
                    liv_corpo = "DIVERSO"
                    det_corpo = (f"il riferimento memorizzato non lo riconosce qui: "
                                 f"picco a {rif[0]:+.1f} colonne, somiglianza {rif[1]:.2f}")
            conc = {"COPERTO": "CORPO: copre i fori del golden",
                    "DIVERSO": "CORPO: il riferimento non concorda",
                    "SPOSTATO": "CORPO: il riferimento lo riconosce altrove",
                    "SCOPERTO": "CORPO: i fori del golden sono nudi"}[liv_corpo]

        # --- DOVE strato 2-quater: il filo (filo.py) ---
        # jumper e cavetti, due rami separati dalla fisica: il flessibile appoggia sul
        # foro un collare grasso (NCC 0.60, e 0.01 a una colonna) mentre il suo filo
        # galleggia in aria; il rigido è sottile (NCC 0.26) ma la sua sagoma sta dove
        # sta il pezzo (0.20 passi). L'NCC dove il pezzo è grasso, la sagoma dove è
        # sottile e piatto.
        liv_filo = det_filo = None
        if USA_FILO and cls_att in fl.FAM_FILO and exp and res["blob_box"] is not None:
            el = st.get("el", "")
            if fl.flessibile(el):
                Sf = {uv: df.legscore(g0, g1, posx, *uv) for uv in exp}
                liv_filo, det_filo = fl.verifica_flessibile(Sf, exp)
            else:
                col = fl.colore(el)
                r = None
                if col:
                    r = fl.verifica_rigido(res["img"], posx, exp, col,
                                           df.board_mask(posx, res["img"].shape, grow=20),
                                           pitch, res["blob_pts"])
                # Escalazione sagoma-diff (la sostituzione secca del colore regrediva,
                # questa no): il colore resta la misura primaria; la sagoma-diff promuove
                # a KO solo con la firma completa (capo agganciato a un altro foro + NCC
                # acceso + golden muto) quando il colore è in dubbio o non applicabile
                # (verde). Mai declassa.
                if r is None or r[0] in ("SCENTRATO", "FUORI SCOPO"):
                    ri = fl.verifica_inserzione(res, posx, exp, pitch, thr)
                    if ri and ri[0] == "LONTANO":
                        r = ri
                # Controprova collare: un ALLINEATO geometrico col capo a 0.54 passi
                # dal golden, ma golden nudo (0.045) e vicino coperto (0.62): il
                # cavetto era una riga sotto. Il capo della sagoma è la cima del
                # collare (misurato: 0.25 passi dal golden, 0.90 dal vero foro), la
                # geometria non decide. Decide l'NCC, con le popolazioni misurate su
                # tutti i cavetti in archivio:
                #   golden col cavetto dentro:   0.069-0.254
                #   golden col cavetto accanto:  0.045
                #   vicino che ospita il collare o attraversato: 0.34-0.92
                # L'attraversamento verso la rail scalda un vicino anche nei montaggi
                # buoni (vicino a 0.87 su un golden buono): il vicino caldo da solo non
                # prova niente. Serve il golden veramente nudo, sotto T_FILO_NUDO, più
                # il vicino coperto.
                if r and r[0] == "ALLINEATO":
                    capi_px = (r[2].get("capi") or [])
                    for uv in exp:
                        s_g = df.legscore(g0, g1, posx, *uv)
                        if s_g >= T_FILO_NUDO or not capi_px:
                            continue
                        cattivo = None
                        for nb in _vicini(uv, posx, set(exp)):
                            s_n = df.legscore(g0, g1, posx, *nb)
                            if s_n >= fl.T_COPERTO:
                                cattivo = (nb, s_n)
                                break
                        if cattivo:
                            r = ("NEL VICINO",
                                 f"golden {df.name(*uv)} nudo ({s_g:.2f}) ma il "
                                 f"vicino {df.name(*cattivo[0])} e' coperto "
                                 f"({cattivo[1]:.2f}): il collare sta li'",
                                 r[2])
                            break
                if r:
                    liv_filo, det_filo, geom = r
                    # la SAGOMA, non solo il verdetto: si mette da parte per il sidecar,
                    # come si fa col percorso. Senza, il pannello non può mostrare su
                    # quale oggetto la maschera si è agganciata.
                    self._filo_corrente = {"livello": liv_filo, "dettaglio": det_filo,
                                           **geom}
                    # Cavetto flessibile dove il golden dice rigido. L'equivalenza
                    # jumper==cavetto del COSA (_cls_ok) è necessaria (il golden scrive
                    # kind='jumper' anche per i flessibili) ma inghiottirebbe l'errore
                    # vero: cavetto flessibile montato dove il golden chiede il rigido,
                    # letto 'cavetto' dal VLM, uscito OK?.
                    # La parola del VLM da sola non basta a bocciare: 3 'cavetto' su
                    # golden rigidi in archivio e uno solo è un flessibile vero (negli
                    # altri la parola veniva dal riquadro sbagliato).
                    # Il trigger è il solo golden che dice 'rigido' in chiaro, non "non
                    # flessibile": i cavetti con testo muto sono flessibili veri e
                    # bocciarli sarebbero falsi allarmi.
                    # Decide la fisica: la guaina nera al capo della sagoma
                    # (df.guaina_v2, calibrata 3/3 sulle guaine vere, 0 falsi sui capi
                    # rigidi nudi). Serve la sagoma agganciata e non fuori scopo: la
                    # stessa fisica che squalifica la sagoma squalifica il suo capo.
                    if (cosa == "OK" and cls_vista == "cavetto"
                            and "rigido" in el.lower()
                            and liv_filo != "FUORI SCOPO" and geom.get("capi")):
                        bp = res.get("blob_pts")
                        for capo in geom["capi"]:
                            ax = None
                            if bp is not None and len(bp) > 5:
                                near = bp[np.hypot(bp[:, 0] - capo[0],
                                                   bp[:, 1] - capo[1]) < 2.5 * pitch]
                                if len(near) > 5:
                                    v = np.array([capo[0] - near[:, 0].mean(),
                                                  capo[1] - near[:, 1].mean()])
                                    nv = float(np.linalg.norm(v))
                                    if nv > 1:
                                        ax = v / nv
                            if df.guaina_v2(res["img"], capo, ax, pitch) is not None:
                                cosa = "KO"
                                nota_cosa = (" [golden 'rigido', il VLM dice 'cavetto'"
                                             " e la guaina nera al capo lo conferma:"
                                             " e' un flessibile]")
                                break
            if liv_filo:
                conc = {"COPERTO": "FILO: il collare copre il foro",
                        "SCOPERTO": "FILO: il foro e' nudo",
                        "ALLINEATO": "FILO: capo sul foro atteso",
                        "SCENTRATO": "FILO: capo vicino ma non sul foro",
                        "NEL VICINO": "FILO: il collare sta sul foro accanto",
                        "LONTANO": "FILO: il pezzo e' altrove",
                        "FUORI SCOPO": "FILO: colore fuori dalla portata del ramo"}[liv_filo]

        # --- DOVE strato 2-quinquies: la cupola (cupola.py, disattivata) ---
        # Il led: un capo sotto la cupola (caldo) e uno nudo (freddo). Si verifica il
        # primo, il secondo si deduce dalla campata del golden. Il ramo promuove
        # soltanto: se la cupola non si vede tace, perché l'NCC per-foro qui è
        # fuorviante (il foro accanto supera la soglia più spesso di quello giusto,
        # 18/49 contro 15/49).
        # --- Regola solo-golden con àncora: non "fori cambiati nel diff prev" ma
        # "fori vivi contro l'ultima board accettata". Il cambiamento è cieco in due
        # modi, misurati:
        # 1) inserimento inghiottito da un frame di assestamento -> al giudizio il
        #    prev contiene già il pezzo: golden 0.01, KO falso;
        # 2) base_g0 (prev del primo KO) già col pezzo dentro -> correzioni a ~0 sui
        #    golden e fantasmi 0.19-0.26 sugli esterni -> KO a catena.
        # Contro l'àncora la presenza è sempre visibile e la rimozione di una
        # posizione vecchia non esiste (pulito=pulito). Ombre a soglia doppia. ---
        liv_led = det_led = None                         # (ramo led-ncc assorbito qui)
        gold_ok = gold_det = None
        S_verd = None            # misure che decidono (vs àncora): finiscono nel
                                 # record e sul foglio, così pannello e verdetto
                                 # dicono gli stessi numeri
        if cls_att in FAM_SOLO_GOLDEN and exp:
            base = max(thr, T_LED_BASE) if cls_att == "led" else thr
            try:
                # Soglia doppia solo per ombra e pezzo-noto. La 'zona-spessa' è il
                # corpo del pezzo corrente: sul suo stesso foro golden quel corpo è
                # il segnale atteso, raddoppiare lì boccia i montaggi giusti
                # (misurato: led giusto a 0.13 contro 0.20 perché la cupola copriva
                # il proprio foro).
                ombra = {uv for uv, lab in df.zone_non_evidenza(
                             g0, g1, posx, res["blob_pts"], pitch).items()
                         if lab != "zona-spessa"}
            except Exception:
                ombra = set()
            # In correzione il riferimento è `base_g0`, non l'àncora. Scartare il diff
            # prev in correzione è giusto (contiene la rimozione del pezzo), ma il
            # ripiego dev'essere la board di prima che il pezzo esistesse, che
            # `_aggiorna_attese` conserva apposta in `base_g0`.
            # L'àncora invecchia: si aggiorna solo sui verdetti non-KO, quindi in una
            # run con KO consecutivi resta indietro di molti scatti, e lì il `legscore`
            # non misura più il singolo foro ma la deriva della zona (misurato: un
            # golden ancora vuoto leggeva 0.458, e un foro che nessuno step tocca mai
            # 0.486; contro `base_g0` lo stesso golden legge 0.009).
            ga = (base_g0.astype(np.float32)
                  if (correzione and base_g0 is not None) else
                  (g0 if self.board_ok is None else self.board_ok.astype(np.float32)))
            Sa = {uv: df.legscore(ga, g1, posx, *uv) for uv in exp}
            parti = []
            gold_ok = True
            S_verd = {}
            for uv in exp:
                # primo scatto: vale la migliore delle due evidenze, presenza vs
                # àncora o evento d'inserzione nel diff prev (lì il prev è pulito per
                # costruzione; la stessa foto registrata due volte non ricade uguale
                # al millesimo: 0.06 vs àncora, 0.09 nel diff).
                # In correzione il diff prev contiene la rimozione: solo l'àncora.
                S_verd[uv] = Sa[uv] if correzione else max(Sa[uv], S.get(uv, 0.0))
            picco = max(S_verd.values(), default=0.0)
            for uv in exp:
                if cls_att == "led" and len(exp) >= 2 and S_verd[uv] < picco:
                    # LED, doppio pavimento: la cupola copre l'anodo (0.55-1.27) ma
                    # la gamba nuda del catodo vale 0.069-0.24 (non passerebbe mai a
                    # 0.10) e il foro vuoto 0.003-0.05. Il picco (cupola) paga
                    # T_LED_BASE, al capo nudo basta T_LED_NUDO.
                    sgl = T_LED_NUDO * (2 if uv in ombra else 1)
                else:
                    sgl = base * (2 if uv in ombra else 1)
                s_eff = S_verd[uv]
                vivo = s_eff >= sgl
                gold_ok = gold_ok and vivo
                parti.append(f"{df.name(*uv)}={s_eff:.2f}"
                             + ("/prev" if s_eff > Sa[uv] else "")
                             + ("/ombra" if uv in ombra else "")
                             + (">=" if vivo else "<") + f"{sgl:.2f}")
            gold_det = "  ".join(parti)
            conc = "SOLO GOLDEN (radar spento per questa classe)"
            # --- Scavalcamento: golden tutti caldi ma entrambi i fori oltre i capi,
            # lungo l'asse del pezzo, vivi anche loro.
            # Il filo che passa sopra un foro lo accende più di un piedino dentro
            # (misurato nel ramo filo: attraversamenti 0.55-0.96, piedini 0.03-0.16),
            # quindi piedini a -1/+1 oltre i golden = golden caldi = OK falso.
            # Solo gli ESTERNI sull'asse: i laterali sono ombra/corpo anche nei
            # montaggi giusti (v. sfidante golden). Lo spostamento secco
            # di 1 non serve coprirlo qui: lascia un golden muto e il KO scatta già.
            # Asse definito solo se i capi stanno sulla stessa riga (il caso di
            # queste classi); un capo esterno fuori mappa -> check muto, dichiarato.
            if gold_ok and len(exp) >= 2:
                us = {uv[0] for uv in exp}
                if len(us) == 1:
                    u0 = us.pop()
                    v_lo = min(v for _, v in exp)
                    v_hi = max(v for _, v in exp)
                    vals = []
                    for uv in ((u0, v_lo - 1), (u0, v_hi + 1)):
                        # esterno nella zona del KO precedente = caldo di rimozione,
                        # non evidenza di scavalco: guardia muta
                        if uv not in posx or uv in ghost_ko:
                            vals = None
                            break
                        s_e = df.legscore(ga, g1, posx, *uv)
                        sgl_e = base * (2 if uv in ombra else 1)
                        vals.append((uv, s_e, sgl_e))
                    if vals and all(s_e >= sgl_e for _, s_e, sgl_e in vals):
                        gold_ok = "SCAVALCO"
                        gold_det += "  |  esterni vivi: " + "  ".join(
                            f"{df.name(*q)}={s_e:.2f}>={sgl_e:.2f}"
                            for q, s_e, sgl_e in vals)
            # Scavalcamento monolatero: un solo piedino oltre il golden, il filo
            # scavalca il capo e lo accende. Contro l'àncora le tre popolazioni si
            # separano in assoluto (misurate su tutte le run):
            #   esterni quieti (montaggi buoni)          <= 0.089
            #   piedino scavalcato (4 scavalchi GT)       0.137-0.180
            #   arco del reoforo sopra l'esterno (buone)  >= 0.256
            # -> banda MONO_PIEDINO. Un rapporto col capo non funziona: il capo
            # scavalcato forte (0.715) butta il piedino vero fuori rapporto.
            # Solo al primo scatto: in correzione l'arco del reoforo appena
            # maneggiato sta a 0.256-0.516, a 6 millesimi dal tetto della banda, e il
            # rischio di innescare un loop di KO non vale la copertura; lì decidono
            # golden vivi + guardia bilaterale.
            # Niente condizione sul pick: aggancia un foro o il vicino a caso
            # (misurato).
            # LED escluso: la banda misura un piedino oltre il capo, ma sul led il
            # foro esterno tiepido è la cupola, non una gamba (larga quasi due passi,
            # copre i fori del golden anche quando il led sta altrove). Sui 5 casi
            # dell'archivio la regola scatta 4 volte su resistenze (reoforo premuto
            # troppo che va davvero oltre, confermato a mano) e 1 volta su un LED,
            # l'unico falso allarme accertato.
            if (USA_MONO_PIEDINO and cls_att != "led"
                    and gold_ok is True and len(exp) >= 2 and not correzione):
                us = {uv[0] for uv in exp}
                if len(us) == 1:
                    u0 = us.pop()
                    v_lo = min(v for _, v in exp)
                    v_hi = max(v for _, v in exp)
                    for capo_v, est_v in ((v_lo, v_lo - 1), (v_hi, v_hi + 1)):
                        est = (u0, est_v)
                        if est not in posx or est in ghost_ko:
                            continue
                        s_est = df.legscore(ga, g1, posx, *est)
                        if MONO_PIEDINO[0] <= s_est <= MONO_PIEDINO[1]:
                            gold_ok = "SCAVALCO"
                            gold_det += (f"  |  monolatero: esterno "
                                         f"{df.name(*est)}={s_est:.2f} nella banda "
                                         f"del piedino {MONO_PIEDINO}")
                            break
            # Sostituzione sul posto: pezzo sostituito negli stessi fori dopo KO
            # COLORE/CLASSE. La gamba nuda del led vs àncora vale ~0.08 (sotto il
            # pavimento 0.10) e l'AND sui golden boccerebbe il montaggio giusto. Ma il
            # DOVE era già buono al KO, il COSA ora è OK, la sostituzione ha mosso i
            # golden nel diff prev e almeno un capo è vivo forte vs àncora (cupola
            # 0.92): basta.
            if gold_ok is False and dove_ok_ko and cosa == "OK":
                attivita = max((S.get(uv, 0.0) for uv in exp), default=0.0)
                presenza = max((Sa.get(uv, 0.0) for uv in exp), default=0.0)
                if attivita >= SOGLIA_ATTESA and presenza >= base:
                    gold_ok = True
                    gold_det += (f"  |  SOSTITUZIONE SUL POSTO: KO precedente di "
                                 f"colore/classe (DOVE gia' buono), attivita' "
                                 f"{attivita:.2f}, presenza vs ancora {presenza:.2f}")

        liv_cup = det_cup = None
        if USA_CUPOLA and cls_att in cu.FAM_CUPOLA and exp:
            r = cu.verifica({uv: df.legscore(g0, g1, posx, *uv) for uv in exp}, exp)
            if r:
                liv_cup, det_cup = r
                conc = "CUPOLA: il corpo copre un capo atteso"

        # --- incrocio -> verdetto ---
        if cosa == "KO":
            verdetto = "KO CLASSE"
            spiega = (f"atteso {cls_att}, visto {cls_vista}. "
                      f"Il DOVE qui sotto e' valutato comunque.")
        elif cosa == "KO COLORE":
            verdetto = "KO COLORE"
            spiega = (f"classe giusta ma COLORE sbagliato: atteso {col_atteso}, "
                      f"visto {col_vista}. Il DOVE qui sotto e' valutato comunque.")
        elif gold_ok is not None:
            # Regola solo-golden: binaria
            if gold_ok == "SCAVALCO":
                # KO, non dubbio: la firma è doppia, esterni vivi e golden coperti dal
                # filo. Il foro esterno che si accende non è un golden, e un capo che
                # finisce lì è uno spostamento a tutti gli effetti. Declassare a "OK?"
                # non basta comunque: "OK?" chiude la voce d'attesa e aggiorna l'àncora
                # (v. _aggiorna_attese / _aggiorna_ancora, condizioni gemelle) e,
                # misurato, 4 righe prima OK diventavano KO.
                verdetto = "KO POSIZIONE"
                spiega = ("SOLO-GOLDEN: SCAVALCAMENTO - i piedini sono OLTRE i fori "
                          f"golden e il filo li copre ({gold_det})")
            elif gold_ok:
                # La sostituzione si vede sul foro. Dopo un KO COLORE il pezzo
                # bocciato spesso non viene rimosso: contro base_g0 (o l'àncora,
                # entrambe di prima che il pezzo esistesse) il suo golden resta "vivo"
                # per sempre, e qualunque frame attribuito alla correzione uscirebbe OK
                # (misurato: un led del colore giusto montato altrove accreditava la
                # correzione mentre quello bocciato stava ancora nel golden).
                # L'attività prev-diff sul golden non separa (correzioni vere
                # 0.104-0.984, il falso caso 0.101 dal leakage della cupola accanto).
                # Separa il colore statico sul foro: la correzione di un KO COLORE
                # promette "stesso foro, colore giusto", quindi il colore atteso deve
                # stare fisicamente sul golden. Misurato (disco 2 passi, px saturi
                # della tinta attesa): sostituzioni vere 243-3648; il falso caso 53 px
                # in tutto. Tinta fuori tavola o non misurabile -> il check tace.
                sost_ko = None
                if correzione and dove_ok_ko and col_atteso and cls_att in CLS_COLORE:
                    cnt = [_px_colore(res["img"], posx[uv], 2.0 * pitch, col_atteso)
                           for uv in exp]
                    if cnt and not any(c is None for c in cnt):
                        if max(cnt) < T_COL_SOST:
                            sost_ko = max(cnt)
                if sost_ko is not None:
                    verdetto = "KO POSIZIONE"
                    spiega = (f"correzione di un KO COLORE, ma il colore atteso "
                              f"({col_atteso}) non sta sul golden: {sost_ko} px "
                              f"saturi nel disco di 2 passi (sostituzioni vere: "
                              f">=243). Il golden e' vivo contro il riferimento "
                              f"perche' il pezzo del KO non e' mai stato tolto; il "
                              f"pezzo nuovo e' altrove ({gold_det})")
                else:
                    verdetto = "OK"
                    spiega = ("SOLO-GOLDEN: tutti i fori golden VIVI contro l'ultima "
                              f"board accettata ({gold_det})")
            else:
                verdetto = "KO POSIZIONE"
                spiega = ("SOLO-GOLDEN: fori golden MUTI contro l'ultima board "
                          f"accettata ({gold_det})")
        elif liv_led is not None:
            # ramo LED-NCC: tetto dichiarato, mai OK FORTE (capi sotto/accanto alla cupola)
            if liv_led == "CONFERMATO":
                verdetto = "OK"
                spiega = det_led + ". Ramo LED-NCC: niente pick, niente percorso"
            else:
                verdetto = "KO POSIZIONE"
                spiega = det_led + ". Ramo LED-NCC: il capo golden vuoto smentisce"
        elif liv_cup is not None:
            # TETTO DICHIARATO: mai OK FORTE. Un solo capo è osservabile, e non sempre.
            verdetto = "OK"
            spiega = (det_cup + ". La cupola nasconde i propri piedini: il capo coperto "
                      "e' la prova, l'altro segue dalla campata")
        elif liv_filo is not None:
            # TETTO DICHIARATO: mai OK FORTE. Nel flessibile i piedini stanno dentro il
            # collare e non sono osservabili; nel rigido la sagoma è una prova indiretta
            # e 6 capi su 20 restano oltre il mezzo foro.
            if liv_filo == "FUORI SCOPO":
                # il ramo si dichiara non applicabile: decide lo strato ancorato, che qui
                # è l'unica misura di cui ci si può fidare. Mai una bocciatura del filo.
                verdetto = "OK" if esito1 == "LI" else ("DEBOLE" if esito1 == "DEBOLE"
                                                        else "NON VERIFICABILE")
                spiega = (det_filo + f". Verdetto dal solo strato ancorato (esito {esito1})")
            elif liv_filo in ("COPERTO", "ALLINEATO"):
                verdetto = "OK"
                spiega = det_filo + (". Il collare copre il foro: i piedini stanno dentro, "
                                     "non osservabili" if liv_filo == "COPERTO" else
                                     ". La sagoma del filo finisce sul foro atteso")
            elif liv_filo == "SCENTRATO":
                # golden davvero nudo -> non è un dubbio (misurato: golden a 0.002
                # con capo a 0.71 passi usciva OK?). Il dubbio SCENTRATO vale solo se
                # il foro ha un minimo di segnale (golden a 0.12 -> resta OK?). Misura
                # generosa: il massimo fra diff prev e àncora, si boccia solo se
                # entrambe dicono vuoto.
                ga2 = g0 if self.board_ok is None else self.board_ok.astype(np.float32)
                mis_n = {uv: max(df.legscore(g0, g1, posx, *uv),
                                 df.legscore(ga2, g1, posx, *uv)) for uv in exp}
                if mis_n and all(v < T_FILO_NUDO for v in mis_n.values()):
                    verdetto = "KO POSIZIONE"
                    spiega = (f"{det_filo} - e il foro golden e' NUDO ("
                              + " ".join(f"{df.name(*uv)}={v:.2f}"
                                         for uv, v in mis_n.items())
                              + "): il capo li' non c'e'")
                else:
                    verdetto = "OK?"
                    spiega = f"{det_filo} - il capo non e' sul foro atteso, CONTROLLA A OCCHIO"
            elif liv_filo == "NEL VICINO":
                # controprova collare: non un dubbio geometrico ma una prova
                # positiva (golden nudo + vicino coperto) -> bocciatura piena
                verdetto = "KO POSIZIONE"
                spiega = det_filo + " - il cavetto e' nel foro accanto"
            elif liv_filo == "LONTANO" and esito1 == "LI":
                # Ansa fuori board. Un cavetto che esce ad arco dal bordo della board:
                # il sagomatore prende come capo l'estremità dell'ansa invece del capo
                # infilato e lo dichiara lontano dal golden, mentre lo strato ancorato
                # misura il golden a 8.8 volte la soglia. I due si contraddicono, e
                # l'ancorato è una misura mentre il sagomatore è un inseguitore di
                # sagome.
                # Misurato sull'archivio: 6 LONTANO in tutto, separazione netta. I 2 con
                # esito ancorato LI stanno a 8.8x la soglia e sono entrambi falsi allarmi
                # confermati dal GT; gli altri 4 hanno esito LI1 o DEBOLE e stanno a
                # 0.1-0.2x, col pezzo davvero altrove. Fra 0.2x e 8.8x non c'è nessun
                # campione: la condizione è `esito1 == LI`, senza soglie nuove.
                verdetto = "OK"
                spiega = (f"{det_filo} - MA i fori golden sono tutti VIVI nello strato "
                          f"ancorato (esito LI): la sagoma ha inseguito l'ansa del filo, "
                          f"non il capo. Decide la misura")
            else:
                verdetto = "KO POSIZIONE"
                spiega = det_filo
        elif cls_att in fl.FAM_FILO and esito1 in ("LI", "DEBOLE"):
            # Ramo filo non agganciato: la sagoma non si è attaccata a niente (il caso
            # del cavetto verde, che filo.py dichiara fuori dalla portata della
            # segmentazione per tinta). Senza questa riga si finirebbe nel ramo
            # generico e a decidere resterebbe il pick cieco, che per i fili è già
            # dichiarato inaffidabile (aggancia la cupola del led vicino o la rail
            # attraversata; la telemetria non ne disegna nemmeno le X) e declasserebbe
            # a "OK?" montaggi coi golden accesi. Se il ramo filo tace, decide il solo
            # strato ancorato, identico a ciò che si fa quando il ramo dichiara FUORI
            # SCOPO. Mai una bocciatura dal pick, e mai OK FORTE (tetto dei fili).
            verdetto = "OK" if esito1 == "LI" else "DEBOLE"
            spiega = ("il ramo filo non ha agganciato la sagoma (colore fuori dalla "
                      "portata della segmentazione): verdetto dal solo strato "
                      f"ancorato (esito {esito1}). Il pick cieco direbbe "
                      f"{radar_dice} ({conc}) ma sui fili non fa testo")
        elif liv_corpo is not None:
            # Tetto dichiarato: mai OK FORTE. I piedini non sono osservabili e la
            # conferma viene dalla rigidità del pezzo, non da una misura sui fori. In
            # archivio ci sono 1 trimmer e 2 buzzer: verificata la ripetibilità, non
            # l'accuratezza.
            if liv_corpo == "COPERTO":
                # COPERTO da solo non dimostra niente su un corpo largo: il buzzer è un
                # disco di ~140 px su un passo di 32 e copre 13-18 fori, quindi copre i
                # golden anche da posizioni sbagliate (misurato: fori attesi 0.68/0.97
                # e fori reali 0.79/0.94, indistinguibili). Decide il conteggio righe,
                # che è discreto.
                rc = self._righe_coperte(cls_att, res, getattr(self, "_corpo_geo", None),
                                         exp)
                if rc and rc[0] != rc[1]:
                    verdetto = "KO POSIZIONE"
                    spiega = (f"il corpo copre i fori attesi, ma sta {rc[0] - rc[1]:+d} "
                              f"righe fuori posto: sotto la riga dei piedini ne copre "
                              f"{rc[0]} invece di {rc[1]}. Il conteggio delle righe e' "
                              f"discreto e non dipende dall'offset del corpo")
                else:
                    verdetto = "OK"
                    spiega = ("il pezzo copre i fori attesi" + (
                        f" e il riferimento memorizzato lo riconosce qui (somiglianza {rif[1]:.2f})"
                        if rif else "") +
                        ". I piedini sono rigidi sotto il corpo: non osservabili, "
                        "confermati dalla posizione del pezzo"
                        + (f" e dal conteggio righe ({rc[0]} coperte sotto i piedini,"
                           f" come atteso)" if rc else ""))
            elif liv_corpo == "SPOSTATO":
                verdetto = "KO POSIZIONE"
                spiega = (det_corpo + ". I fori golden sono coperti solo perche' il "
                          "corpo e' largo: il pezzo sta altrove")
            elif liv_corpo == "DIVERSO":
                verdetto = "OK?"
                spiega = f"i fori attesi sono coperti ma {det_corpo} - CONTROLLA A OCCHIO"
            else:
                verdetto = "KO POSIZIONE"
                spiega = (det_corpo + ". Uno spostamento di UNA colonna e' preso in 7 casi "
                          "su 8, di DUE colonne sempre")
        elif esito1 == "LI" and conc == "CONCORDE":
            # Un solo verdetto verde: OK. La doppia conferma (golden caldo + radar
            # concorde) non è confrontabile fra righe: uscirebbe solo dove il pick
            # cieco decide (bottone, opto, transistor: 9 righe su 237), mentre
            # solo-golden, filo, corpo e cupola la dichiarano impossibile per
            # costruzione (i piedini non sono osservabili, manca la seconda misura
            # indipendente). Un "OK" su una resistenza e un "OK FORTE" su un bottone
            # sembrerebbero due qualità di montaggio diverse, mentre la differenza è
            # solo la classe. La doppia conferma resta scritta nella spiegazione.
            verdetto = "OK"
            spiega = ("pezzo giusto nel posto giusto, doppia conferma (golden caldo + "
                      + ("golden confermato: nessuna coppia traslata lo batte)" if res_sfida
                         else "radar concorde)"))
        elif esito1 == "LI" and conc in ("MUTO", "FANTASMA", "OCCUPATO"):
            verdetto = "OK"
            spiega = (("segnale sui fori attesi sopra soglia; il radar cieco aggancia solo "
                       "la posizione del KO precedente (fantasma della rimozione): nessuna "
                       "controprova contraria") if conc == "FANTASMA" else
                      (f"segnale sui fori attesi sopra soglia; il radar cieco propone "
                       f"{radar_dice} ma appoggia un piedino su un foro gia' OCCUPATO "
                       f"({pick_occupato}) da uno step chiuso, dove un secondo pezzo non "
                       f"ci sta: sta rileggendo il pezzo vecchio, non contraddice")
                      if pick_occupato else
                      (f"segnale sui fori attesi sopra soglia; il radar cieco propone "
                       f"{radar_dice} ma appoggia un piedino su un foro MUTO "
                       f"({pick_muto}), dove non e' cambiato niente: non e' una "
                       f"posizione possibile, quindi non contraddice") if pick_muto else
                      "segnale sui fori attesi sopra soglia; radar cieco muto (nessuna controprova)")
        elif esito1 == "LI" and res_sfida:
            # DISCORDE/PARZIALE dallo sfidante: qui la controprova è una misura, una
            # copia traslata coerente del footprint golden è più calda del golden. Il
            # segnale ancorato non distingue un componente slittato di 1-3 fori (leakage
            # sui fori attesi) -> onestà: niente OK pieno, si chiede di guardare.
            verdetto = "OK?"
            spiega = ("segnale sui fori attesi sopra soglia MA una coppia golden TRASLATA "
                      "e' piu' calda (>= %.1fx): possibile slittamento coerente - "
                      "CONTROLLA A OCCHIO" % M_SFIDA)
        elif esito1 == "LI":
            # DISCORDE/PARZIALE dal solo pick cieco -> OK. Qui la controprova non è
            # una misura, è il pick di fit_pins, che per costruzione cerca gli estremi
            # del segmento supportato mentre il calore sta al centro sul corpo, ed è
            # cieco sul contesto (vedi replay_vlm._pin_letti). Su un montaggio che
            # l'ancorato conferma (esito1 LI, tutti i fori golden sopra soglia)
            # sbagliare una gamba di una colonna non è evidenza di slittamento: è il
            # difetto noto del pick, la stessa constatazione che per la resistenza ha
            # portato allo sfidante golden.
            # Misurato sull'archivio: 10 OK? in tutto, questo ramo ne prende 4, tutti
            # lo stesso transistor a tre gambe; dove c'è il ground truth conferma il
            # montaggio corretto in tutti i casi verificabili. Non tocca le altre due
            # famiglie di OK?: quelli del ramo filo (capo vicino ma non sul foro) e il
            # golden sotto soglia, che resta un dubbio vero. Il disaccordo del pick
            # resta scritto nella spiegazione.
            verdetto = "OK"
            spiega = (f"segnale sui fori attesi sopra soglia su TUTTI i fori golden; il "
                      f"radar cieco dice {radar_dice} ({conc}) ma il pick sbaglia il foro "
                      f"di +-1 per costruzione e non e' una misura: non contraddice")
        elif esito1 == "LI1":
            verdetto = "KO POSIZIONE"
            spiega = ("probabile +-1: golden muto ma vicino caldo -> "
                      + ", ".join(f"{n} ({s:.2f})" for n, s in sospetti))
        elif esito1 == "DEBOLE":
            verdetto = "DEBOLE"
            spiega = (f"segnale tra {weak} e {thr}: non dimostrabile ne' escludibile "
                      f"(stile STIMATO). Radar: {radar_dice} ({conc})")
        elif pins_grid:
            verdetto = "KO POSIZIONE"
            spiega = f"non e' nei fori golden; il radar cieco lo vede in {radar_dice}"
        else:
            verdetto = "KO POSIZIONE"
            spiega = "non lo vedo dove dovrebbe e il radar non lo aggancia altrove (debole)"

        # --- Spostamento di un foro confermato da due fonti ---
        # `LI1` è già la firma del +-1: il DOVE ancorato ha trovato un vicino sospetto
        # accanto al golden. Da sola non basta a bocciare, e infatti alcuni rami (filo
        # ALLINEATO, solo-golden con gold_ok) dichiarano OK lo stesso. Ma quando anche
        # il pick cieco nomina proprio quel vicino, le due fonti sono indipendenti (una
        # misura sui fori, una geometria sulla sagoma) e concordano sul foro sbagliato.
        # Caso misurato: la resistenza sta nel foro accanto al golden e ci arriva col
        # corpo sopra il golden, che quindi misura 0.587 e fa dire "foro occupato"; ma
        # `vicini_sospetti` dà il vicino a 0.338 e il pick nomina lo stesso vicino.
        # Sull'archivio: 29 righe LI1, di cui 2 a verdetto OK, e in tutte e due il pick
        # conferma il vicino. Le altre 27 sono già KO e non si toccano.
        if (not verdetto.startswith("KO") and esito1 == "LI1" and sospetti):
            pick_grid = {df.name(*uv) for uv in res["pins"] if isinstance(uv[0], int)}
            conferma = [n for n, _ in sospetti if n in pick_grid]
            if conferma:
                verdetto = "KO POSIZIONE"
                spiega = (f"SPOSTATO DI UN FORO: il vicino {', '.join(conferma)} e' "
                          f"sospetto per il DOVE ancorato ("
                          + ", ".join(f"{n}={s:.2f}" for n, s in sospetti)
                          + f") ED e' quello che nomina il radar cieco. Due fonti "
                          f"indipendenti sullo stesso foro sbagliato. " + spiega)

        # --- Capo di rail col segno sbagliato (un cavetto atteso sul più e montato
        # sul meno uscirebbe OK? scentrato). Il DOVE ancorato le rail non le verifica,
        # ma il segno atteso è scritto nel golden e il pick il capo-rail lo vede: se
        # nessun capo-rail visto sta sul segno atteso, non è un dubbio. ---
        if cls_att in fl.FAM_FILO and n_rail > 0 and not verdetto.startswith("KO"):
            h_txt = st.get("holes", "") or ""
            rail_att = "+" if "+" in h_txt else (
                "-" if ("-" in h_txt or "−" in h_txt) else None)
            rail_viste = {uv[0][0] for uv in res["pins"] if not isinstance(uv[0], int)}
            if rail_att and rail_viste and rail_att not in rail_viste:
                # Attraversamento della rail interna: per arrivare alla rail esterna
                # il rigido scavalca quella interna, e l'attraversamento accende il
                # nodo più del piedino dentro (misurato: 0.55 attraversato contro 0.13
                # piedino, stessa fisica dello scavalcamento golden). Il pick prende il
                # più caldo, quindi sui capi alla rail esterna il segno esce invertito
                # sempre. Controprova che non si può falsificare: il nodo esterno non
                # ha filo oltre sé; se il nodo del segno atteso accanto al capo visto è
                # vivo, il capo sta lì.
                vivo_att = 0.0
                for rp in res["pins"]:
                    if isinstance(rp[0], int):
                        continue
                    blocco = rp[0][1:] if len(rp[0]) > 1 else ""
                    for uv2 in res.get("rpos", {}):
                        if (uv2[0] == rail_att + blocco
                                and abs(uv2[1] - rp[1]) <= 1):
                            vivo_att = max(vivo_att,
                                           df.legscore(g0, g1, posx, *uv2))
                if vivo_att >= thr:
                    spiega += (f" [pick su rail "
                               f"'{','.join(sorted(rail_viste))}' ma il nodo "
                               f"{rail_att} accanto e' vivo ({vivo_att:.2f}): il "
                               f"capo sta li', l'opposto caldo e' il filo che lo "
                               f"attraversa]")
                else:
                    verdetto = "KO POSIZIONE"
                    spiega = (f"capo sulla RAIL SBAGLIATA: il golden vuole "
                              f"'{rail_att}', il radar vede il capo su "
                              f"'{','.join(sorted(rail_viste))}' e il nodo "
                              f"{rail_att} e' muto ({vivo_att:.2f}). " + spiega)

        if nota_base:
            spiega += " [misura contro la board pre-KO]"
        if nota_cosa:
            spiega += nota_cosa

        # --- report terminale (solo ASCII) ---
        L = []
        L.append("=" * 60)
        L.append(f"STEP {step_num}/{len(self.build)} - commessa {self.cid} - foto {res['k']}")
        L.append(_ascii(f"ATTESO: {st['el']} in {st.get('holes', '?')}"))
        L.append("=" * 60)
        L.append("COSA")
        vis = cls_vista or "(non letta)"
        L.append(f"  visto: {vis} (fonte: {fonte})"
                 + (f"   colore: {res['color']}" if res["color"] else "")
                 + (f" (atteso {col_atteso})" if col_atteso else "")
                 + f"        -> CLASSE {cosa}")
        L.append("")
        L.append("DOVE - verifica ancorata al golden")
        L.extend(righe_dove)
        if corpo is None and punteggio is not None:
            stato = {"LI": "SOPRA SOGLIA", "LI1": "SOSPETTO +-1",
                     "DEBOLE": "DEBOLE", "NON_LI": "SOTTO SOGLIA"}[esito1]
            L.append(f"  punteggio di classe: {punteggio:.3f}  vs soglia {thr}   {stato}")
        if sospetti:
            L.append("  vicini +-1 CALDI: " + ", ".join(f"{n} ({s:.2f})" for n, s in sospetti))
        elif corpo is None:
            L.append("  vicini +-1: nessuno batte i golden")
        if n_rail > 0:
            L.append(f"  capi su rail: {n_rail} NON verificati live (li certifica run_qc)")
        L.append("")
        L.append("DOVE - radar cieco (controprova indipendente)")
        L.append(f"  senza golden il radar dice: {radar_dice}        {conc}")
        if fantasma:
            L.append("  ignorati (posizione del KO precedente, fantasma della rimozione): "
                     + ", ".join(df.name(*uv) for uv in fantasma))
        L.append("")
        L.append(f"VERDETTO: {verdetto}")
        L.append(f"  {spiega}")
        L.append("=" * 60)

        rec = {"step": step_num, "k": res["k"],
               "atteso": {"el": st["el"], "cls": cls_att, "coords": st.get("coords", []),
                          "holes": st.get("holes", "")},
               "cosa": {"cls_vista": cls_vista, "col_vista": col_vista,
                        "col_atteso": col_atteso, "fonte": fonte, "esito": cosa},
               "ancorata": {"misure": {df.name(*uv): round((S_verd or S_mis)[uv], 3)
                                       for uv in exp},
                            "punteggio_classe": (round(punteggio, 3) if punteggio is not None else None),
                            "soglia": thr, "esito": esito1,
                            "vicini_sospetti": [[n, round(s, 3)] for n, s in sospetti]},
               "colore": colore_verdetto(verdetto),
               "radar": {"pins": [df.name(*uv) for uv in res["pins"]], "concordanza": conc,
                         "fantasma": [df.name(*uv) for uv in fantasma]},
               "filo": {"livello": liv_filo, "dettaglio": det_filo},
               "cupola": {"livello": liv_cup or liv_led, "dettaglio": det_cup or det_led},
               "corpo": {"livello": liv_corpo, "dettaglio": det_corpo,
                         "riferimento": (list(rif) if rif else None)},
               "verdetto": verdetto, "spiegazione": spiega, "report": "\n".join(L)}
        return rec

    # ---------- persistenza / riepilogo ----------
    def dump(self):
        return {"commessa": self.cid, "golden": self.gpath,
                "steps": [{k: v for k, v in r.items() if k != "report"}
                          for r in self.records],
                "storico": list(self.storico)}

    def riepilogo_txt(self):
        """Tabella finale per il terminale + hint run_qc (referto certificato)."""
        L = ["", "#" * 60, f"RIEPILOGO commessa {self.cid} - {self.golden['name']}", "#" * 60]
        for r in self.records:
            if r["step"] is None:
                L.append(f"  foto {r['k']}: FUORI GOLDEN")
            else:
                L.append(_ascii(f"  step {r['step']}: {r['atteso']['el']:<28} -> {r['verdetto']}"))
        aperti = len(self.build) - self.step_i
        if aperti > 0:
            L.append(f"  ({aperti} step golden NON visti)")
        L.append("")
        L.append("referto certificato (motore qc_main completo, a posteriori):")
        L.append(f'  python src/probe/run_qc.py <cartella-run> {self.cid} [--chiave <key>]')
        L.append("#" * 60)
        return "\n".join(L)


def raccogli_gt(oper, registro_path):
    """CLI a fine run: ground truth dell'operatore, step per step.
    Per ogni step chiede i fori di GRIGLIA dove i pin sono REALMENTE finiti
    (invio = come atteso, '-' = componente non montato). Salva <run>_gt.json
    accanto al registro. Il GT alimenta score_oper.py (registro errori)."""
    if not oper.records or not registro_path:
        return None
    try:
        r = input("\nGT a fine run: registrare i fori REALI? [s/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if r != "s":
        print("GT saltato (score_oper.py puo' chiederlo dopo).")
        return None
    per_step = {}
    for rec in oper.records:
        if rec.get("step") is None or not rec["atteso"]["coords"]:
            continue                                  # None=fuori golden, []=solo-rail
        per_step[rec["step"]] = rec                    # l'ultima occorrenza vince (correzione sovrascrive lo scarto)
    steps = []
    for n in sorted(per_step):
        rec = per_step[n]
        att = rec["atteso"]["coords"]
        p = _ascii(f"  step {rec['step']} {rec['atteso']['el']} - atteso {','.join(att)} "
                   f"- reale? [invio=atteso, -=non montato] ")
        try:
            r = input(p).strip().lower().replace(" ", "")
        except (EOFError, KeyboardInterrupt):
            return None
        reale = att if r == "" else ([] if r == "-" else r.split(","))
        steps.append({"step": rec["step"], "k": rec["k"], "atteso": att, "reale": reale,
                      "errore_montaggio": sorted(reale) != sorted(att)})
    gt = {"commessa": oper.cid, "steps": steps}
    gpath = registro_path.replace("_registro.json", "_gt.json")
    json.dump(gt, open(gpath, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    nerr = sum(s["errore_montaggio"] for s in steps)
    print(f"GT salvato: {gpath}  ({nerr} errori montaggio dichiarati)")
    print(f'score: python src/radar/score_oper.py "{os.path.basename(registro_path).replace("_registro.json", "")}"')
    return gpath
