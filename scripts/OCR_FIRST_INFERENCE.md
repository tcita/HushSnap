# OCR First-Inference Latency & Buffer Lifecycle

Why the first OCR on a large screenshot is markedly slower than steady-state
(and a single-line crop is fast even cold), what gets allocated, and why we
intentionally do **not** add a dummy inference to pre-commit buffers.

## TL;DR

- There are **two distinct cold-start costs**, and `get_ppocr_engine`
  only addresses one of them:
  1. Model load (session construction, mmap, graph optimization) - ~300ms,
     paid by `get_ppocr_engine()` before any inference.
  2. First-inference buffer commit - the cost this doc is about; engine load
     does NOT touch it.
- First-inference slowness is **not** from reading ONNX weight pages (measured:
  zero disk I/O on first inference). It is ONNX Runtime committing the
  **detector's intermediate tensor buffers** (size tracks the input image) and
  first-writing them, which triggers a storm of **demand-zero page faults**
  (confirmed by direct classified-counter measurement: Demand Zero Faults/sec
  spikes ~0.8-1.65M/s during the cold window, Pages Input/sec stays 0 - so
  demand-zero, not hard).
- Those buffers, once committed, are **never reclaimed in production** -
  `release_engine()` is dead code. They live for the process lifetime.
- `idle-trim` only moves physical pages to the standby list; it does not free
  the committed buffers. The next inference **soft-faults** them back cheaply
  (~tens of ms extra vs warm), because the pages are still in RAM on standby.
- Decision: **do not add a dummy inference to pre-commit buffers.** Buffer size
  self-adapts to the user's actual capture sizes, which is strictly better than
  pre-committing a guessed size.

## Two cold starts (do not confuse them)

This is the key terminology trap. "Cold start" is used for two unrelated
one-time costs:

1. **Model-load cold start** - process/session initialization. Constructing
   `RapidOCR`, building the ORT `InferenceSession`, mmap'ing the `.onnx`
   files, graph optimization. ~300ms, ~40k faults. This is what `get_ppocr_engine`
   eliminates by calling `get_ppocr_engine()` ahead of time.
2. **First-inference cold start** - the intermediate-tensor buffers have never
   been allocated. The first `engine(arr)` commits them and first-writes them,
   triggering demand-zero faults. This is the ~1s+ cost on large images that
   `run_ocr` exposes. `get_ppocr_engine` does NOT run an inference, so it does
   nothing for this cost.

Verified in the benchmark's own path: `_wait_for_load` blocks until
`load_finished`, by which point WS ~140MB / ~40k faults (model load only,
no inference). Iteration 0 then pays the first-inference cost on top - so
iter0 is clean first-inference cold start, not contaminated by model load.

## Symptom

`run_ocr.py` on a full screenshot reports a latency in the same range as the
benchmark's **cold** iteration; the benchmark reports its **warm** average,
which is markedly lower. Both run the identical pipeline
(`OcrService.recognize` -> `recognize_ppocr_qimage` -> `get_ppocr_engine()` ->
`engine(arr)`). The difference is purely **which inference is being timed**:
run_ocr times the one cold inference; the benchmark reports the warm average
(`iter_results[1:]`, "excluding cold iteration 0"). Same cold cost, different
framing.

## Root cause: det tensor size = input image size

The dominant first-inference cost is the detector's intermediate tensors. In
`rapidocr/ch_ppocr_det/utils.py`, `DetPreProcess.resize` with
`limit_side_len=32`, `limit_type="min"`: when `min(h,w) > 32` the ratio is
`1.0` - **large images are not downscaled**. So the det input tensor is the
original image size, rounded to a multiple of 32, plus multi-layer feature
maps. A full-screen capture produces a very large tensor; a single-line crop
produces a tiny one.

On the first `session.run`, ORT `VirtualAlloc`-commits these buffers.
`VirtualAlloc(MEM_COMMIT)` only books the virtual address range - **no
physical pages are assigned yet** (Windows lazy allocation). When the
inference first writes each 4KB page, the CPU finds no physical mapping and
triggers a **demand-zero fault**: the kernel allocates a zeroed physical page
and maps it. This is a normal demand-paging mechanism, not an error and not a
disk read (measured: zero disk I/O on first inference). A large det tensor is
tens of thousands of pages, so first inference triggers millions of
demand-zero faults.

