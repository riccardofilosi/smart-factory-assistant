# -*- coding: utf-8 -*-
"""Memoria della conversazione, una history per sessione."""
import threading
import time
from collections import OrderedDict

SESSIONE_CONDIVISA = "__anonima__"


class SessionStore:
    def __init__(self, max_sessioni: int = 50, max_scambi: int = 6,
                 ttl_secondi: int = 6 * 3600, clock=time.time):
        self.max_sessioni = max_sessioni
        self.max_scambi = max_scambi
        self.ttl_secondi = ttl_secondi
        self.clock = clock
        # sid → {"scambi": [(domanda, risposta)], "aggiornata": timestamp}
        self._sessioni: OrderedDict[str, dict] = OrderedDict()
        # Gli endpoint FastAPI sincroni girano nel threadpool: più richieste possono
        # mutare la mappa in parallelo. Un lock rende atomiche le operazioni.
        self._lock = threading.Lock()

    @staticmethod
    def _chiave(sid) -> str:
        return sid if isinstance(sid, str) and sid.strip() else SESSIONE_CONDIVISA

    def _pulisci(self):
        adesso = self.clock()
        scadute = [k for k, v in self._sessioni.items() if adesso - v["aggiornata"] > self.ttl_secondi]
        for k in scadute:
            del self._sessioni[k]

    def history(self, sid) -> list:
        with self._lock:
            self._pulisci()
            sessione = self._sessioni.get(self._chiave(sid))
            return list(sessione["scambi"]) if sessione else []

    def append(self, sid, domanda: str, risposta: str) -> None:
        with self._lock:
            self._pulisci()
            chiave = self._chiave(sid)
            sessione = self._sessioni.pop(chiave, None) or {"scambi": []}
            sessione["scambi"].append((domanda, risposta))
            del sessione["scambi"][:-self.max_scambi]
            sessione["aggiornata"] = self.clock()
            self._sessioni[chiave] = sessione            # in coda: la più recente
            while len(self._sessioni) > self.max_sessioni:
                self._sessioni.popitem(last=False)       # sfratta la meno recente

    def reset(self, sid) -> None:
        with self._lock:
            self._sessioni.pop(self._chiave(sid), None)
