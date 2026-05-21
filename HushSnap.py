"""Application entry point: launch the main app."""

import os
# OpenBLAS pre-allocates ~32 MB of virtual memory per thread. At the
# default MAX_THREADS=24 that is ~768 MB committed but mostly untouched.
# OCR workloads do array conversion only, not BLAS compute, so a single
# thread keeps virtual memory minimal with no performance impact.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import time
# Record the absolute earliest possible start time in the Python process.
BOOT_START_TIME = time.perf_counter()

from hushsnap.app import main


if __name__ == "__main__":
    main(boot_start_time=BOOT_START_TIME)