The first-inference fault count scales with tensor size, hence with input
image size. The proof is the contrast between a large first capture and a
small first capture: a full screenshot's first inference is orders of
magnitude slower (and faults orders of magnitude more) than a single-line
crop's first inference. If the cost were reading fixed model weights, both
would pay equally - they do not. (`session.run` has no buffer reuse;
`enable_cpu_mem_arena=False` by HushSnap default.)

What makes the first-inference faults expensive while the post-trim faults
(see below) are cheap is their **kind**, not their count:

- **First inference** pays **demand-zero faults** - kernel allocates + zeroes
  a fresh physical page for each. These are the millions-of-faults,
  ~1s-on-large-images cost.
- **Post-trim re-inference** pays **soft faults** - the pages were already
  written, trim only moved them to the standby list, so the kernel just
  re-maps the existing physical page (no allocate, no zero). Measured: a
  trimmed re-inference costs only ~tens of ms more than a warm one, despite a
  similar fault count, because soft faults are far cheaper per fault than
  demand-zero faults.

The exact per-fault cost split between demand-zero and soft is not something
`PageFaultCount` (which does not classify fault kind) can measure directly;
the demand-zero vs soft distinction itself is certain (zero disk I/O on first
inference; ~tens-of-ms extra after trim), the precise share of the ~1s
attributable to fault handling vs other first-run overhead is not pinned down
here.

### Direct measurement: it IS demand-zero faults, not hard faults

The distinction above was confirmed by sampling the system-wide classified
fault counters (`\Memory\Demand Zero Faults/sec`, `\Memory\Transition
Faults/sec`, `\Memory\Pages Input/sec`) via `typeperf` across a cold-first /
warm / post-trim / small-image sequence, correlated against per-phase
windows and the per-process `PageFaultCount` delta:

- **Cold first inference (4.png, 4181ms, +3.62M proc faults):** the inference
  window coincides with a **Demand Zero Faults/sec spike of ~0.8-1.65M/s**
  (vs a ~3-5k/s idle baseline). This is the demand-zero signature - kernel
  allocating + zeroing fresh physical pages for the newly-committed det
  tensors. **Pages Input/sec stayed 0** throughout - no disk reads, which
  rules out hard faults definitively (a hard fault's defining characteristic
  is a disk page-in).
- **Warm 2nd inference (3278ms, +1.13M proc faults):** still ~0.4-0.5M/s
  demand-zero - the steady-state per-inference faults are also demand-zero
  (ORT re-touches/re-allocates working buffers each call), just far fewer
  than the first because the big det bucket is already committed.
- **Post-trim re-inference (3308ms, +1.15M proc faults):** demand-zero
  ~0.2-0.5M/s **plus a visible Transition Faults/sec bump (~3-11k/s vs ~150/s
  baseline)** - the soft-fault signature, pages being re-mapped from the
  standby list that trim relocated. Same total fault count as warm but a
  portion shifted from demand-zero to (cheaper) transition.
- **Small image 0.png (23ms, +2.6k proc faults):** all counters collapse to
  baseline (~3k/s demand-zero, ~0 transition, 0 pages input). A tiny tensor
  faults almost nothing.

Conclusion: the first-inference cost **is** fault-driven, the fault kind is
**demand-zero** (not hard - zero page-ins measured), and the cold-vs-warm
delta (~900ms, ~2.5M extra faults) is almost entirely the one-time
demand-zero storm of committing the det tensor pages. `Pages Input/sec == 0`
across all phases is the hard-fault refutation. (Caveat: these are
system-wide counters at 1s granularity - `typeperf`'s `-si` only accepts
whole seconds - so they cannot attribute faults to HushSnap's PID, only
corroborate that the spikes coincide with the in-process inference windows.
Per-process classified fault attribution would need ETW/xperf, which was not
required here since the page-in=0 result already settles the hard-fault
question.)

## Buffer lifecycle: commit once, keep forever

The intermediate-tensor buffers commit on first inference and are never
returned to the OS in production. Two experiments make the mechanism visible.

