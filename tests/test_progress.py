"""Tests for progress reporting functionality."""

import io
import unittest

from dulwich.progress import ProgressReporter, StreamingPackDataHandler


class TestProgressReporter(unittest.TestCase):
    """Test the ProgressReporter class."""

    def setUp(self):
        self.messages = []
        self.reporter = ProgressReporter(
            progress_callback=lambda msg: self.messages.append(msg),
            report_interval=100,  # Report every 100 bytes for testing
        )

    def test_bytes_update(self):
        """Test that bytes updates trigger progress reports."""
        # First update shouldn't trigger report (< interval)
        self.reporter.update_bytes(50)
        self.assertEqual(len(self.messages), 0)

        # Second update should trigger report (total >= interval)
        self.reporter.update_bytes(60)
        self.assertEqual(len(self.messages), 1)
        self.assertIn(b"110.0 B", self.messages[0])

    def test_object_progress(self):
        """Test object count progress reporting."""
        self.reporter.set_total_objects(100)
        self.assertEqual(len(self.messages), 1)
        self.assertIn(b"0%", self.messages[0])

        self.reporter.update_objects(50)
        self.assertEqual(len(self.messages), 2)
        self.assertIn(b"50%", self.messages[1])
        self.assertIn(b"50/100", self.messages[1])

    def test_finish(self):
        """Test finish message."""
        self.reporter.set_total_objects(10)
        self.reporter.update_objects(10)
        self.reporter.update_bytes(1024)
        self.reporter.finish()

        finish_msg = self.messages[-1]
        self.assertIn(b"100%", finish_msg)
        self.assertIn(b"done", finish_msg)
        self.assertIn(b"\n", finish_msg)  # Should end with newline

    def test_format_bytes(self):
        """Test byte formatting."""
        self.assertEqual(self.reporter._format_bytes(0), "0.0 B")
        self.assertEqual(self.reporter._format_bytes(512), "512.0 B")
        self.assertEqual(self.reporter._format_bytes(1024), "1.0 KB")
        self.assertEqual(self.reporter._format_bytes(1536), "1.5 KB")
        self.assertEqual(self.reporter._format_bytes(1048576), "1.0 MB")
        self.assertEqual(self.reporter._format_bytes(1073741824), "1.0 GB")


class TestStreamingPackDataHandler(unittest.TestCase):
    """Test the StreamingPackDataHandler class."""

    def setUp(self):
        self.output = io.BytesIO()
        self.messages = []
        self.reporter = ProgressReporter(
            progress_callback=lambda msg: self.messages.append(msg), report_interval=10
        )

        # Create a wrapper that returns the number of bytes written
        def write_wrapper(data):
            return self.output.write(data) or len(data)

        self.handler = StreamingPackDataHandler(write_wrapper, self.reporter)

    def test_write_passthrough(self):
        """Test that data is written through correctly."""
        data = b"test data"
        self.handler.write(data)
        self.assertEqual(self.output.getvalue(), data)

    def test_header_parsing(self):
        """Test pack header parsing."""
        # Create a valid pack header: PACK + version (2) + num_objects (5)
        header = b"PACK" + (2).to_bytes(4, "big") + (5).to_bytes(4, "big")

        # Write header
        self.handler.write(header)

        # Should have detected 5 objects
        self.assertEqual(self.reporter.total_objects, 5)

        # Progress should have been reported
        self.assertTrue(any(b"5" in msg for msg in self.messages))

    def test_partial_header_handling(self):
        """Test handling of partial header data."""
        # Send header in chunks
        self.handler.write(b"PA")
        self.assertEqual(self.reporter.total_objects, None)

        self.handler.write(b"CK")
        self.assertEqual(self.reporter.total_objects, None)

        # Complete header
        header_rest = (2).to_bytes(4, "big") + (10).to_bytes(4, "big")
        self.handler.write(header_rest)
        self.assertEqual(self.reporter.total_objects, 10)

    def test_no_progress_reporter(self):
        """Test handler works without progress reporter."""

        def write_wrapper(data):
            return self.output.write(data) or len(data)

        handler = StreamingPackDataHandler(write_wrapper, None)
        data = b"test data"
        handler.write(data)
        self.assertEqual(self.output.getvalue(), data)


if __name__ == "__main__":
    unittest.main()
