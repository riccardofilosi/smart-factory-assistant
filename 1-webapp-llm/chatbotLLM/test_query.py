import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
from core.rag_engine import RAGRetriever

rag = RAGRetriever(db_path="db_vector")

results = rag.collection.get(where_document={"$contains": "DIFFICILE D4"})
print("Chunks found:", len(results['documents']))
for doc in results['documents']:
    print("--- FULL CHUNK ---")
    print(doc)
