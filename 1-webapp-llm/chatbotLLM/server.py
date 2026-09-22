# -*- coding: utf-8 -*-
"""
Backend della webapp Electronics.

Fa due cose:
  1. Serve la webapp (cartella ../WEBAPP) su http://localhost:8000
  2. Espone l'assistente LLM (RAG sul manuale operativo) via API REST

Avvio:  python server.py      →  poi apri http://localhost:8000
"""
import os
import sys
import json
import time
import shutil
import logging
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent          # .../chatbotLLM
WEBAPP_DIR = BASE.parent / "WEBAPP"             # .../WEBAPP
PAGINA = "/app/mockups-flusso/variante-2-laboratorio.html"

# I moduli del motore usano percorsi relativi (data/keys.txt, db_vector): ci posizioniamo qui.
os.chdir(BASE)
sys.path.insert(0, str(BASE))

from core.api_manager import GeminiKeyRotator      # noqa: E402
from core.rag_engine import RAGRetriever           # noqa: E402
from core.manuale import Manuale                   # noqa: E402
from core.context_builder import ContextBuilder    # noqa: E402
from core.chatbot import ElectronicAssistant       # noqa: E402
from core.sessions import SessionStore             # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

app = FastAPI(title="Electronics · Assistente operatore")


@app.middleware("http")
async def _niente_cache_html(request, call_next):
    """L'HTML della webapp non va mai messo in cache dal browser: dopo un
    aggiornamento l'operatore deve vedere subito la versione nuova, senza
    refresh forzato (in campo un JS vecchio in cache ha causato malfunzionamenti)."""
    risposta = await call_next(request)
    percorso = request.url.path
    if percorso.endswith(".html") or percorso.endswith(".js") or percorso.rstrip("/") in ("", "/app"):
        risposta.headers["Cache-Control"] = "no-cache"
    return risposta

_assistant = None
_sessioni = SessionStore(max_sessioni=50, max_scambi=12, ttl_secondi=6 * 3600)


def get_assistant() -> ElectronicAssistant:
    """Crea l'assistente una sola volta (il caricamento di ChromaDB richiede qualche secondo)."""
    global _assistant
    if _assistant is None:
        logger.info("Inizializzazione motore LLM…")
        # Timeout HTTP esplicito: senza, una chiamata può restare appesa per
        # sempre e l'operatore vede "in corso" all'infinito.
        from google import genai
        fabbrica = lambda chiave: genai.Client(  # noqa: E731
            api_key=chiave, http_options={"timeout": 60_000})
        rotator = GeminiKeyRotator(keys_file_path=str(BASE / "data" / "keys.txt"),
                                   client_factory=fabbrica)
        manuale = Manuale(str(BASE / "data" / "manuale_operativo.md"))
        contesto = ContextBuilder(manuale, RAGRetriever())
        _assistant = ElectronicAssistant(rotator, contesto)
        logger.info("Motore LLM pronto (modello: %s).", _assistant.model_name)
    return _assistant


@app.on_event("startup")
def warmup():
    """Scalda il motore all'avvio, così la prima domanda dell'operatore non attende."""
    try:
        get_assistant()
    except Exception as e:                                  # non blocca l'avvio del server
        logger.error("Motore LLM non inizializzato: %s", e)


# ----------------------------- API -----------------------------
class Contesto(BaseModel):
    commessa: str | None = None
    fase: str | None = None
    passo: int | None = None
    passoTot: int | None = None


class Domanda(BaseModel):
    testo: str
    sessione: str | None = None
    contesto: Contesto | None = None


class Sessione(BaseModel):
    sessione: str | None = None


@app.get("/api/stato")
def stato():
    """Diagnostica: la webapp la usa per capire se l'assistente è disponibile."""
    try:
        a = get_assistant()
        return {"pronto": True, "modello": a.model_name}
    except Exception as e:
        return {"pronto": False, "errore": str(e)}


