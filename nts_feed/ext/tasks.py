import os
import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

_executor: Optional[ThreadPoolExecutor] = None


def get_executor(max_workers: Optional[int] = None) -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        if max_workers is None:
            try:
                max_workers = int(os.getenv('EXECUTOR_MAX_WORKERS', '8'))
            except Exception:
                max_workers = 8
        _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nts-bg")
        atexit.register(lambda: _executor.shutdown(wait=True))
    return _executor


