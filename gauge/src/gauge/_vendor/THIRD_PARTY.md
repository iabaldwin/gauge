# Multi-IF evaluation code

`multiif.py` is derived from `ifeval.py` from Meta's Multi-IF repository,
commit `1cdb53ed18499ad729e0766e5d3099dd5344406f`. Source: https://github.com/facebookresearch/Multi-IF/tree/1cdb53ed18499ad729e0766e5d3099dd5344406f
Licensed under Apache-2.0; see MULTIIF_LICENSE. The upstream header is retained.

Gauge uses the strict instruction checks with a deterministic language-detection
seed. The initial English subset excludes the two checkers requiring external NLTK
sentence models (`length_constraints:number_sentences` and
`change_case:capital_word_frequency`), and oversized word-count requirements.
The selected IDs, arguments, and filtering policy are recorded in each manifest.

This code license does not cover the benchmark dataset. Multi-IF data is
CC-BY-NC-2.0 and is downloaded separately, never bundled in Gauge distributions.

Gauge replaces the two eager Thai tokenizer imports with unsupported-language
errors. This avoids implicit corpus-cache writes/downloads. English checks are
unchanged; outputs requiring Thai tokenization are reported as unscored.
