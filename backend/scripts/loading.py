from functools import lru_cache


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
