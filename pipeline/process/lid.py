"""
Language identification with GlotLID v3 (fastText, ISO 639-3 + script labels such as vie_Latn).

The model (~1.6 GB) is loaded once per process; callers size worker pools with that in mind.
Prediction looks at the first PREDICT_CHARS characters with newlines flattened, which is what
fastText was trained on (single lines). Vietnamese typed without diacritics is not recognised
as vie_Latn: GlotLID scores it low under unrelated labels.
"""
from functools import cache

from huggingface_hub import hf_hub_download

GLOTLID_REPO, GLOTLID_FILE = "cis-lmu/glotlid", "model.bin"
GLOTLID_REVISION = "e9592e0a9bc3b6f2a946de94faaa188d1dd6f522"
PREDICT_CHARS = 3000


@cache
def _model():
    import fasttext  # fasttext-predict: inference-only build with Windows wheels
    return fasttext.load_model(hf_hub_download(GLOTLID_REPO, GLOTLID_FILE, revision=GLOTLID_REVISION))


def identify(text):
    """(label, score), e.g. ("vie_Latn", 0.998); ("und", 0.0) for empty text."""
    sample = " ".join(text[:PREDICT_CHARS].split())
    if not sample:
        return "und", 0.0
    labels, scores = _model().predict(sample, k=1)
    return labels[0].removeprefix("__label__"), min(1.0, float(scores[0]))
