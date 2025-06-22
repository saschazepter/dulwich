"""Integration tests for progress reporting in client operations."""

import io
import unittest

from dulwich.progress import ProgressReporter, StreamingPackDataHandler


class TestProgressIntegration(unittest.TestCase):
    """Test progress reporting integration."""

    def test_streaming_pack_handler_integration(self):
        """Test that StreamingPackDataHandler properly reports progress."""
        # Set up output and progress tracking
        output = io.BytesIO()
        progress_messages = []

        def progress_callback(msg: bytes) -> None:
            progress_messages.append(msg)

        # Create reporter and handler
        reporter = ProgressReporter(progress_callback, report_interval=50)

        def write_wrapper(data: bytes) -> int:
            return output.write(data) or len(data)

        handler = StreamingPackDataHandler(write_wrapper, reporter)

        # Simulate pack header
        pack_header = b"PACK" + (2).to_bytes(4, "big") + (10).to_bytes(4, "big")
        handler.write(pack_header)

        # Should have detected 10 objects
        self.assertTrue(any(b"10" in msg for msg in progress_messages))
        self.assertTrue(any(b"0%" in msg for msg in progress_messages))

        # Write more data to trigger progress updates
        for i in range(5):
            handler.write(b"x" * 60)  # 60 bytes each

        # Should have progress updates
        self.assertTrue(len(progress_messages) > 1)
        # Check that we have byte counts in messages
        byte_messages = [
            msg
            for msg in progress_messages
            if b"B" in msg or b"KB" in msg or b"MB" in msg
        ]
        self.assertTrue(
            len(byte_messages) > 0,
            f"No byte messages found. Messages: {progress_messages}",
        )

        # Verify data was written correctly
        self.assertEqual(output.getvalue(), pack_header + b"x" * 300)

    def test_progress_reporter_lifecycle(self):
        """Test complete progress reporting lifecycle."""
        messages = []
        reporter = ProgressReporter(lambda msg: messages.append(msg))

        # Set total objects
        reporter.set_total_objects(100)
        self.assertTrue(any(b"0%" in msg and b"0/100" in msg for msg in messages))

        # Update progress
        reporter.update_objects(25)
        self.assertTrue(any(b"25%" in msg and b"25/100" in msg for msg in messages))

        reporter.update_bytes(1024 * 1024)  # 1MB
        reporter.update_objects(75)
        self.assertTrue(any(b"100%" in msg and b"100/100" in msg for msg in messages))

        # Finish
        reporter.finish()
        finish_msg = messages[-1]
        self.assertIn(b"100%", finish_msg)
        self.assertIn(b"done", finish_msg)
        self.assertIn(b"1.0 MB", finish_msg)


if __name__ == "__main__":
    unittest.main()
