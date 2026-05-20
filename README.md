# osbuild-depsolve-dnf-benchmark

Benchmark tooling for [osbuild-depsolve-dnf](https://github.com/osbuild/osbuild) — measures runtime, peak memory (via [memray](https://github.com/bloomberg/memray)), and CPU profiles across solver commands (`dump`, `search`, `depsolve`).

## Prerequisites

- Python 3
- [memray](https://pypi.org/project/memray/) (`pip install memray`)
- A checkout of the [osbuild](https://github.com/osbuild/osbuild) repository

## Usage

Run from the osbuild repo checkout root:

```sh
python3 benchmark.py \
    --tool-path /path/to/osbuild_repo/tools/osbuild-depsolve-dnf \
    --pythonpath /path/to/osbuild_repo \
    --queries-dir . \
    --profile \
```

### Options

| Flag | Description |
|---|---|
| `--tool-path` | Path to the `osbuild-depsolve-dnf` script (required) |
| `--queries-dir` | Directory containing JSON query files (required) |
| `--api-version` | API version to benchmark: `1` or `2` (required) |
| `--iterations` | Number of iterations per command (default: 5) |
| `--pythonpath` | `PYTHONPATH` for the tool invocation (default: `.`) |
| `--commands` | Commands to benchmark: `dump`, `search`, `depsolve` (default: all) |
| `--profile` | Enable cProfile profiling (saves `.prof` files) |
| `--dnf5` | Use the dnf5 solver instead of dnf4 |

## Query files

Sample query JSON files are included for both API versions:

- `dump.json` / `dump_v2.json`
- `search.json` / `search_v2.json`
- `depsolve.json` / `depsolve_v2.json`

## License

Apache-2.0
