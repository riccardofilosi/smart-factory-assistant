#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
read_holes.py — accesso al VLM (Gemini via endpoint OpenAI-compatibile).

`chat(messages)` è l'unico punto di chiamata del modello usato dal resto del
sistema: gestisce la rotazione delle chiavi API e l'interruzione da tastiera.

Chiavi: file `chiave.txt` (chiave preferenziale) e `chiavi.txt` (pool di riserva)
nella radice del progetto, oppure i percorsi nelle variabili d'ambiente
QC_CHIAVE_FILE / QC_CHIAVI_FILE. La variabile QC_CHIAVE, se impostata, contiene una
chiave provata prima di tutte le altre. Nessun file di chiavi va versionato.

Come strumento a riga di comando legge le coordinate dei fori di un componente su
una foto con griglia disegnata, N volte, per misurare la consistenza del modello:
  python read_holes.py IMG --n 5 --model gemini-3.7-flash --think none
"""
import argparse, base64, json, os, re, sys, time
from pathlib import Path
from collections import Counter

_ROOT = Path(__file__).resolve().parents[2]
CHIAVE_AQ   = Path(os.environ.get("QC_CHIAVE_FILE", _ROOT / "chiave.txt"))   # chiave preferenziale
CHIAVI_POOL = Path(os.environ.get("QC_CHIAVI_FILE", _ROOT / "chiavi.txt"))   # pool di riserva
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL = "gemini-3.7-flash"   # modello di progetto. Le run valutate con un modello
                             # diverso non sono confrontabili: rigiocarle con
                             # replay_vlm.py prima di usarne i numeri.
REASONING = "none"
VERBOSE = False            # se True, chat() stampa i passi della rotazione chiavi
ABORT = None               # callable()->bool: se True fra un tentativo e l'altro, chat()
                           # interrompe la rotazione (il radar lo lega alla tastiera)


class VlmInterrupted(BaseException):
    """Alzata da chat() quando la rotazione chiavi viene interrotta dal terminale.
    Eredita da BaseException: nessun 'except Exception' a valle la cattura, arriva
    pulita al loop del radar, che chiede la classe all'operatore."""

SYS = """Guardi la foto di una breadboard su cui e' stata DISEGNATA una griglia di coordinate:
- pallini VERDI = posizione esatta di ogni foro,
- lettere ROSSE ai lati = righe (a..j, con fessura centrale tra e ed f),
- numeri BLU sopra e sotto = colonne (1..63).

C'e' UN componente sulla board. Il tuo compito: dire su quali fori stanno le sue gambe.
REGOLA: NON contare i fori. LEGGI l'etichetta disegnata: trova il pallino verde piu' vicino
a ogni gamba, poi leggi la sua riga (lettera rossa sulla stessa fila) e la sua colonna
(numero blu sulla stessa colonna). Se una gamba e' ambigua, abbassa la confidenza, NON inventare.

Rispondi SOLO con JSON:
{"componente":"tipo che vedi",
 "gambe":[{"descrizione":"es. gamba sinistra","foro":"<riga><colonna>, es. e12"}],
 "confidenza":0.0-1.0,
 "note":"dubbi/occlusioni"}"""

SYS_MULTI = """Guardi la foto di una breadboard su cui e' stata DISEGNATA una griglia di coordinate:
- pallini VERDI = posizione esatta di ogni foro,
- lettere ROSSE ai lati = righe (a..j, con fessura centrale tra e ed f),
- numeri BLU sopra e sotto = colonne (1..63).

Ci sono PIU' componenti sulla board. Elenca TUTTI quelli che vedi.
Per ognuno: tipo (resistenza/LED/condensatore/transistor/bottone/jumper/IC...), valore se leggibile
(es. bande resistenza -> ohm; LED -> colore), e i fori di OGNI gamba.
REGOLA: NON contare i fori. LEGGI l'etichetta disegnata piu' vicina a ogni gamba (riga rossa + colonna blu).
Se ambiguo, abbassa la confidenza, NON inventare.

Rispondi SOLO con JSON:
{"componenti":[
   {"tipo":"...","valore":"... o null","gambe":["<riga><colonna>", "..."],"confidenza":0.0-1.0}
 ],
 "note":"occlusioni/dubbi"}"""

SYS_NOGRID = """Guardi la foto di una breadboard SENZA griglia disegnata: ci sono solo le serigrafie
stampate sulla board (lettere di riga a..j con fessura centrale tra e ed f; numeri di colonna 1..63,
stampati ogni 5). C'e' UN componente sulla board.
Il tuo compito: dire su quali fori stanno le sue gambe, come <riga><colonna> (es. e12).
Determina riga e colonna contando/leggendo le serigrafie stampate. Se ambiguo, abbassa la confidenza.

Rispondi SOLO con JSON:
{"componente":"tipo che vedi",
 "gambe":[{"descrizione":"es. gamba sinistra","foro":"<riga><colonna>"}],
 "confidenza":0.0-1.0,
 "note":"dubbi/occlusioni"}"""

