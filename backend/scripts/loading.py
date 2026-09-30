import threading
from functools import lru_cache, wraps


def _load_once(cached):
    """Make an lru_cache'd loader thread-safe.

    `lru_cache` does not stop several threads that miss at the same moment from each running the
    loader. Under concurrent first requests that loaded the 500 MB encoder and the CAMeL
    disambiguator once per thread and ran a 4 GB container out of memory (handoff item 24).
    """
    lock = threading.Lock()

    @wraps(cached)
    def load():
        if cached.cache_info().currsize:
            return cached()
        with lock:
            return cached()

    load.cache_clear = cached.cache_clear
    return load


@lru_cache()
def get_mle():
    from camel_tools.disambig.mle import MLEDisambiguator

    return MLEDisambiguator.pretrained("calima-msa-r13")


@lru_cache()
def get_english_lemmatizer():
    from nltk.stem import WordNetLemmatizer

    return WordNetLemmatizer()


@lru_cache()
def get_model():
    """The Arabic sentence encoder (ONNX Runtime); see scripts/export_onnx.py to create it."""
    from scripts.arabic_encoder import load_encoder

    return load_encoder()


get_mle = _load_once(get_mle)
get_english_lemmatizer = _load_once(get_english_lemmatizer)
get_model = _load_once(get_model)
