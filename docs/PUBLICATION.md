# Public publication map

The public release separates compact source, versioned research content, and the
reader-facing narrative so each destination has a clear purpose.

| Surface | Public role |
|---|---|
| GitHub project repository | Source, tests, configuration, synthetic fixtures, reports, and documentation |
| GitHub Pages project page | Research question, evidence tiers, headline results, limitations, and reproduction links |
| Hugging Face Dataset | Versioned mirror of the publishable research package |
| Hugging Face Space | Interactive project and file explorer backed by the Dataset |

## Publication boundary

The release includes only files tracked by this repository. It excludes local
environments, caches, registered raw files, loan-granular intermediate and processed
tables, and generated output artifacts. In particular, nothing under `data/raw/`,
`data/interim/`, `data/processed/`, `data/cache/`, or `outputs/` is publishable.

The public reports contain only aggregates and model coefficients. They retain their
evidence-tier labels and the registered-data attribution block. The release does not
convert third-party material to a new license; `data/LICENSE_AND_REDISTRIBUTION.md`
remains authoritative.

## Permanent destinations

- Website: <https://yangxiaoshawn.github.io/projects/realestate/>
- GitHub: <https://github.com/YangXiaoShawn/YangXiaoShawn.github.io/tree/main/realestate>
- Dataset: <https://huggingface.co/datasets/ShawnChamberlain/open-economic-quant-research-data/tree/main/RealEstate>
- Space: <https://huggingface.co/spaces/ShawnChamberlain/open-economic-quant-research-observatory>