# ---------- ponte VLM ----------
# I due sistemi restano DISACCOPPIATI: il radar scrive lo stato nella cartella-ponte
# TEMPORANEA (2-vlm/data/ponte, svuotata all'avvio e alla chiusura del radar), la
# webapp la legge e basta. Radar spento = cartella vuota = pallini neutri.
# L'archivio storico resta in data/registri e qui non si tocca.
VLM_PONTE = BASE.parent.parent / "2-vlm" / "data" / "ponte"


def _vlm_colore(v):
    """Fallback per registri vecchi senza campo `colore` (stessa regola di
    radar_operativa.colore_verdetto)."""
    v = str(v or "")
    if v in ("OK", "OK FORTE"):
        return "verde"
    if v.startswith("KO"):
        return "rosso"
    if v in ("OK?", "DEBOLE"):
        return "giallo"
    return "grigio"


# Sentinella di stop: quando l'operatore passa al collaudo il montaggio è
# finito e altri scatti sarebbero solo rumore. La webapp posa il file, il radar lo
# vede nel suo loop e CHIUDE la run (wizard GT + RUN_<id>.xlsx, come col tasto Q).
# Stesso canale del semaforo: cartella temporanea, i due sistemi restano disaccoppiati.
STOP_FLAG = "_stop.flag"


@app.post("/api/vlm/reset")
def vlm_reset():
    """Svuota la cartella-ponte (il tasto «Reset commesse» azzera anche il
    semaforo). Unica scrittura concessa alla webapp, e solo sul ponte
    temporaneo — l'archivio in data/registri resta intoccabile da qui."""
    rimossi = 0
    try:
        for f in list(VLM_PONTE.glob("*.json")) + [VLM_PONTE / STOP_FLAG]:
            if f.exists():
                f.unlink()
                rimossi += 1
    except OSError:
        pass
    return {"rimossi": rimossi}



@app.post("/api/vlm/stop")
def vlm_stop(c: Contesto | None = None):
    """Chiede al radar di smettere di acquisire immagini. Se il radar è spento il
    file resta lì innocuo: la cartella-ponte viene svuotata all'avvio della run dopo."""
    try:
        VLM_PONTE.mkdir(parents=True, exist_ok=True)
        (VLM_PONTE / STOP_FLAG).write_text(
            json.dumps({"commessa": (c.commessa if c else None), "ora": time.time()}),
            encoding="utf-8")
    except OSError as e:
        logger.error("Sentinella di stop non scritta: %s", e)
        return {"ok": False}
    return {"ok": True}


@app.get("/api/vlm/stato")
def vlm_stato(commessa: str):
    """Semaforo della commessa dal registro VLM più recente (per mtime).
    Ritorna {commessa, run, steps: {n: {colore, verdetto, motivo, k}}}.
    `steps` usa i record definitivi (un record per step, l'ultimo vince: la
    correzione aggiorna il pallino da sola al poll successivo). Se il file è a
    metà scrittura si risponde vuoto e il poll dopo recupera."""
    if not commessa.isalnum():
        raise HTTPException(status_code=400, detail="commessa non valida")
    p = VLM_PONTE / f"{commessa}.json"
    if not p.exists():
        return {"commessa": commessa, "run": None, "steps": {}}
    run = commessa
    try:
        aggiornato = p.stat().st_mtime             # il frontend colora solo se FRESCO
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"commessa": commessa, "run": run, "steps": {}}
    steps = {}
    for rec in d.get("steps") or []:
        n = rec.get("step")
        if n is None:
            continue
        steps[str(n)] = {"colore": rec.get("colore") or _vlm_colore(rec.get("verdetto")),
                         "verdetto": rec.get("verdetto", ""),
                         "motivo": (rec.get("spiegazione") or "")[:160],
                         "k": rec.get("k")}
    return {"commessa": commessa, "run": run, "aggiornato": aggiornato, "steps": steps}


def _rispondi(testo: str | None, audio: str | None, sessione: str | None, contesto: dict | None) -> str:
    assistente = get_assistant()
    risposta = assistente.ask(
        query_text=testo,
        audio_file_path=audio,
        contesto=contesto,
        history=_sessioni.history(sessione),
    )
    _sessioni.append(sessione, testo or "[Richiesta Audio]", risposta)
    return risposta


