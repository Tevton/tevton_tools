from PySide6.QtCore import Signal
from .base_worker import BaseFTPWorker
from ..ftp_utils import format_size as _format_size


class BaseTransferWorker(BaseFTPWorker):
    """
    Base class for upload/download workers.
    Provides progress tracking and speed/ETA signal.
    """

    # Emitted every ~200ms during transfer:
    # (speed_mbps: float, transferred_bytes: float, total_bytes: float, eta_seconds: float)
    transfer_stats = Signal(float, float, float, float)

    def __init__(self, host, user, password, port):
        super().__init__(host, user, password, port)
        self.total_bytes = 0
