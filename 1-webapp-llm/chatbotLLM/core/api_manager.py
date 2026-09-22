import os
import time
import logging
from google import genai
from google.genai import types
from google.genai.errors import APIError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Modello predefinito riallineato alla configurazione documentata del collaudo pre.
# Lo stesso modello non basta a trasferire le metriche: devono essere verificati
# anche knowledge base, prompt, parametri e protocollo della valutazione.
MODELLO_PREDEFINITO = "gemini-3.5-flash"

class GeminiKeyRotator:
    """
    Gestisce un pool di chiavi API per Gemini, ruotandole automaticamente
    in caso di esaurimento quota o superamento del limite di richieste (HTTP 429).
    """
    def __init__(self, keys_file_path: str = "data/keys.txt", client_factory=None):
        self.keys = self._load_keys(keys_file_path)
        self.current_key_index = 0
        # Chiavi che hanno dato un errore definitivo (non valide, o senza accesso al
        # modello): vengono escluse dalla rotazione per il resto della sessione.
        self.chiavi_morte: set[int] = set()
        self._client_factory = client_factory or (lambda chiave: genai.Client(api_key=chiave))
        if not self.keys:
            raise ValueError(f"Nessuna chiave API trovata. Assicurati che {keys_file_path} o chiavi.txt esista.")

        logger.info(f"Caricate {len(self.keys)} chiavi API.")
        self.client = self._create_client()

    def _load_keys(self, file_path: str) -> list[str]:
        keys = []
        # Tenta il path specifico o il fallback sulla root directory
        paths_to_try = [file_path, "chiavi.txt"]
        for p in paths_to_try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        key = line.strip()
                        if key and not key.startswith("#"):
                            keys.append(key)
                if keys:
                    break # Se abbiamo trovato le chiavi, fermiamoci
        return keys

    def _create_client(self):
        return self._client_factory(self.keys[self.current_key_index])

    def rotate_key(self) -> bool:
        """
        Passa alla prossima chiave utilizzabile, saltando quelle già escluse.
        Ritorna False se non ne resta nessuna.
        """
        for passo in range(1, len(self.keys) + 1):
            candidata = (self.current_key_index + passo) % len(self.keys)
            if candidata not in self.chiavi_morte:
                self.current_key_index = candidata
                self.client = self._create_client()
                logger.warning(f"Rotazione chiave API: ora uso la chiave di indice {candidata}.")
                return True
        logger.error("NESSUNA CHIAVE API UTILIZZABILE.")
        return False

    def _escludi_chiave_corrente(self, motivo: str):
        logger.error(f"Chiave di indice {self.current_key_index} esclusa ({motivo}).")
        self.chiavi_morte.add(self.current_key_index)

    def generate_content_with_retry(self, model: str, contents,
                                    config: types.GenerateContentConfig = None,
                                    max_retries: int = None):
        """
        Esegue la chiamata a Gemini gestendo tre situazioni diverse:
          - chiave inutilizzabile (400 chiave non valida, 403, 404 modello non accessibile):
            la chiave viene esclusa definitivamente e si passa alla successiva;
          - limite di richieste (429): si ruota chiave senza escluderla;
          - servizio sovraccarico (503): si attende e si riprova sulla stessa chiave.
        """
        if max_retries is None:
            # Deve bastare a scartare tutte le chiavi rotte e trovarne una buona.
            max_retries = len(self.keys) + 2

        tentativi = 0
        while tentativi < max_retries:
            try:
                return self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )
            except APIError as e:
                codice = getattr(e, "code", None)
                messaggio = str(getattr(e, "message", "") or e)

                chiave_inutilizzabile = (
                    codice in (403, 404)
                    or (codice == 400 and "api key not valid" in messaggio.lower())
                )

                if chiave_inutilizzabile:
                    self._escludi_chiave_corrente(f"HTTP {codice}")
                    if not self.rotate_key():
                        raise Exception("Nessuna chiave API utilizzabile per questo modello.")
                elif codice == 429:
                    logger.warning("Limite richieste raggiunto (429). Rotazione chiave…")
                    if not self.rotate_key():
                        raise Exception("Quota esaurita per tutte le chiavi disponibili.")
                    time.sleep(1)
                elif codice in (503, 504):
                    # Il 503 «high traffic» non colpisce tutte le chiavi insieme (misurato:
                    # 8 chiavi su 14 rispondono mentre le altre danno 503). Aspettare sulla
                    # stessa chiave spreca i tentativi: si ruota, senza escluderla, al giro
                    # dopo quella chiave può essere di nuovo buona. Stessa strategia per il
                    # 504 DEADLINE_EXCEEDED, che compare quando il client ha un timeout
                    # esplicito e il server è saturo.
                    logger.warning("Server Google sovraccarico (%s). Rotazione chiave…", codice)
                    time.sleep(2)
                    if not self.rotate_key():
                        raise Exception(f"Servizio sovraccarico su tutte le chiavi ({codice}).")
                else:
                    raise
                tentativi += 1

        raise Exception("Superato il numero massimo di tentativi.")