def load_keys():
    # Ordine: prima la chiave preferenziale (chiave.txt), poi il pool di riserva
    # (chiavi.txt), per reindirizzare la domanda quando una chiave torna 503/quota.
    # Due formati Google: "AIza..." e "AQ....".
    pat = r"AIza[A-Za-z0-9_\-]{20,}|AQ\.[A-Za-z0-9_\-]{20,}"
    def _read(p):
        try:
            return re.findall(pat, p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            return []
    keys = _read(CHIAVE_AQ) + _read(CHIAVI_POOL)
    # La variabile d'ambiente QC_CHIAVE, se impostata, viene provata prima di tutte.
    pref = os.environ.get("QC_CHIAVE", "").strip()
    if pref:
        keys = [pref] + keys
    seen, out = set(), []                      # dedup mantenendo l'ordine
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out

def chat(messages):
    from openai import OpenAI, RateLimitError, InternalServerError, NotFoundError, APIError
    keys, last = load_keys(), None
    for giro in range(2):
        for i, k in enumerate(keys):
            if ABORT and ABORT():                     # interruzione dal terminale -> classe a mano
                raise VlmInterrupted()
            try:
                if VERBOSE: print(f"[VLM] chiamo chiave #{i} (giro {giro + 1}/2)...", flush=True)
                # timeout=12 + max_retries=0: una chiave congestionata fallisce subito
                # (niente backoff interno dell'SDK) e la rotazione passa alla successiva.
                cli = OpenAI(api_key=k, base_url=ENDPOINT, timeout=12.0, max_retries=0)
                r = cli.chat.completions.create(model=MODEL, messages=messages,
                        temperature=0, max_tokens=1500, reasoning_effort=REASONING)
                if VERBOSE: print(f"[VLM] chiave #{i} OK", flush=True)
                return r.choices[0].message.content.strip(), i
            except NotFoundError as e:
                last = e
                if VERBOSE: print(f"[VLM] chiave #{i} 404 modello, prossima", flush=True)
                continue
            except (RateLimitError, InternalServerError) as e:
                last = e
                if VERBOSE: print(f"[VLM] chiave #{i} 429/500, pausa 2s poi prossima", flush=True)
                time.sleep(2); continue
            except APIError as e:
                last = e
                if VERBOSE: print(f"[VLM] chiave #{i} KO ({type(e).__name__}), prossima", flush=True)
                continue
        if ABORT and ABORT():
            raise VlmInterrupted()
        if VERBOSE: print("[VLM] giro finito, pausa 5s", flush=True)
        time.sleep(5)
    sys.exit(f"Chiavi esaurite. Ultimo errore: {last}")

def data_url(path):
    b = Path(path).read_bytes()
    ext = Path(path).suffix.lower().lstrip(".") or "png"
    ext = "jpeg" if ext in ("jpg", "jpeg") else ext
    return f"data:image/{ext};base64,{base64.b64encode(b).decode()}"

def parse_json(raw):
    txt = re.sub(r"^```json|^```|```$", "", raw, flags=re.M).strip()
    try: return json.loads(txt)
    except Exception: return None

def read_once(photo, mode="grid"):
    sysmsg = {"grid": SYS, "nogrid": SYS_NOGRID, "multi": SYS_MULTI}[mode]
    usermsg = {"grid": "Leggi i fori delle gambe del componente usando la griglia disegnata.",
               "nogrid": "Leggi i fori delle gambe del componente dalle serigrafie stampate.",
               "multi": "Elenca TUTTI i componenti con tipo/valore e i fori di ogni gamba, usando la griglia disegnata."}[mode]
    msgs = [{"role": "system", "content": sysmsg},
            {"role": "user", "content": [
                {"type": "text", "text": usermsg},
                {"type": "image_url", "image_url": {"url": data_url(photo)}}]}]
    raw, ki = chat(msgs)
    return parse_json(raw), raw, ki

def holes_of(parsed):
    if not parsed: return None
    try: return tuple(sorted(g.get("foro", "?").lower().replace(" ", "") for g in parsed["gambe"]))
    except Exception: return None

def main():
    global MODEL, REASONING
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--think", choices=["none","low","high"], default=REASONING)
    ap.add_argument("--nogrid", action="store_true", help="controllo: foto senza overlay, legge le serigrafie")
    ap.add_argument("--multi", action="store_true", help="piu' componenti: elenca tutti (tipo/valore/fori)")
    a = ap.parse_args()
    MODEL, REASONING = a.model, a.think
    if not Path(a.photo).exists(): sys.exit(f"Foto non trovata: {a.photo}")
    mode = "multi" if a.multi else ("nogrid" if a.nogrid else "grid")
    print(f"modello={MODEL} think={REASONING} n={a.n} modo={mode} foto={a.photo}\n")

    if mode == "multi":
        for run in range(1, a.n + 1):
            parsed, raw, ki = read_once(a.photo, mode)
            print(f"----- run {run} (key#{ki}) -----")
            if not parsed:
                print("   raw:", raw[:300]); continue
            comps = parsed.get("componenti", [])
            print(f"   {len(comps)} componenti:")
            for cp in comps:
                print(f"     - {cp.get('tipo')} {cp.get('valore') or ''} @ {cp.get('gambe')}  conf={cp.get('confidenza')}")
            if parsed.get("note"): print(f"   note: {parsed['note']}")
        return

    votes = Counter()
    for run in range(1, a.n + 1):
        parsed, raw, ki = read_once(a.photo, mode)
        h = holes_of(parsed)
        votes[h] += 1
        print(f"run {run} (key#{ki}): fori={h}  conf={parsed.get('confidenza') if parsed else '?'}")
        if not parsed: print("   raw:", raw[:200])
    print("\n===== CONSISTENZA =====")
    for h, c in votes.most_common():
        print(f"  {c}/{a.n}  {h}")

if __name__ == "__main__":
    main()
