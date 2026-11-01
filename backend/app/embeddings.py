import os
import time

from google.genai import errors, types

from app.extractor import _get_client

BATCH = 32


def embed_texts(texts, task_type):
    """task_type: RETRIEVAL_DOCUMENT for stored text, RETRIEVAL_QUERY for a user's query."""
    dim = int(os.environ.get("EMBEDDING_DIM", "768"))
    out = []
    for i in range(0, len(texts), BATCH):
        batch = [t[:8000] for t in texts[i:i + BATCH]]
        for attempt in (1, 2, 3):
            try:
                resp = _get_client().models.embed_content(
                    model=os.environ["EMBEDDING_MODEL"],
                    contents=batch,
                    config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=dim),
                )
                out.extend(list(e.values) for e in resp.embeddings)
                break
            except errors.APIError as e:
                if e.code in (429, 500, 503, 504) and attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise
    return out
