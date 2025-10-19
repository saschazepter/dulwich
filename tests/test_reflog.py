# test_reflog.py -- tests for reflog.py
# Copyright (C) 2015 Jelmer Vernooij <jelmer@jelmer.uk>
#
# SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-or-later
# Dulwich is dual-licensed under the Apache License, Version 2.0 and the GNU
# General Public License as published by the Free Software Foundation; version 2.0
# or (at your option) any later version. You can redistribute it and/or
# modify it under the terms of either of these two licenses.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# You should have received a copy of the licenses; if not, see
# <http://www.gnu.org/licenses/> for a copy of the GNU General Public License
# and <http://www.apache.org/licenses/LICENSE-2.0> for a copy of the Apache
# License, Version 2.0.
#

"""Tests for dulwich.reflog."""

import tempfile
from io import BytesIO

from dulwich.objects import ZERO_SHA, Blob, Commit, Tree
from dulwich.reflog import (
    drop_reflog_entry,
    expire_reflog,
    format_reflog_line,
    iter_reflogs,
    parse_reflog_line,
    parse_reflog_spec,
    read_reflog,
)
from dulwich.repo import Repo

from . import TestCase


class ReflogSpecTests(TestCase):
    def test_parse_reflog_spec_basic(self) -> None:
        # Test basic reflog spec
        ref, index = parse_reflog_spec("HEAD@{1}")
        self.assertEqual(b"HEAD", ref)
        self.assertEqual(1, index)

    def test_parse_reflog_spec_with_full_ref(self) -> None:
        # Test with full ref name
        ref, index = parse_reflog_spec("refs/heads/master@{5}")
        self.assertEqual(b"refs/heads/master", ref)
        self.assertEqual(5, index)

    def test_parse_reflog_spec_bytes(self) -> None:
        # Test with bytes input
        ref, index = parse_reflog_spec(b"develop@{0}")
        self.assertEqual(b"develop", ref)
        self.assertEqual(0, index)

    def test_parse_reflog_spec_no_ref(self) -> None:
        # Test with no ref (defaults to HEAD)
        ref, index = parse_reflog_spec("@{2}")
        self.assertEqual(b"HEAD", ref)
        self.assertEqual(2, index)

    def test_parse_reflog_spec_invalid_no_brace(self) -> None:
        # Test invalid spec without @{
        with self.assertRaises(ValueError) as cm:
            parse_reflog_spec("HEAD")
        self.assertIn("Expected format: ref@{n}", str(cm.exception))

    def test_parse_reflog_spec_invalid_no_closing_brace(self) -> None:
        # Test invalid spec without closing brace
        with self.assertRaises(ValueError) as cm:
            parse_reflog_spec("HEAD@{1")
        self.assertIn("Expected format: ref@{n}", str(cm.exception))

    def test_parse_reflog_spec_invalid_non_numeric(self) -> None:
        # Test invalid spec with non-numeric index
        with self.assertRaises(ValueError) as cm:
            parse_reflog_spec("HEAD@{foo}")
        self.assertIn("Expected integer", str(cm.exception))


class ReflogLineTests(TestCase):
    def test_format(self) -> None:
        self.assertEqual(
            b"0000000000000000000000000000000000000000 "
            b"49030649db3dfec5a9bc03e5dde4255a14499f16 Jelmer Vernooij "
            b"<jelmer@jelmer.uk> 1446552482 +0000	"
            b"clone: from git://jelmer.uk/samba",
            format_reflog_line(
                b"0000000000000000000000000000000000000000",
                b"49030649db3dfec5a9bc03e5dde4255a14499f16",
                b"Jelmer Vernooij <jelmer@jelmer.uk>",
                1446552482,
                0,
                b"clone: from git://jelmer.uk/samba",
            ),
        )

        self.assertEqual(
            b"0000000000000000000000000000000000000000 "
            b"49030649db3dfec5a9bc03e5dde4255a14499f16 Jelmer Vernooij "
            b"<jelmer@jelmer.uk> 1446552482 +0000	"
            b"clone: from git://jelmer.uk/samba",
            format_reflog_line(
                None,
                b"49030649db3dfec5a9bc03e5dde4255a14499f16",
                b"Jelmer Vernooij <jelmer@jelmer.uk>",
                1446552482,
                0,
                b"clone: from git://jelmer.uk/samba",
            ),
        )

    def test_parse(self) -> None:
        reflog_line = (
            b"0000000000000000000000000000000000000000 "
            b"49030649db3dfec5a9bc03e5dde4255a14499f16 Jelmer Vernooij "
            b"<jelmer@jelmer.uk> 1446552482 +0000	"
            b"clone: from git://jelmer.uk/samba"
        )
        self.assertEqual(
            (
                b"0000000000000000000000000000000000000000",
                b"49030649db3dfec5a9bc03e5dde4255a14499f16",
                b"Jelmer Vernooij <jelmer@jelmer.uk>",
                1446552482,
                0,
                b"clone: from git://jelmer.uk/samba",
            ),
            parse_reflog_line(reflog_line),
        )


