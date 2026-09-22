# -*- coding: utf-8 -*-
"""
Ricostruisce la knowledge base (ChromaDB) da manuale e guida webapp.

Uso, da chatbotLLM/:   python rebuild_kb.py

Richiede data/keys.txt. Le descrizioni delle immagini vengono salvate in
data/descrizioni_immagini.json: dalla seconda esecuzione in poi non si ripaga
l'analisi Vision.
"""
import logging

from core.api_manager import GeminiKeyRotator
from core.document_processor import KnowledgeBaseBuilder

logging.basicConfig(level=logging.INFO)

SORGENTI = {
    "manuale": "data/manuale_operativo.md",
    "guida_webapp": "data/guida_webapp.md",
    "fondamenti": "data/fondamenti_elettronica.md",
}

if __name__ == "__main__":
    rotator = GeminiKeyRotator(keys_file_path="data/keys.txt")
    builder = KnowledgeBaseBuilder(rotator, db_path="db_vector")
    builder.build_knowledge_base(SORGENTI)
    print("\nKnowledge base ricostruita in db_vector/.")
