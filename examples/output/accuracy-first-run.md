# First live run (kept for the record)

This was the first run with the Claude API, before one prompt change. The NF-e access key was then
requested as "44 digits, no spaces", and on both DANFEs the key the model returned was short
(42 and 43 digits instead of 44, with digits lost in long runs of 0s and 7s). The validation
caught both: the NF-e key check failed. The key is now copied as printed, keeping the spaces
between the 4-digit groups (see `app/schema.py`). The current results are in [accuracy.md](accuracy.md).

Field-by-field comparison between the extraction and the ground truth written by `scripts/make_samples.py` (what is printed on each fictitious document).

| Sample | Model | Fields correct | Validation | Time (s) |
|---|---|---|---|---|
| `danfe-exemplo-industrial.pdf` | claude-sonnet-5 | 52/53 (98%) | NF-e access key | 12.63 |
| `foto-danfe-parafusos.jpg` | claude-sonnet-5 | 46/47 (98%) | Line totals, Items vs subtotal, NF-e access key | 11.77 |
| `orden-compra-andina.pdf` | claude-sonnet-5 | 53/53 (100%) | all passed | 9.66 |

## danfe-exemplo-industrial.pdf

- nfe_access_key: expected '35260911222333000181550010000042171739201841', got '352609112223330018155001000042171739201841'

## foto-danfe-parafusos.jpg

- nfe_access_key: expected '31260911444777000161550020000187341503188423', got '3126091144477770016155002000187341503188423'

## orden-compra-andina.pdf

- no differences
