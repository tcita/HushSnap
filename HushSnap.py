"""Application entry point: launch the main app."""

import time
# Record the absolute earliest possible start time in the Python process.
BOOT_START_TIME = time.perf_counter()

from hushsnap.app import main


if __name__ == "__main__":
    main(boot_start_time=BOOT_START_TIME)
