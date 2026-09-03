# python-bench

A Python-based benchmarking suite for Lustre storage. It drives `ior`, `mdtest`,
`fio` (including a parallel dual-job mode), `mlperf` (via `mlpstorage`), and
`elbencho` from a single `config.json`, automatically striping Lustre pools,
managing per-tool client services (`fio --server`, `elbencho --service`),
starting/stopping SFA monitoring, and scraping key metrics (IOPS, throughput,
etc.) to the console as each test runs.

## Running

```
python3 main.py [options]
```

Options:

| Flag | Description |
| --- | --- |
| `--dry-run` | Print every command that would run for the full config without executing anything. |
| `--fstrim` | Run `fstrim` across the filesystem before testing. |
| `--drop-client-cache` | Drop page cache on all clients before testing. |
| `--drop-server-cache` | Drop page cache on all appliance/server nodes before testing. |
| `--delete-before-write` | Delete old test data before writing new files (per test, where applicable — see each tool's `file_layout`/cleanup notes below). |
| `--no-client-tune` | Skip running the client tuning scripts (`tune_checksums`/`tune_no_checksums`) before each test. |

Copy `config.template.json` to `config.json` and edit it for your environment.
Every entry under `tests.<tool>` is a dict of `param: [list of values]`; the
suite takes the cross product (`itertools.product`) of all lists for a tool to
generate the full test matrix (with the exception of `mlperf`'s `model`/
`file_counts` pairing — see the MLPerf section below).

## Global configuration

These top-level `config.json` keys apply across all tools:

| Key | Description |
| --- | --- |
| `data_path_root` | Root Lustre path under which each tool creates its own subdirectory (`<data_path_root>/ior`, `/fio`, `/mlperf`, `/elbencho`, etc.). |
| `machine_file` | Path to the default client machinefile (one host per line) used to pick clients for tests. |
| `tuning_files.tune_checksums` / `tune_no_checksums` | Scripts run against the client machinefile before each test, selected by that test's `checksums` param (`"on"`/`"off"`). |
| `mpirun_path` | Path to the `mpirun` binary used by `ior` and `mdtest`. |
| `mpi_conf` | Generic OpenMPI flags passed to `ior`/`mdtest` (e.g. UCX/interface settings, `--map-by`, `--bind-to`, `--allow-run-as-root`). **Not used by `mlperf`** — see below. |
| `log_path` | Root directory for all logs and results; each run gets its own `<log_path>/<tool>/<runid>_<timestamp>/` directory. |
| `tools.<name>` | Absolute path to each tool's binary (`ior`, `mdtest`, `fio`, `fio_parallel`, `mlperf`, `elbencho`). Only tools present here (and under `tests`) are run. |
| `appliances` | Map of appliance name → hostname/range (e.g. `sv30[0-3]`), used for SFA monitoring and cache-drop/fstrim targets. |

### MPI configuration — where things get set

There are **two independent MPI configs** in this project; don't mix them up:

- **`mpi_conf` (global, top-level)** — used only by `ior` and `mdtest`, passed
  verbatim to every `mpirun` invocation for those tools
  (`{mpirun_path} --host <hosts> {mpi_conf} --np <total_ppn> {tool_path} ...`).
  This is a good place for generic UCX/network/binding flags that apply to
  the whole cluster.
- **`mpi_params` (per-test, under `tests.mlperf`)** — used only by `mlperf`,
  passed to `mlpstorage` via its own `--mpi-params="..."` flag. `mlpstorage`
  requires MLPerf-specific MPI flags (e.g. `--map-by ppr:8:node:PE=6
  --bind-to hwthread --use-hwthread-cpus`, plus `--mca btl_tcp_if_include`/
  `--mca oob_tcp_if_include` for the management interface) which are
  different in shape from the generic `mpi_conf` string — **do not** reuse
  `mpi_conf`'s value here.

`fio`/`fio_parallel` and `elbencho` don't use MPI at all — `fio` uses its own
`--client`/`--server` distributed mode, and `elbencho` uses `clush` to run a
persistent `--service` on each client (started/stopped once per whole
`elbencho` test batch, not per individual test).

Client tuning (`tune_checksums`/`tune_no_checksums`) runs over `mpi_conf`'s
`machine_file` by default for every tool, unless a test overrides its
target machinefile (currently only `fio_parallel`'s per-job `machinefile`).

---

## Shared params

These param names mean the same thing across every tool that has them:

| Param | Description |
| --- | --- |
| `clients` | Number of client hosts to use (sliced from the top of `machine_file`). |
| `ppn` | Processes/threads per node (exact meaning is tool-specific — MPI ranks for `ior`/`mdtest`, fio `numjobs`, elbencho threads, simulated accelerators for `mlperf`). |
| `pools` | Lustre pool name to stripe into (or `"default"` for no pool). |
| `stripesize` | Lustre stripe size (`lfs setstripe -S`), e.g. `"1m"`. |
| `stripecount` | Lustre stripe count (`lfs setstripe -c`); falls back to `-C` (overstripe) if plain `-c` fails. |
| `checksums` | `"on"`/`"off"` — selects which tuning script runs on the clients before the test. |
| `directio` | `1` to add direct I/O flags (skip page cache). |
| `extra_args` | Free-form string appended verbatim to the command line. |
| `operation` | Test type — meaning is tool-specific (`write`/`read`, `randread`, `run`, etc. — see each section). |
| `file_layout` | `1` to run a sequential write "layout" pass before a random read, so every file is fully allocated first (avoids short-read errors). Combine with `--delete-before-write` to also wipe old data before laying out fresh files. |

---

## `ior`

Runs via `mpirun`. Config keys under `tests.ior`:

| Param | Description |
| --- | --- |
| `api` | IOR API, e.g. `"posix"`. |
| `blocksize` | Per-process file size (IOR `-b`). |
| `xfersize` | Transfer/block size per I/O (IOR `-t`). |
| `randomoffset` | `1` for random-offset I/O (IOR `-z`); combine with `file_layout: 1` to sequentially write the file first (mirrored write→random-read pattern). |
| `fileperproc` | `1` for one file per process (IOR `-F`). |
| `keep_files` | `1` to keep files after the run (IOR `-k`) instead of deleting them. |
| `operation` | `"write"` or `"read"`. |

`--delete-before-write` cleans the data path after a `read` test completes
(so the next `write` test starts fresh), and also before the file-layout
write pass if `randomoffset`+`file_layout` are both set.

## `mdtest`

Runs via `mpirun`. Config keys under `tests.mdtest`:

| Param | Description |
| --- | --- |
| `DOM` | `1` to set Distributed Object Metadata (`lfs setstripe -E 64k -L mdt`) on the data path. |
| `objects` | Number of files/dirs per process (`-n`). |
| `branching` | Directory tree branching factor (`-b`). |
| `depth` | Directory tree depth (`-z`). |
| `bytesread` / `byteswrite` | Bytes to read/write per file (`-e` / `-w`). |
| `iterations` | Number of test iterations (`-i`). |
| `itemsperdir` | Items per directory (`-I`). |
| `onlycreate` / `onlystat` / `onlyread` / `onlyremove` | Restrict the run to just that phase (`-C` / `-T` / `-E` / `-r`). |
| `dironly` / `fileonly` | Test only directories (`-D`) or only files (`-F`). |
| `uniquedir` | `1` for unique working directories per task (`-u`). |
| `verbose` | Verbosity flag string, e.g. `"-v"`. |
| `syncafterwrite` | `1` to sync after writes (`-y`). |

## `fio`

Uses `fio`'s client/server mode: `main.py` starts `fio --server` on all
clients via `clush` before the batch and kills it after. Config keys under
`tests.fio`:

| Param | Description |
| --- | --- |
| `ioengine` | fio I/O engine, e.g. `"libaio"`. |
| `rwmixread` | Read percentage for mixed `rw`/`randrw` workloads. |
| `iodepth` | I/O queue depth. |
| `filesize` | Size of each test file. |
| `blocksize` | fio block size. |
| `timebased` | `1` to run for a fixed `runtime` instead of until files are consumed. |
| `runtime` | Seconds to run when `timebased: 1`. |
| `operation` | `"write"`, `"read"`, `"randwrite"`, `"randread"`, `"rw"`, or `"randrw"`. |

For `write`/`randwrite` operations, a separate un-timed file-creation pass
(`fio_create.job`) runs first to lay out the files, then the actual
(optionally time-based) job runs against them. IOPS/BW are scraped from the
fio output and printed live. `--delete-before-write` cleans the data path
after `read`/`randread` tests.

## `fio_parallel`

Runs two independently configured `fio` jobs concurrently (e.g. a sustained
write on one pool while reading from another). Config under
`tests.fio_parallel`:

| Param | Description |
| --- | --- |
| `job1` / `job2` | Each is a full `fio`-style param dict (see above), and must each produce the **same number of combos** (they're paired 1:1, not cross-produced against each other). |
| `job1.machinefile` / `job2.machinefile` | Optional per-job client override; defaults to the global `machine_file` if unset. If both jobs share a machinefile, the `fio --server` daemon is started/stopped once for both rather than twice. |
| `job2_start_delay` | Seconds to wait after job1 starts before starting job2 (lets job1 ramp up first). |

## `mlperf`

Drives `mlpstorage` through its full workflow: `datasize` → `datagen` → `run`.
Config keys under `tests.mlperf`:

| Param | Description |
| --- | --- |
| `model` | List of MLPerf models to test, e.g. `["unet3d", "cosmoflow", "resnet50"]`. |
| `operation` | `"run"` (the normal case), or `"datasize"`/`"datagen"` to invoke just that phase standalone. |
| `accelerator_type` | Simulated accelerator type, e.g. `"h100"`. |
| `client_host_memory_gb` | Memory (GB) per client host, used by `datasize` sizing math and `run`. |
| `max_accelerators` | Max accelerators used by the `datasize` sizing calculation (can differ from `ppn`, which is the accelerator count for `run`). |
| `read_threads` | Reader thread count (`--param reader.read_threads`). |
| `loops` | Number of loops for the `run` phase. |
| `mpi_params` | MLPerf-specific MPI flags passed to `mlpstorage --mpi-params="..."` — see the MPI section above. **Do not** reuse the global `mpi_conf` here. |
| `skip_datagen` | `1` to skip the automatic `datagen` phase before `run` (use when the dataset is already generated on disk). |
| `file_counts` | Dict tagging a `dataset.num_files_train` count to each model, e.g. `{"unet3d": 224000, "resnet50": 114978}`. Wrap it in a single-element list (`[{...}]`) so it isn't cross-produced — it's looked up per model instead. A model with no entry here automatically runs the `datasize` phase to calculate its count. A model can be given a **list** of counts (e.g. `{"unet3d": [224000, 448000]}`) to run more than one file count for that model — this expands to one test per count for that model only, without cross-multiplying against the other models' counts. |

Because `model`/`file_counts` are tagged together rather than cross-produced,
`clients`, `ppn`, `accelerator_type`, etc. are still cross-produced against
every `(model, file_count)` pair as normal.

When `operation: "run"` and `skip_datagen` isn't set, each test automatically
resolves its file count (from `file_counts`, or by running `datasize`) and
runs `datagen` first; if `datagen` fails, the `run` phase is skipped for that
test rather than running against an incomplete dataset.

## `elbencho`

Uses a persistent `elbencho --service` daemon on each client (started once
for the whole `elbencho` test batch via `clush`, stopped once at the end).
Config keys under `tests.elbencho`:

| Param | Description |
| --- | --- |
| `filesize` | Size of each test file. |
| `files` | Number of files per client thread. |
| `blocksize` | I/O block size. |
| `iodepth` | I/O queue depth. |
| `blockvaralgo` / `blockvarpct` | Block content variance algorithm/percentage (for compressible/incompressible data patterns). |
| `dirs` | Number of directories to spread files across. |
| `runtime` | Time limit in seconds (`--timelimit`); `0` means run until all files are complete. |
| `sync` | `1` to `--sync` after writes. |
| `dropcache` | `1` to `--dropcache`. |
| `latency` | `1` to report latency (`--lat`). |
| `nolive` | `1` to suppress live stats (`--nolive`). |
| `randalgo` | Optional `--randalgo` value. |
| `verify` | Optional `--verify` value. |
| `random` | `1` for random access (`--rand`, the default if omitted), `0` for sequential. |
| `operation` | `"write"` or `"read"`. |

`file_layout: 1` on a `read` + `random: 1` combo runs an un-timed sequential
write pass first (mirrors `ior`'s pattern) so the random read doesn't hit
short-read errors against partially-written files; if `--delete-before-write`
is also set, old data is cleaned up (and directories recreated) before that
layout pass runs. If the layout pass fails, the read test for that combo is
skipped rather than running against incomplete data.