_TEST_REFLOG = (
    b"0000000000000000000000000000000000000000 "
    b"49030649db3dfec5a9bc03e5dde4255a14499f16 Jelmer Vernooij "
    b"<jelmer@jelmer.uk> 1446552482 +0000	"
    b"clone: from git://jelmer.uk/samba\n"
    b"49030649db3dfec5a9bc03e5dde4255a14499f16 "
    b"42d06bd4b77fed026b154d16493e5deab78f02ec Jelmer Vernooij "
    b"<jelmer@jelmer.uk> 1446552483 +0000	"
    b"clone: from git://jelmer.uk/samba\n"
    b"42d06bd4b77fed026b154d16493e5deab78f02ec "
    b"df6800012397fb85c56e7418dd4eb9405dee075c Jelmer Vernooij "
    b"<jelmer@jelmer.uk> 1446552484 +0000	"
    b"clone: from git://jelmer.uk/samba\n"
)


class ReflogDropTests(TestCase):
    def setUp(self) -> None:
        TestCase.setUp(self)
        self.f = BytesIO(_TEST_REFLOG)
        self.original_log = list(read_reflog(self.f))
        self.f.seek(0)

    def _read_log(self):
        self.f.seek(0)
        return list(read_reflog(self.f))

    def test_invalid(self) -> None:
        self.assertRaises(ValueError, drop_reflog_entry, self.f, -1)

    def test_drop_entry(self) -> None:
        drop_reflog_entry(self.f, 0)
        log = self._read_log()
        self.assertEqual(len(log), 2)
        self.assertEqual(self.original_log[0:2], log)

        self.f.seek(0)
        drop_reflog_entry(self.f, 1)
        log = self._read_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(self.original_log[1], log[0])

    def test_drop_entry_with_rewrite(self) -> None:
        drop_reflog_entry(self.f, 1, True)
        log = self._read_log()
        self.assertEqual(len(log), 2)
        self.assertEqual(self.original_log[0], log[0])
        self.assertEqual(self.original_log[0].new_sha, log[1].old_sha)
        self.assertEqual(self.original_log[2].new_sha, log[1].new_sha)

        self.f.seek(0)
        drop_reflog_entry(self.f, 1, True)
        log = self._read_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(ZERO_SHA, log[0].old_sha)
        self.assertEqual(self.original_log[2].new_sha, log[0].new_sha)


class RepoReflogTests(TestCase):
    def setUp(self) -> None:
        TestCase.setUp(self)
        self.test_dir = tempfile.mkdtemp()
        self.repo = Repo.init(self.test_dir)

    def tearDown(self) -> None:
        TestCase.tearDown(self)
        import shutil

        shutil.rmtree(self.test_dir)

    def test_read_reflog_nonexistent(self) -> None:
        # Reading a reflog that doesn't exist should return empty
        entries = list(self.repo.read_reflog(b"refs/heads/nonexistent"))
        self.assertEqual([], entries)

    def test_read_reflog_head(self) -> None:
        # Create a commit to generate a reflog entry
        blob = Blob.from_string(b"test content")
        self.repo.object_store.add_object(blob)

        tree = Tree()
        tree.add(b"test", 0o100644, blob.id)
        self.repo.object_store.add_object(tree)

        commit = Commit()
        commit.tree = tree.id
        commit.author = b"Test Author <test@example.com>"
        commit.committer = b"Test Author <test@example.com>"
        commit.commit_time = 1234567890
        commit.commit_timezone = 0
        commit.author_time = 1234567890
        commit.author_timezone = 0
        commit.message = b"Initial commit"
        self.repo.object_store.add_object(commit)

        # Manually write a reflog entry
        self.repo._write_reflog(
            b"HEAD",
            ZERO_SHA,
            commit.id,
            b"Test Author <test@example.com>",
            1234567890,
            0,
            b"commit (initial): Initial commit",
        )

        # Read the reflog
        entries = list(self.repo.read_reflog(b"HEAD"))
        self.assertEqual(1, len(entries))
        self.assertEqual(ZERO_SHA, entries[0].old_sha)
        self.assertEqual(commit.id, entries[0].new_sha)
        self.assertEqual(b"Test Author <test@example.com>", entries[0].committer)
        self.assertEqual(1234567890, entries[0].timestamp)
        self.assertEqual(0, entries[0].timezone)
        self.assertEqual(b"commit (initial): Initial commit", entries[0].message)

    def test_iter_reflogs(self) -> None:
        # Create commits and reflog entries
        blob = Blob.from_string(b"test content")
        self.repo.object_store.add_object(blob)

        tree = Tree()
        tree.add(b"test", 0o100644, blob.id)
        self.repo.object_store.add_object(tree)

        commit = Commit()
        commit.tree = tree.id
        commit.author = b"Test Author <test@example.com>"
        commit.committer = b"Test Author <test@example.com>"
        commit.commit_time = 1234567890
        commit.commit_timezone = 0
        commit.author_time = 1234567890
        commit.author_timezone = 0
        commit.message = b"Initial commit"
        self.repo.object_store.add_object(commit)

        # Write reflog entries for multiple refs
        self.repo._write_reflog(
            b"HEAD",
            ZERO_SHA,
            commit.id,
            b"Test Author <test@example.com>",
            1234567890,
            0,
            b"commit (initial): Initial commit",
        )
        self.repo._write_reflog(
            b"refs/heads/master",
            ZERO_SHA,
            commit.id,
            b"Test Author <test@example.com>",
            1234567891,
            0,
            b"branch: Created from HEAD",
        )
        self.repo._write_reflog(
            b"refs/heads/develop",
            ZERO_SHA,
            commit.id,
            b"Test Author <test@example.com>",
            1234567892,
            0,
            b"branch: Created from HEAD",
        )

        # Use iter_reflogs to get all reflogs
        import os

        logs_dir = os.path.join(self.repo.controldir(), "logs")
        reflogs = list(iter_reflogs(logs_dir))

        # Should have at least HEAD, refs/heads/master, and refs/heads/develop
        self.assertIn(b"HEAD", reflogs)
        self.assertIn(b"refs/heads/master", reflogs)
        self.assertIn(b"refs/heads/develop", reflogs)


