import os
import sys
import time
import subprocess
import logging
import re
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PPOCR_PATH = PROJECT_ROOT / "hushsnap" / "ocr" / "ppocr.py"
BENCHMARK_SCRIPT = PROJECT_ROOT / "tools" / "benchmark.py"

TEST_IMAGES = ["0.png"]

CONFIGS = {
    "Side 736 (Mini)": {
        "intra": 8,
        "batch": 1,
        "arena": "False",
        "side": 736
    },
    "Side 960 (Fast)": {
        "intra": 8,
        "batch": 1,
        "arena": "False",
        "side": 960
    },
    "Side 1216 (Balanced)": {
        "intra": 8,
        "batch": 1,
        "arena": "False",
        "side": 1216
    },
    "Side 1536 (Current)": {
        "intra": 8,
        "batch": 1,
        "arena": "False",
        "side": 1536
    },
    "Side 2048 (Large)": {
        "intra": 8,
        "batch": 1,
        "arena": "False",
        "side": 2048
    },
    "Side 2560 (Ultra)": {
        "intra": 8,
        "batch": 1,
        "arena": "False",
        "side": 2560
    }
}

def update_config(intra, batch, arena, side=1536):
    content = PPOCR_PATH.read_text(encoding="utf-8")

    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if '"Rec.rec_batch_num":' in line:
            indent = line[:line.find('"')]
            new_lines.append(f'{indent}"Rec.rec_batch_num": {batch},')
        elif '"EngineConfig.onnxruntime.intra_op_num_threads":' in line:
            indent = line[:line.find('"')]
            new_lines.append(f'{indent}"EngineConfig.onnxruntime.intra_op_num_threads": {intra},')
        elif '"EngineConfig.onnxruntime.enable_cpu_mem_arena":' in line:
            indent = line[:line.find('"')]
            new_lines.append(f'{indent}"EngineConfig.onnxruntime.enable_cpu_mem_arena": {arena},')
        elif '"Global.max_side_len":' in line:
            indent = line[:line.find('"')]
            new_lines.append(f'{indent}"Global.max_side_len": {side},')
        else:
            new_lines.append(line)

    PPOCR_PATH.write_text("\n".join(new_lines), encoding="utf-8")

def run_benchmark(img_name):
    img_path = PROJECT_ROOT / "tools" / img_name
    if not img_path.exists():
        logger.error(f"Image not found: {img_path}")
        return None

    # Hijack benchmark.py
    bench_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")
    original_bench = bench_content
    new_bench = re.sub(r'img = os\.path\.join\(str\(project_root\), "tools", ".*?"\)',
                       f'img = os.path.join(str(project_root), "tools", "{img_name}")',
                       bench_content)
    BENCHMARK_SCRIPT.write_text(new_bench, encoding="utf-8")

    try:
        cmd = [sys.executable, str(BENCHMARK_SCRIPT)]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        output = result.stdout

        # Parse metrics
        latency_match = re.search(r'Average End-to-End Latency: ([\d.]+) ms', output)
        memory_match = re.search(r'Max Private Bytes: ([\d.]+) MB', output)

        return {
            "latency": float(latency_match.group(1)) if latency_match else 0,
            "memory": float(memory_match.group(1)) if memory_match else 0
        }
    finally:
        BENCHMARK_SCRIPT.write_text(original_bench, encoding="utf-8")

def main():
    print("\n" + "="*80)
    print(" Automated max_side_len Magic Number Validation Script")
    print("="*80)

    original_ppocr = PPOCR_PATH.read_text(encoding="utf-8")
    all_results = {}

    try:
        for config_name, params in CONFIGS.items():
            print(f"\n>>> Testing config: {config_name} (Side={params.get('side', 1536)})")
            update_config(params['intra'], params['batch'], params['arena'], params.get('side', 1536))

            all_results[config_name] = {}
            for img in TEST_IMAGES:
                print(f"    Testing sample: {img} ...", end="", flush=True)
                res = run_benchmark(img)
                if res:
                    print(f" Done. [Latency: {res['latency']:.1f}ms, Memory: {res['memory']:.1f}MB]")
                    all_results[config_name][img] = res
                else:
                    print(" Failed.")

    finally:
        PPOCR_PATH.write_text(original_ppocr, encoding="utf-8")

        print("\n" + "="*80)
        print(" Final Validation Report Summary")
        print("="*80)

        header = f"{'Config':<20} | {'Sample':<10} | {'Avg Latency':<15} | {'Peak Memory':<15}"
        print(header)
        print("-" * len(header))

        for config_name, samples in all_results.items():
            for img, res in samples.items():
                print(f"{config_name:<20} | {img:<10} | {res['latency']:>10.1f} ms | {res['memory']:>10.1f} MB")

        print("="*80)

if __name__ == "__main__":
    main()