**Experiment A - same big image, repeated, then trim.** Run the same large
image several times, snapshotting process commit (PrivateUsage /
PagefileUsage) and working set before/after each run, and after an
`idle-trim`. Observe: commit jumps on the first run and stays flat for every
subsequent run and through idle-trim; working set, by contrast, collapses to
~nothing after idle-trim and refills on the next run. The split is the whole
story - `idle-trim` (`SetProcessWorkingSetSize(-1,-1)`) only relocates
physical pages to the standby list, so the next inference soft-faults them
back cheaply (no disk read; measured ~tens of ms extra vs warm). The
committed virtual pages are never freed, so only the first run pays the
demand-zero faults.

**Experiment B - escalating sizes, the commit staircase.** Feed a sequence of
increasing image sizes - an extreme small image (e.g. 1x1), a small crop
(single-line), then a large full screenshot - and watch commit between steps.
The clearest framing is to interleave an extreme-small and an extreme-large:
the small image moves commit only a hair and a same-size repeat moves it not
at all; the large image jumps commit by a visible chunk; a repeat of the
large image does not move it again. Repeating this with the order reversed
(large first, then small) shows the small image adds nothing once the large
bucket is already committed. The takeaway: commit tracks ORT's internal
per-tensor-shape buckets, not a single "max size seen" - a small image whose
shape falls in an already-committed bucket adds zero commit, while a new large
shape allocates a fresh bucket that is then kept. Either way the direction is
one-way: commit grows on novel shapes, never shrinks.

The only thing that frees the buffers is `release_engine()` (sets `_engine =
None`; ORT session destructor `VirtualFree`s). **`release_engine()` is dead
code in production** - grep finds zero call sites in `hushsnap/`; it is used
only by `scripts/benchmark_cpu_opts.py` to reset between A/B configs.
Production never releases the engine, so buffers accompany the `_engine`
singleton until process exit. The "release on long idle" path does not exist;
`idle-trim` (physical pages only) is the sole memory-recovery mechanism.

## Why no dummy inference to pre-commit buffers

A dummy inference to pre-commit buffers faces an unsolvable size problem,
because buffer size is keyed to the input image shape:

- An **extreme-small dummy** commits only the small bucket and does NOT
  pre-cover a real large image's big det buffer. Verified by Experiment B's
  framing: after a small dummy, a large first inference still pays the full
  cold commit (the large bucket has to be allocated anyway).
- A **large dummy** does pre-cover the large bucket, but it costs a full
  background inference at startup and pre-commits the large bucket for users
  who only ever capture small regions - paying a cost they would never incur.

We cannot predict what the user will capture. Letting the buffer self-adapt -
commit whatever the first real capture needs, then keep it - is strictly
better:

- Small-region users stay light: a small first capture commits only the small
  bucket and runs fast even cold.
- Big-image users pay the one-time first-capture tax, then run warm forever
  for that size class.
- The OS standby list handles physical-RAM pressure automatically; commit is
  only freed at process exit.

The current `get_ppocr_engine` (session load only, no inference) is the correct
minimum: it eliminates the model-load cold start (#1) and defers buffer
commit to the real first capture so the size self-adapts. The one-time
first-inference cold start (#2) on a novel large size is acceptable and
intentional.

## If memory amplification ever matters

A user who captures one rare huge image keeps that large commit for the
process lifetime (no reclaim path). The magnitude is small (det-bucket-class,
nothing like the hundreds of MB of an enabled arena) and fits the established
memory-for-speed tradeoff. If it ever needs addressing, the lever is wiring up
`release_engine()` on long idle (currently unused) - NOT a dummy load inference.

## Key files

- `hushsnap/ocr/ppocr.py` - `get_ppocr_engine` (1291), `release_engine` (1338, dead
  in prod), `get_ppocr_engine` (1291), `_trim_working_set` (1234)
- `hushsnap/ocr/ocr_service.py` - `OcrService.recognize` (shared pipeline)
- `hushsnap/system/memory_utils.py` - `trim_working_set`
  (`SetProcessWorkingSetSize(-1,-1)`)
- `rapidocr/ch_ppocr_det/utils.py` - `DetPreProcess.resize`
  (limit_side_len=32 keeps big tensors)
- `ocr_batch/run_ocr.py` - single-image debug entry (times the cold inference)
- `hushsnap/benchmark/_runner.py` - reports warm avg, excludes iter 0