class ReflogExpireTests(TestCase):
    def setUp(self) -> None:
        TestCase.setUp(self)
        # Create a reflog with entries at different timestamps
        self.f = BytesIO()
        # Old entry (timestamp: 1000000000)
        self.f.write(
            b"0000000000000000000000000000000000000000 "
            b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            b"Test <test@example.com> 1000000000 +0000\told entry\n"
        )
        # Medium entry (timestamp: 1500000000)
        self.f.write(
            b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb "
            b"Test <test@example.com> 1500000000 +0000\tmedium entry\n"
        )
        # Recent entry (timestamp: 2000000000)
        self.f.write(
            b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb "
            b"cccccccccccccccccccccccccccccccccccccccc "
            b"Test <test@example.com> 2000000000 +0000\trecent entry\n"
        )
        self.f.seek(0)

    def _read_log(self):
        self.f.seek(0)
        return list(read_reflog(self.f))

    def test_expire_no_criteria(self) -> None:
        # If no expiration criteria, nothing should be expired
        count = expire_reflog(self.f)
        self.assertEqual(0, count)
        log = self._read_log()
        self.assertEqual(3, len(log))

    def test_expire_by_time(self) -> None:
        # Expire entries older than timestamp 1600000000
        # Should remove the first two entries
        count = expire_reflog(self.f, expire_time=1600000000)
        self.assertEqual(2, count)
        log = self._read_log()
        self.assertEqual(1, len(log))
        self.assertEqual(b"recent entry", log[0].message)

    def test_expire_unreachable(self) -> None:
        # Test expiring unreachable entries
        # Mark the middle entry as unreachable
        def reachable_checker(sha):
            return sha != b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        count = expire_reflog(
            self.f,
            expire_unreachable_time=1600000000,
            reachable_checker=reachable_checker,
        )
        self.assertEqual(1, count)
        log = self._read_log()
        self.assertEqual(2, len(log))
        # First and third entries should remain
        self.assertEqual(b"old entry", log[0].message)
        self.assertEqual(b"recent entry", log[1].message)

    def test_expire_mixed(self) -> None:
        # Test with both expire_time and expire_unreachable_time
        def reachable_checker(sha):
            # Only the most recent entry is reachable
            return sha == b"cccccccccccccccccccccccccccccccccccccccc"

        count = expire_reflog(
            self.f,
            expire_time=1800000000,  # Would expire first two if reachable
            expire_unreachable_time=1200000000,  # Would expire first if unreachable
            reachable_checker=reachable_checker,
        )
        # First entry is unreachable and old enough -> expired
        # Second entry is unreachable but not old enough -> kept
        # Third entry is reachable and recent -> kept
        self.assertEqual(1, count)
        log = self._read_log()
        self.assertEqual(2, len(log))
        self.assertEqual(b"medium entry", log[0].message)
        self.assertEqual(b"recent entry", log[1].message)

    def test_expire_all_entries(self) -> None:
        # Expire all entries
        count = expire_reflog(self.f, expire_time=3000000000)
        self.assertEqual(3, count)
        log = self._read_log()
        self.assertEqual(0, len(log))
