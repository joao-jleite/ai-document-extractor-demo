# Accuracy on the bundled samples

Field-by-field comparison between the extraction and the ground truth written by `scripts/make_samples.py` (what is printed on each fictitious document).

How fields are compared (`norm()` in `scripts/run_samples.py`): text ignores case, accents and repeated spaces; numbers and IDs written with digits and `. / -` ignore that punctuation and leading zeros (`000.004.217` = `4217`, `001` = `1`); amounts are compared as numbers; for discount, freight, insurance, other charges and taxes, `0` and `null` (not printed) count as equal. *Exact match* counts the same fields with no normalisation at all: identical strings, equal numbers.

| Sample | Model | Fields correct | Exact match | Validation | Time (s) |
|---|---|---|---|---|---|
| `danfe-exemplo-industrial.pdf` | claude-sonnet-5 | 53/53 (100%) | 53/53 | all passed | 11.33 |
| `foto-danfe-parafusos.jpg` | claude-sonnet-5 | 47/47 (100%) | 47/47 | Line totals, Items vs subtotal | 11.37 |
| `orden-compra-andina.pdf` | claude-sonnet-5 | 53/53 (100%) | 53/53 | all passed | 9.91 |

## danfe-exemplo-industrial.pdf

- no differences

## foto-danfe-parafusos.jpg

- no differences

## orden-compra-andina.pdf

- no differences