@app.post("/api/chat")
def chat(d: Domanda):
    """Domanda testuale. `contesto` descrive cosa sta facendo l'operatore adesso."""
    testo = (d.testo or "").strip()
    if not testo:
        raise HTTPException(status_code=400, detail="Domanda vuota")
    try:
        contesto = d.contesto.model_dump(exclude_none=True) if d.contesto else None
        return {"risposta": _rispondi(testo, None, d.sessione, contesto)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Errore /api/chat: %s", e)
        raise HTTPException(status_code=500, detail="Assistente non disponibile")


@app.post("/api/chat-audio")
def chat_audio(  # sync di proposito: gira nel threadpool come gli altri, la
                 # chiamata Gemini bloccante non deve congelare l'event loop
    audio: UploadFile = File(...),
    sessione: str | None = Form(default=None),
    contesto: str | None = Form(default=None),
):
    """Domanda vocale: il browser invia un WAV, lo passiamo a Gemini (multimodale)."""
    updir = BASE / "data" / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    dest = updir / "domanda.wav"
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(audio.file, f)
        if dest.stat().st_size < 1000:
            raise HTTPException(status_code=400, detail="Audio troppo breve")
        # La voce è dettatura: qui si trascrive e basta. La webapp
        # mette la trascrizione nel campo di testo, l'operatore la rilegge,
        # la corregge se serve e decide se inviarla — il chatbot riceve solo
        # domande testuali dal normale /api/chat.
        trascrizione = get_assistant().trascrivi(str(dest))
        if not trascrizione:
            raise HTTPException(status_code=502, detail="Trascrizione non riuscita: riprova.")
        return {"trascrizione": trascrizione}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Errore /api/chat-audio: %s", e)
        raise HTTPException(status_code=500, detail="Assistente non disponibile")


@app.post("/api/reset")
def reset(s: Sessione | None = None):
    """Azzera la memoria della conversazione della sola sessione indicata."""
    _sessioni.reset(s.sessione if s else None)
    return {"ok": True}


# ---------- tempi delle fasi ----------
# Cronometro PURO lato webapp: T1 picking, T2 assemblaggio, T3 collaudo. A commessa
# chiusa (OK o KO) la pagina manda qui i tre tempi e il server li STAMPA e basta —
# niente file, niente Excel: l'operatore legge il terminale e li salva dove vuole.
class Tempi(BaseModel):
    commessa: str | None = None
    t1: float = 0.0        # picking, secondi
    t2: float = 0.0        # assemblaggio, secondi
    t3: float = 0.0        # collaudo & CQ, secondi


def _mmss(sec: float) -> str:
    sec = int(round(max(0.0, sec)))
    return f"{sec // 60:02d}:{sec % 60:02d}"


@app.post("/api/tempi")
def tempi(t: Tempi):
    """Salva i tempi della commessa appena chiusa in data/tempi_commesse.xlsx (una riga
    per commessa, rilanciabile: la stessa commessa aggiorna la sua riga) e li stampa.
    La seconda riga stampata è TSV, per chi vuole copiarla al volo."""
    cid = t.commessa or "?"
    tot = t.t1 + t.t2 + t.t3
    try:                                   # l'Excel non deve mai far fallire la chiusura
        import tempi_xlsx
        tempi_xlsx.scrivi(cid, t.t1, t.t2, t.t3)
    except Exception as e:
        logger.error("Tempi non scritti su Excel: %s", e)
    print(f"\n[tempi] commessa {cid}  |  T1 picking {_mmss(t.t1)}  "
          f"T2 assemblaggio {_mmss(t.t2)}  T3 collaudo {_mmss(t.t3)}  "
          f"|  totale {_mmss(tot)}", flush=True)
    print(f"[tempi] {cid}\t{t.t1:.1f}\t{t.t2:.1f}\t{t.t3:.1f}\t{tot:.1f}\n", flush=True)
    return {"ok": True}


# --------------------------- Webapp ----------------------------
@app.get("/")
def home():
    return RedirectResponse(PAGINA)


app.mount("/app", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")


if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 62)
    print("  Webapp Electronics + Assistente")
    print("  Apri il browser su:  http://localhost:8000")
    print("=" * 62 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
