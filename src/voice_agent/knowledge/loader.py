import json
from pathlib import Path

KNOWLEDGE_PATH = Path(__file__).parent / "product_knowledge.json"

def load_product_knowledge() -> dict:
    with open(KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
