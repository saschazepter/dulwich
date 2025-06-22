"""Progress reporting utilities for Dulwich operations."""

from typing import Callable, Optional


class ProgressReporter:
    """Helper class for reporting progress during pack operations."""

    def __init__(
        self,
        progress_callback: Optional[Callable[[bytes], None]] = None,
        report_interval: int = 65536,  # Report every 64KB by default
    ):
        """Initialize progress reporter.

        Args:
          progress_callback: Function to call with progress messages
          report_interval: Bytes between progress reports
        """
        self.progress_callback = progress_callback
        self.report_interval = report_interval
        self.bytes_received = 0
        self.objects_received = 0
        self.total_objects: Optional[int] = None
        self.last_report_bytes = 0

    def update_bytes(self, num_bytes: int) -> None:
        """Update the number of bytes received.

        Args:
          num_bytes: Number of new bytes received
        """
        self.bytes_received += num_bytes

        # Report progress if we've received enough bytes since last report
        if self.bytes_received - self.last_report_bytes >= self.report_interval:
            self.report_progress()
            self.last_report_bytes = self.bytes_received

    def set_total_objects(self, total: int) -> None:
        """Set the total number of objects expected.

        Args:
          total: Total number of objects in the pack
        """
        self.total_objects = total
        self.report_progress()

    def update_objects(self, count: int = 1) -> None:
        """Update the number of objects received.

        Args:
          count: Number of new objects received
        """
        self.objects_received += count
        self.report_progress()

    def report_progress(self) -> None:
        """Report current progress."""
        if not self.progress_callback:
            return

        if self.total_objects is not None:
            # Calculate percentage
            percentage = int((self.objects_received / self.total_objects) * 100)
            message = (
                f"Receiving objects: {percentage}% "
                f"({self.objects_received}/{self.total_objects}), "
                f"{self._format_bytes(self.bytes_received)}\r"
            )
        else:
            # Just report bytes if we don't know total objects yet
            message = (
                f"Receiving pack data: {self._format_bytes(self.bytes_received)}\r"
            )

        self.progress_callback(message.encode("ascii"))

    def _format_bytes(self, num_bytes: int) -> str:
        """Format bytes in human-readable form.

        Args:
          num_bytes: Number of bytes

        Returns:
          Formatted string like "1.5 MB"
        """
        bytes_float = float(num_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if bytes_float < 1024.0:
                return f"{bytes_float:.1f} {unit}"
            bytes_float /= 1024.0
        return f"{bytes_float:.1f} TB"

    def finish(self) -> None:
        """Report completion."""
        if self.progress_callback:
            if self.total_objects is not None:
                message = (
                    f"Receiving objects: 100% "
                    f"({self.objects_received}/{self.total_objects}), "
                    f"{self._format_bytes(self.bytes_received)}, done.\n"
                )
            else:
                message = f"Received {self._format_bytes(self.bytes_received)}, done.\n"
            self.progress_callback(message.encode("ascii"))


class StreamingPackDataHandler:
    """Handles pack data as it arrives, providing progress updates."""

    def __init__(
        self,
        write_func: Callable[[bytes], int],
        progress_reporter: Optional[ProgressReporter] = None,
    ):
        """Initialize the handler.

        Args:
          write_func: Function to write pack data to
          progress_reporter: Optional progress reporter
        """
        self.write_func = write_func
        self.progress_reporter = progress_reporter
        self._header_buffer = bytearray()
        self._header_parsed = False

    def write(self, data: bytes) -> int:
        """Write pack data and update progress.

        Args:
          data: Pack data chunk

        Returns:
          Number of bytes written
        """
        # Write the data
        n = self.write_func(data)

        # Update progress
        if self.progress_reporter:
            self.progress_reporter.update_bytes(len(data))

        # Try to parse header if we haven't yet
        if not self._header_parsed and len(self._header_buffer) < 12:
            # Accumulate data until we have the header
            self._header_buffer.extend(data[: 12 - len(self._header_buffer)])

            if len(self._header_buffer) >= 12:
                # Parse pack header
                if self._header_buffer[:4] == b"PACK":
                    # version = int.from_bytes(self._header_buffer[4:8], 'big')  # version not used yet
                    num_objects = int.from_bytes(self._header_buffer[8:12], "big")
                    self._header_parsed = True
                    if self.progress_reporter:
                        self.progress_reporter.set_total_objects(num_objects)

        return n
