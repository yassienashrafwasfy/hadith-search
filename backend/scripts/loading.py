import os
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
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("intfloat/multilingual-e5-large")

    from features import load_features

    adapter_path = load_features().finetuned_adapter_path
    if adapter_path and os.path.exists(adapter_path):
        from peft import PeftModel

        model[0].auto_model = PeftModel.from_pretrained(
            model[0].auto_model,
            adapter_path,
        )
        model[0].auto_model = model[0].auto_model.merge_and_unload()

    return model
