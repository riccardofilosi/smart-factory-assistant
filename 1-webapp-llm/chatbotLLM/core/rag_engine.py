import logging
import chromadb
from chromadb.utils import embedding_functions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RAGRetriever:
    """
    Gestisce il recupero delle informazioni pertinenti (chunks di testo)
    dal database vettoriale locale in base alla query dell'utente.
    """
    def __init__(self, db_path: str = "db_vector"):
        self.db_path = db_path
        self.chroma_client = chromadb.PersistentClient(path=self.db_path)
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        
        self.collection_name = "manuale_operativo"
        try:
            self.collection = self.chroma_client.get_collection(
                name=self.collection_name, 
                embedding_function=self.embedding_fn
            )
            logger.info("RAGRetriever connesso alla collezione esistente.")
        except Exception as e:
            logger.warning(f"Collezione {self.collection_name} non trovata. Costruire prima la Knowledge Base.")
            self.collection = None

    def retrieve_context(self, query: str, n_results: int = 15) -> str:
        """
        Interroga il database vettoriale e restituisce i frammenti più rilevanti
        uniti in una singola stringa di contesto.
        """
        if not self.collection:
            return ""

        logger.info(f"Ricerca RAG per la query: '{query}'")
        
        all_docs = []
        
        # 1. Ricerca Semantica Classica
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            if results and results['documents'] and len(results['documents'][0]) > 0:
                all_docs.extend(results['documents'][0])
        except Exception as e:
            logger.error(f"Errore durante la ricerca vettoriale: {str(e)}")

        # 2. Ricerca Ibrida (Keyword Boosting)
        # Se l'utente nomina esplicitamente un codice come "D4" o "F1", 
        # forziamo il recupero dei 5 migliori chunk che contengono ESATTAMENTE quella parola.
        import re
        codes = re.findall(r'\b[A-Z]\d+\b', query.upper())
        if codes:
            for code in codes:
                try:
                    keyword_results = self.collection.query(
                        query_texts=[query],
                        n_results=5,
                        where_document={"$contains": code}
                    )
                    if keyword_results and keyword_results['documents'] and len(keyword_results['documents'][0]) > 0:
                        all_docs.extend(keyword_results['documents'][0])
                except Exception as e:
                    logger.warning(f"Filtro keyword '{code}' fallito: {e}")

        # 3. Rimuovi duplicati
        unique_docs = []
        for doc in all_docs:
            if doc not in unique_docs:
                unique_docs.append(doc)

        if not unique_docs:
            return ""

        context = "\n\n--- FRAMMENTO MANUALE ---\n\n".join(unique_docs)
        return context
