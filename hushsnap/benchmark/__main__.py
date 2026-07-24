"""CLI entry point — ``python -m hushsnap.benchmark``."""

import sys
import argparse
from pathlib import Path

from ._runner import BenchmarkRunner


def main():
    parser = argparse.ArgumentParser(
        description="HushSnap OCR benchmark — latency, memory, and shape profiling"
    )
    parser.add_argument(
        "image",
        help="Image filename in scratch/ or absolute path"
    )
    parser.add_argument(
        "-s", "--interval",
        type=float, default=5.0,
        help="Seconds between OCR iterations (default: 5.0)"
    )
    parser.add_argument(
        "-n", "--iterations",
        type=int, default=5,
        help="Number of OCR iterations (default: 5)"
    )
    parser.add_argument(
        "-p", "--profile",
        action="store_true",
        help="Enable high-frequency memory sampling on first warm iteration "
             "(rise/fall times, decay λ, AUC)"
    )
    parser.add_argument(
        "--gc-between",
        action="store_true",
        help="Run gc.collect() before each iteration. "
             "Off by default (matches production behaviour)."
    )
    parser.add_argument(
        "--idle-trim",
        action="store_true",
        help="Simulate production idle-trim timer between warm iterations. "
             "Calls trim_engine() after --trim-delay seconds of the interval, "
             "then waits the remaining interval before the next OCR. "
             "Use for A/B testing trim vs no-trim."
    )
    parser.add_argument(
        "--trim-delay",
        type=float, default=5.0,
        help="Seconds after previous OCR before firing idle trim "
             "(default: 5.0, matches production _trim_timer). "
             "Must be ≤ --interval.  Only meaningful with --idle-trim."
    )
    parser.add_argument(
        "--json",
        type=Path, default=None,
        help="Export results as JSON to this file path"
    )
    parser.add_argument(
        "--rec-batch-num",
        type=int, default=None,
        help="Override Rec.rec_batch_num (default: use production setting)"
    )
    parser.add_argument(
        "--intra-op-num-threads",
        type=int, default=None,
        help="Override EngineConfig.onnxruntime.intra_op_num_threads "
             "(default: use production setting)"
    )
    parser.add_argument(
        "--inter-op-num-threads",
        type=int, default=None,
        help="Override EngineConfig.onnxruntime.inter_op_num_threads "
             "(default: use production setting)"
    )
    parser.add_argument(
        "--max-side-len",
        type=int, default=None,
        help="Override Global.max_side_len (production: absent; RapidOCR "
             "default 2000). Only takes effect when use_preprocess_img=True."
    )
    parser.add_argument(
        "--arena",
        action="store_true",
        help="Enable ONNX Runtime CPU memory arena (production: False). "
             "Arena=True pools allocations for ~7%% speedup but retains "
             "~700 MB after OCR unless trimmed."
    )
    parser.add_argument(
        "--no-cls",
        action="store_true",
        help="Disable direction classifier (Global.use_cls=False). "
             "Tests impact of removing the 180° rotation classifier."
    )
    parser.add_argument(
        "--no-det",
        action="store_true",
        help="Disable text detector (Global.use_det=False). "
             "Sends full image directly to recognizer."
    )
    args = parser.parse_args()

    # Resolve image path
    img_path = Path(args.image)
    if not img_path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent
        # Try relative to CWD first, then fall back to scratch/ for backward compat
        if not img_path.exists():
            img_path = project_root / "scratch" / args.image
    if not img_path.exists():
        print(f"Error: Could not find test sample {img_path}")
        sys.exit(1)

    # ── Apply engine parameter overrides for A/B testing ──────────
    override_params: dict = {}
    if args.rec_batch_num is not None:
        override_params["Rec.rec_batch_num"] = args.rec_batch_num
    if args.intra_op_num_threads is not None:
        override_params["EngineConfig.onnxruntime.intra_op_num_threads"] = args.intra_op_num_threads
    if args.inter_op_num_threads is not None:
        override_params["EngineConfig.onnxruntime.inter_op_num_threads"] = args.inter_op_num_threads
    if args.max_side_len is not None:
        override_params["Global.max_side_len"] = args.max_side_len
    if args.arena:
        override_params["EngineConfig.onnxruntime.enable_cpu_mem_arena"] = True
    if args.no_cls:
        override_params["Global.use_cls"] = False
    if args.no_det:
        override_params["Global.use_det"] = False

    if override_params:
        from hushsnap.ocr.ppocr import set_engine_params_override
        set_engine_params_override(override_params)
        print(f"[A/B TEST] Engine overrides applied: {override_params}")

    with BenchmarkRunner(str(img_path)) as runner:
        result = runner.run(
            iterations=args.iterations,
            interval=args.interval,
            profile=args.profile,
            gc_between=args.gc_between,
            idle_trim=args.idle_trim,
            trim_delay_s=args.trim_delay,
            engine_overrides=override_params,
        )

    if args.json:
        result.to_json(args.json)
        print(f"Results written to {args.json}")


if __name__ == "__main__":
    main()
