#!/usr/bin/env python
# test_cli.py -- tests for dulwich.cli
# vim: expandtab
#
# Copyright (C) 2024 Jelmer Vernooij <jelmer@jelmer.uk>
#
# SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-or-later
# Dulwich is dual-licensed under the Apache License, Version 2.0 and the GNU
# General Public License as public by the Free Software Foundation; version 2.0
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

"""Tests for dulwich.cli."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import skipIf
from unittest.mock import MagicMock, patch

from dulwich import cli
from dulwich.cli import format_bytes, parse_relative_time
from dulwich.repo import Repo
from dulwich.tests.utils import (
    build_commit_graph,
)

from . import TestCase


class DulwichCliTestCase(TestCase):
    """Base class for CLI tests."""

    def setUp(self) -> None:
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir)
        self.repo_path = os.path.join(self.test_dir, "repo")
        os.mkdir(self.repo_path)
        self.repo = Repo.init(self.repo_path)
        self.addCleanup(self.repo.close)

    def _run_cli(self, *args, stdout_stream=None):
        """Run CLI command and capture output."""

        class MockStream:
            def __init__(self):
                self._buffer = io.BytesIO()
                self.buffer = self._buffer

            def write(self, data):
                if isinstance(data, bytes):
                    self._buffer.write(data)
                else:
                    self._buffer.write(data.encode("utf-8"))

            def getvalue(self):
                value = self._buffer.getvalue()
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    return value

            def __getattr__(self, name):
                return getattr(self._buffer, name)

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_cwd = os.getcwd()
        try:
            # Use custom stdout_stream if provided, otherwise use MockStream
            if stdout_stream:
                sys.stdout = stdout_stream
                if not hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer = sys.stdout
            else:
                sys.stdout = MockStream()

            sys.stderr = MockStream()

            os.chdir(self.repo_path)
            result = cli.main(list(args))
            return result, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            os.chdir(old_cwd)


class InitCommandTest(DulwichCliTestCase):
    """Tests for init command."""

    def test_init_basic(self):
        # Create a new directory for init
        new_repo_path = os.path.join(self.test_dir, "new_repo")
        result, stdout, stderr = self._run_cli("init", new_repo_path)
        self.assertTrue(os.path.exists(os.path.join(new_repo_path, ".git")))

    def test_init_bare(self):
        # Create a new directory for bare repo
        bare_repo_path = os.path.join(self.test_dir, "bare_repo")
        result, stdout, stderr = self._run_cli("init", "--bare", bare_repo_path)
        self.assertTrue(os.path.exists(os.path.join(bare_repo_path, "HEAD")))
        self.assertFalse(os.path.exists(os.path.join(bare_repo_path, ".git")))


class AddCommandTest(DulwichCliTestCase):
    """Tests for add command."""

    def test_add_single_file(self):
        # Create a file to add
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")

        result, stdout, stderr = self._run_cli("add", "test.txt")
        # Check that file is in index
        self.assertIn(b"test.txt", self.repo.open_index())

    def test_add_multiple_files(self):
        # Create multiple files
        for i in range(3):
            test_file = os.path.join(self.repo_path, f"test{i}.txt")
            with open(test_file, "w") as f:
                f.write(f"content {i}")

        result, stdout, stderr = self._run_cli(
            "add", "test0.txt", "test1.txt", "test2.txt"
        )
        index = self.repo.open_index()
        self.assertIn(b"test0.txt", index)
        self.assertIn(b"test1.txt", index)
        self.assertIn(b"test2.txt", index)


class RmCommandTest(DulwichCliTestCase):
    """Tests for rm command."""

    def test_rm_file(self):
        # Create, add and commit a file first
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Add test file")

        # Now remove it from index and working directory
        result, stdout, stderr = self._run_cli("rm", "test.txt")
        # Check that file is not in index
        self.assertNotIn(b"test.txt", self.repo.open_index())


class CommitCommandTest(DulwichCliTestCase):
    """Tests for commit command."""

    def test_commit_basic(self):
        # Create and add a file
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")
        self._run_cli("add", "test.txt")

        # Commit
        result, stdout, stderr = self._run_cli("commit", "--message=Initial commit")
        # Check that HEAD points to a commit
        self.assertIsNotNone(self.repo.head())


class LogCommandTest(DulwichCliTestCase):
    """Tests for log command."""

    def test_log_empty_repo(self):
        result, stdout, stderr = self._run_cli("log")
        # Empty repo should not crash

    def test_log_with_commits(self):
        # Create some commits
        c1, c2, c3 = build_commit_graph(
            self.repo.object_store, [[1], [2, 1], [3, 1, 2]]
        )
        self.repo.refs[b"HEAD"] = c3.id

        result, stdout, stderr = self._run_cli("log")
        self.assertIn("Commit 3", stdout)
        self.assertIn("Commit 2", stdout)
        self.assertIn("Commit 1", stdout)

    def test_log_reverse(self):
        # Create some commits
        c1, c2, c3 = build_commit_graph(
            self.repo.object_store, [[1], [2, 1], [3, 1, 2]]
        )
        self.repo.refs[b"HEAD"] = c3.id

        result, stdout, stderr = self._run_cli("log", "--reverse")
        # Check order - commit 1 should appear before commit 3
        pos1 = stdout.index("Commit 1")
        pos3 = stdout.index("Commit 3")
        self.assertLess(pos1, pos3)


class StatusCommandTest(DulwichCliTestCase):
    """Tests for status command."""

    def test_status_empty(self):
        result, stdout, stderr = self._run_cli("status")
        # Should not crash on empty repo

    def test_status_with_untracked(self):
        # Create an untracked file
        test_file = os.path.join(self.repo_path, "untracked.txt")
        with open(test_file, "w") as f:
            f.write("untracked content")

        result, stdout, stderr = self._run_cli("status")
        self.assertIn("Untracked files:", stdout)
        self.assertIn("untracked.txt", stdout)


class BranchCommandTest(DulwichCliTestCase):
    """Tests for branch command."""

    def test_branch_create(self):
        # Create initial commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Create branch
        result, stdout, stderr = self._run_cli("branch", "test-branch")
        self.assertIn(b"refs/heads/test-branch", self.repo.refs.keys())

    def test_branch_delete(self):
        # Create initial commit and branch
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")
        self._run_cli("branch", "test-branch")

        # Delete branch
        result, stdout, stderr = self._run_cli("branch", "-d", "test-branch")
        self.assertNotIn(b"refs/heads/test-branch", self.repo.refs.keys())


class CheckoutCommandTest(DulwichCliTestCase):
    """Tests for checkout command."""

    def test_checkout_branch(self):
        # Create initial commit and branch
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")
        self._run_cli("branch", "test-branch")

        # Checkout branch
        result, stdout, stderr = self._run_cli("checkout", "test-branch")
        self.assertEqual(
            self.repo.refs.read_ref(b"HEAD"), b"ref: refs/heads/test-branch"
        )


class TagCommandTest(DulwichCliTestCase):
    """Tests for tag command."""

    def test_tag_create(self):
        # Create initial commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Create tag
        result, stdout, stderr = self._run_cli("tag", "v1.0")
        self.assertIn(b"refs/tags/v1.0", self.repo.refs.keys())


class DiffCommandTest(DulwichCliTestCase):
    """Tests for diff command."""

    def test_diff_working_tree(self):
        # Create and commit a file
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("initial content\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Modify the file
        with open(test_file, "w") as f:
            f.write("initial content\nmodified\n")

        # Test unstaged diff
        result, stdout, stderr = self._run_cli("diff")
        self.assertIn("+modified", stdout)

    def test_diff_staged(self):
        # Create initial commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("initial content\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Modify and stage the file
        with open(test_file, "w") as f:
            f.write("initial content\nnew file\n")
        self._run_cli("add", "test.txt")

        # Test staged diff
        result, stdout, stderr = self._run_cli("diff", "--staged")
        self.assertIn("+new file", stdout)

    def test_diff_cached(self):
        # Create initial commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("initial content\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Modify and stage the file
        with open(test_file, "w") as f:
            f.write("initial content\nnew file\n")
        self._run_cli("add", "test.txt")

        # Test cached diff (alias for staged)
        result, stdout, stderr = self._run_cli("diff", "--cached")
        self.assertIn("+new file", stdout)

    def test_diff_commit(self):
        # Create two commits
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("first version\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=First")

        with open(test_file, "w") as f:
            f.write("first version\nsecond line\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Second")

        # Add working tree changes
        with open(test_file, "a") as f:
            f.write("working tree change\n")

        # Test single commit diff (should show working tree vs HEAD)
        result, stdout, stderr = self._run_cli("diff", "HEAD")
        self.assertIn("+working tree change", stdout)

    def test_diff_two_commits(self):
        # Create two commits
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("first version\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=First")

        # Get first commit SHA
        first_commit = self.repo.refs[b"HEAD"].decode()

        with open(test_file, "w") as f:
            f.write("first version\nsecond line\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Second")

        # Get second commit SHA
        second_commit = self.repo.refs[b"HEAD"].decode()

        # Test diff between two commits
        result, stdout, stderr = self._run_cli("diff", first_commit, second_commit)
        self.assertIn("+second line", stdout)

    def test_diff_commit_vs_working_tree(self):
        # Test that diff <commit> shows working tree vs commit (not commit vs parent)
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("first version\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=First")

        first_commit = self.repo.refs[b"HEAD"].decode()

        with open(test_file, "w") as f:
            f.write("first version\nsecond line\n")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Second")

        # Add changes to working tree
        with open(test_file, "w") as f:
            f.write("completely different\n")

        # diff <first_commit> should show working tree vs first commit
        result, stdout, stderr = self._run_cli("diff", first_commit)
        self.assertIn("-first version", stdout)
        self.assertIn("+completely different", stdout)

    def test_diff_with_paths(self):
        # Test path filtering
        # Create multiple files
        file1 = os.path.join(self.repo_path, "file1.txt")
        file2 = os.path.join(self.repo_path, "file2.txt")
        subdir = os.path.join(self.repo_path, "subdir")
        os.makedirs(subdir)
        file3 = os.path.join(subdir, "file3.txt")

        with open(file1, "w") as f:
            f.write("content1\n")
        with open(file2, "w") as f:
            f.write("content2\n")
        with open(file3, "w") as f:
            f.write("content3\n")

        self._run_cli("add", ".")
        self._run_cli("commit", "--message=Initial")

        # Modify all files
        with open(file1, "w") as f:
            f.write("modified1\n")
        with open(file2, "w") as f:
            f.write("modified2\n")
        with open(file3, "w") as f:
            f.write("modified3\n")

        # Test diff with specific file
        result, stdout, stderr = self._run_cli("diff", "--", "file1.txt")
        self.assertIn("file1.txt", stdout)
        self.assertNotIn("file2.txt", stdout)
        self.assertNotIn("file3.txt", stdout)

        # Test diff with directory
        result, stdout, stderr = self._run_cli("diff", "--", "subdir")
        self.assertNotIn("file1.txt", stdout)
        self.assertNotIn("file2.txt", stdout)
        self.assertIn("file3.txt", stdout)

        # Test staged diff with paths
        self._run_cli("add", "file1.txt")
        result, stdout, stderr = self._run_cli("diff", "--staged", "--", "file1.txt")
        self.assertIn("file1.txt", stdout)
        self.assertIn("+modified1", stdout)

        # Test diff with multiple paths (file2 and file3 are still unstaged)
        result, stdout, stderr = self._run_cli(
            "diff", "--", "file2.txt", "subdir/file3.txt"
        )
        self.assertIn("file2.txt", stdout)
        self.assertIn("file3.txt", stdout)
        self.assertNotIn("file1.txt", stdout)

        # Test diff with commit and paths
        first_commit = self.repo.refs[b"HEAD"].decode()
        with open(file1, "w") as f:
            f.write("newer1\n")
        result, stdout, stderr = self._run_cli("diff", first_commit, "--", "file1.txt")
        self.assertIn("file1.txt", stdout)
        self.assertIn("-content1", stdout)
        self.assertIn("+newer1", stdout)
        self.assertNotIn("file2.txt", stdout)


class FilterBranchCommandTest(DulwichCliTestCase):
    """Tests for filter-branch command."""

    def setUp(self):
        super().setUp()
        # Create a more complex repository structure for testing
        # Create some files in subdirectories
        os.makedirs(os.path.join(self.repo_path, "subdir"))
        os.makedirs(os.path.join(self.repo_path, "other"))

        # Create files
        files = {
            "README.md": "# Test Repo",
            "subdir/file1.txt": "File in subdir",
            "subdir/file2.txt": "Another file in subdir",
            "other/file3.txt": "File in other dir",
            "root.txt": "File at root",
        }

        for path, content in files.items():
            file_path = os.path.join(self.repo_path, path)
            with open(file_path, "w") as f:
                f.write(content)

        # Add all files and create initial commit
        self._run_cli("add", ".")
        self._run_cli("commit", "--message=Initial commit")

        # Create a second commit modifying subdir
        with open(os.path.join(self.repo_path, "subdir/file1.txt"), "a") as f:
            f.write("\nModified content")
        self._run_cli("add", "subdir/file1.txt")
        self._run_cli("commit", "--message=Modify subdir file")

        # Create a third commit in other dir
        with open(os.path.join(self.repo_path, "other/file3.txt"), "a") as f:
            f.write("\nMore content")
        self._run_cli("add", "other/file3.txt")
        self._run_cli("commit", "--message=Modify other file")

        # Create a branch
        self._run_cli("branch", "test-branch")

        # Create a tag
        self._run_cli("tag", "v1.0")

    def test_filter_branch_subdirectory_filter(self):
        """Test filter-branch with subdirectory filter."""
        # Run filter-branch to extract only the subdir
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--subdirectory-filter", "subdir"
        )

        # Check that the operation succeeded
        self.assertEqual(result, 0)
        self.assertIn("Rewrite HEAD", stdout)

        # filter-branch rewrites history but doesn't update working tree
        # We need to check the commit contents, not the working tree
        # Reset to the rewritten HEAD to update working tree
        self._run_cli("reset", "--hard", "HEAD")

        # Now check that only files from subdir remain at root level
        self.assertTrue(os.path.exists(os.path.join(self.repo_path, "file1.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.repo_path, "file2.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.repo_path, "README.md")))
        self.assertFalse(os.path.exists(os.path.join(self.repo_path, "root.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.repo_path, "other")))
        self.assertFalse(os.path.exists(os.path.join(self.repo_path, "subdir")))

        # Check that original refs were backed up
        original_refs = [
            ref for ref in self.repo.refs.keys() if ref.startswith(b"refs/original/")
        ]
        self.assertTrue(
            len(original_refs) > 0, "No original refs found after filter-branch"
        )

    @skipIf(sys.platform == "win32", "sed command not available on Windows")
    def test_filter_branch_msg_filter(self):
        """Test filter-branch with message filter."""
        # Run filter-branch to prepend [FILTERED] to commit messages
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--msg-filter", "sed 's/^/[FILTERED] /'"
        )

        self.assertEqual(result, 0)

        # Check that commit messages were modified
        result, stdout, stderr = self._run_cli("log")
        self.assertIn("[FILTERED] Modify other file", stdout)
        self.assertIn("[FILTERED] Modify subdir file", stdout)
        self.assertIn("[FILTERED] Initial commit", stdout)

    def test_filter_branch_env_filter(self):
        """Test filter-branch with environment filter."""
        # Run filter-branch to change author email
        env_filter = """
        if [ "$GIT_AUTHOR_EMAIL" = "test@example.com" ]; then
            export GIT_AUTHOR_EMAIL="filtered@example.com"
        fi
        """
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--env-filter", env_filter
        )

        self.assertEqual(result, 0)

    def test_filter_branch_prune_empty(self):
        """Test filter-branch with prune-empty option."""
        # Create a commit that only touches files outside subdir
        with open(os.path.join(self.repo_path, "root.txt"), "a") as f:
            f.write("\nNew line")
        self._run_cli("add", "root.txt")
        self._run_cli("commit", "--message=Modify root file only")

        # Run filter-branch to extract subdir with prune-empty
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--subdirectory-filter", "subdir", "--prune-empty"
        )

        self.assertEqual(result, 0)

        # The last commit should have been pruned
        result, stdout, stderr = self._run_cli("log")
        self.assertNotIn("Modify root file only", stdout)

    @skipIf(sys.platform == "win32", "sed command not available on Windows")
    def test_filter_branch_force(self):
        """Test filter-branch with force option."""
        # Run filter-branch once with a filter that actually changes something
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--msg-filter", "sed 's/^/[TEST] /'"
        )
        self.assertEqual(result, 0)

        # Check that backup refs were created
        # The implementation backs up refs under refs/original/
        original_refs = [
            ref for ref in self.repo.refs.keys() if ref.startswith(b"refs/original/")
        ]
        self.assertTrue(len(original_refs) > 0, "No original refs found")

        # Run again without force - should fail
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--msg-filter", "sed 's/^/[TEST2] /'"
        )
        self.assertEqual(result, 1)
        self.assertIn("Cannot create a new backup", stdout)
        self.assertIn("refs/original", stdout)

        # Run with force - should succeed
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--force", "--msg-filter", "sed 's/^/[TEST3] /'"
        )
        self.assertEqual(result, 0)

    @skipIf(sys.platform == "win32", "sed command not available on Windows")
    def test_filter_branch_specific_branch(self):
        """Test filter-branch on a specific branch."""
        # Switch to test-branch and add a commit
        self._run_cli("checkout", "test-branch")
        with open(os.path.join(self.repo_path, "branch-file.txt"), "w") as f:
            f.write("Branch specific file")
        self._run_cli("add", "branch-file.txt")
        self._run_cli("commit", "--message=Branch commit")

        # Run filter-branch on the test-branch
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--msg-filter", "sed 's/^/[BRANCH] /'", "test-branch"
        )

        self.assertEqual(result, 0)
        self.assertIn("Ref 'refs/heads/test-branch' was rewritten", stdout)

        # Check that only test-branch was modified
        result, stdout, stderr = self._run_cli("log")
        self.assertIn("[BRANCH] Branch commit", stdout)

        # Switch to master and check it wasn't modified
        self._run_cli("checkout", "master")
        result, stdout, stderr = self._run_cli("log")
        self.assertNotIn("[BRANCH]", stdout)

    def test_filter_branch_tree_filter(self):
        """Test filter-branch with tree filter."""
        # Use a tree filter to remove a specific file
        tree_filter = "rm -f root.txt"
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--tree-filter", tree_filter
        )

        self.assertEqual(result, 0)

        # Check that the file was removed from the latest commit
        # We need to check the commit tree, not the working directory
        result, stdout, stderr = self._run_cli("ls-tree", "HEAD")
        self.assertNotIn("root.txt", stdout)

    def test_filter_branch_index_filter(self):
        """Test filter-branch with index filter."""
        # Use an index filter to remove a file from the index
        index_filter = "git rm --cached --ignore-unmatch root.txt"
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--index-filter", index_filter
        )

        self.assertEqual(result, 0)

    def test_filter_branch_parent_filter(self):
        """Test filter-branch with parent filter."""
        # Create a merge commit first
        self._run_cli("checkout", "HEAD", "-b", "feature")
        with open(os.path.join(self.repo_path, "feature.txt"), "w") as f:
            f.write("Feature")
        self._run_cli("add", "feature.txt")
        self._run_cli("commit", "--message=Feature commit")

        self._run_cli("checkout", "master")
        self._run_cli("merge", "feature", "--message=Merge feature")

        # Use parent filter to linearize history (remove second parent)
        parent_filter = "cut -d' ' -f1"
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--parent-filter", parent_filter
        )

        self.assertEqual(result, 0)

    def test_filter_branch_commit_filter(self):
        """Test filter-branch with commit filter."""
        # Use commit filter to skip commits with certain messages
        commit_filter = """
        if grep -q "Modify other" <<< "$GIT_COMMIT_MESSAGE"; then
            skip_commit "$@"
        else
            git commit-tree "$@"
        fi
        """
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--commit-filter", commit_filter
        )

        # Note: This test may fail because the commit filter syntax is simplified
        # In real Git, skip_commit is a function, but our implementation may differ

    def test_filter_branch_tag_name_filter(self):
        """Test filter-branch with tag name filter."""
        # Run filter-branch with tag name filter to rename tags
        result, stdout, stderr = self._run_cli(
            "filter-branch",
            "--tag-name-filter",
            "sed 's/^v/version-/'",
            "--msg-filter",
            "cat",
        )

        self.assertEqual(result, 0)

        # Check that tag was renamed
        self.assertIn(b"refs/tags/version-1.0", self.repo.refs.keys())

    def test_filter_branch_errors(self):
        """Test filter-branch error handling."""
        # Test with invalid subdirectory
        result, stdout, stderr = self._run_cli(
            "filter-branch", "--subdirectory-filter", "nonexistent"
        )
        # Should still succeed but produce empty history
        self.assertEqual(result, 0)

    def test_filter_branch_no_args(self):
        """Test filter-branch with no arguments."""
        # Should work as no-op
        result, stdout, stderr = self._run_cli("filter-branch")
        self.assertEqual(result, 0)


class ShowCommandTest(DulwichCliTestCase):
    """Tests for show command."""

    def test_show_commit(self):
        # Create a commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Test commit")

        result, stdout, stderr = self._run_cli("show", "HEAD")
        self.assertIn("Test commit", stdout)


class FormatPatchCommandTest(DulwichCliTestCase):
    """Tests for format-patch command."""

    def test_format_patch_single_commit(self):
        # Create a commit with actual content
        from dulwich.objects import Blob, Tree

        # Initial commit
        tree1 = Tree()
        self.repo.object_store.add_object(tree1)
        self.repo.do_commit(
            message=b"Initial commit",
            tree=tree1.id,
        )

        # Second commit with a file
        blob = Blob.from_string(b"Hello, World!\n")
        self.repo.object_store.add_object(blob)
        tree2 = Tree()
        tree2.add(b"hello.txt", 0o100644, blob.id)
        self.repo.object_store.add_object(tree2)
        self.repo.do_commit(
            message=b"Add hello.txt",
            tree=tree2.id,
        )

        # Test format-patch for last commit
        result, stdout, stderr = self._run_cli("format-patch", "-n", "1")
        self.assertEqual(result, None)
        self.assertIn("0001-Add-hello.txt.patch", stdout)

        # Check patch contents
        patch_file = os.path.join(self.repo_path, "0001-Add-hello.txt.patch")
        with open(patch_file, "rb") as f:
            content = f.read()
            # Check header
            self.assertIn(b"Subject: [PATCH 1/1] Add hello.txt", content)
            self.assertIn(b"From:", content)
            self.assertIn(b"Date:", content)
            # Check diff content
            self.assertIn(b"diff --git a/hello.txt b/hello.txt", content)
            self.assertIn(b"new file mode", content)
            self.assertIn(b"+Hello, World!", content)
            # Check footer
            self.assertIn(b"-- \nDulwich", content)

        # Clean up
        os.remove(patch_file)

    def test_format_patch_multiple_commits(self):
        from dulwich.objects import Blob, Tree

        # Initial commit
        tree1 = Tree()
        self.repo.object_store.add_object(tree1)
        self.repo.do_commit(
            message=b"Initial commit",
            tree=tree1.id,
        )

        # Second commit
        blob1 = Blob.from_string(b"File 1 content\n")
        self.repo.object_store.add_object(blob1)
        tree2 = Tree()
        tree2.add(b"file1.txt", 0o100644, blob1.id)
        self.repo.object_store.add_object(tree2)
        self.repo.do_commit(
            message=b"Add file1.txt",
            tree=tree2.id,
        )

        # Third commit
        blob2 = Blob.from_string(b"File 2 content\n")
        self.repo.object_store.add_object(blob2)
        tree3 = Tree()
        tree3.add(b"file1.txt", 0o100644, blob1.id)
        tree3.add(b"file2.txt", 0o100644, blob2.id)
        self.repo.object_store.add_object(tree3)
        self.repo.do_commit(
            message=b"Add file2.txt",
            tree=tree3.id,
        )

        # Test format-patch for last 2 commits
        result, stdout, stderr = self._run_cli("format-patch", "-n", "2")
        self.assertEqual(result, None)
        self.assertIn("0001-Add-file1.txt.patch", stdout)
        self.assertIn("0002-Add-file2.txt.patch", stdout)

        # Check first patch
        with open(os.path.join(self.repo_path, "0001-Add-file1.txt.patch"), "rb") as f:
            content = f.read()
            self.assertIn(b"Subject: [PATCH 1/2] Add file1.txt", content)
            self.assertIn(b"+File 1 content", content)

        # Check second patch
        with open(os.path.join(self.repo_path, "0002-Add-file2.txt.patch"), "rb") as f:
            content = f.read()
            self.assertIn(b"Subject: [PATCH 2/2] Add file2.txt", content)
            self.assertIn(b"+File 2 content", content)

        # Clean up
        os.remove(os.path.join(self.repo_path, "0001-Add-file1.txt.patch"))
        os.remove(os.path.join(self.repo_path, "0002-Add-file2.txt.patch"))

    def test_format_patch_output_directory(self):
        from dulwich.objects import Blob, Tree

        # Create a commit
        blob = Blob.from_string(b"Test content\n")
        self.repo.object_store.add_object(blob)
        tree = Tree()
        tree.add(b"test.txt", 0o100644, blob.id)
        self.repo.object_store.add_object(tree)
        self.repo.do_commit(
            message=b"Test commit",
            tree=tree.id,
        )

        # Create output directory
        output_dir = os.path.join(self.test_dir, "patches")
        os.makedirs(output_dir)

        # Test format-patch with output directory
        result, stdout, stderr = self._run_cli(
            "format-patch", "-o", output_dir, "-n", "1"
        )
        self.assertEqual(result, None)

        # Check that file was created in output directory with correct content
        patch_file = os.path.join(output_dir, "0001-Test-commit.patch")
        self.assertTrue(os.path.exists(patch_file))
        with open(patch_file, "rb") as f:
            content = f.read()
            self.assertIn(b"Subject: [PATCH 1/1] Test commit", content)
            self.assertIn(b"+Test content", content)

    def test_format_patch_commit_range(self):
        from dulwich.objects import Blob, Tree

        # Create commits with actual file changes
        commits = []
        trees = []

        # Initial empty commit
        tree0 = Tree()
        self.repo.object_store.add_object(tree0)
        trees.append(tree0)
        c0 = self.repo.do_commit(
            message=b"Initial commit",
            tree=tree0.id,
        )
        commits.append(c0)

        # Add three files in separate commits
        for i in range(1, 4):
            blob = Blob.from_string(f"Content {i}\n".encode())
            self.repo.object_store.add_object(blob)
            tree = Tree()
            # Copy previous files
            for j in range(1, i):
                prev_blob_id = trees[j][f"file{j}.txt".encode()][1]
                tree.add(f"file{j}.txt".encode(), 0o100644, prev_blob_id)
            # Add new file
            tree.add(f"file{i}.txt".encode(), 0o100644, blob.id)
            self.repo.object_store.add_object(tree)
            trees.append(tree)

            c = self.repo.do_commit(
                message=f"Add file{i}.txt".encode(),
                tree=tree.id,
            )
            commits.append(c)

        # Test format-patch with commit range (should get commits 2 and 3)
        result, stdout, stderr = self._run_cli(
            "format-patch", f"{commits[1].decode()}..{commits[3].decode()}"
        )
        self.assertEqual(result, None)

        # Should create patches for commits 2 and 3
        self.assertIn("0001-Add-file2.txt.patch", stdout)
        self.assertIn("0002-Add-file3.txt.patch", stdout)

        # Verify patch contents
        with open(os.path.join(self.repo_path, "0001-Add-file2.txt.patch"), "rb") as f:
            content = f.read()
            self.assertIn(b"Subject: [PATCH 1/2] Add file2.txt", content)
            self.assertIn(b"+Content 2", content)
            self.assertNotIn(b"file3.txt", content)  # Should not include file3

        with open(os.path.join(self.repo_path, "0002-Add-file3.txt.patch"), "rb") as f:
            content = f.read()
            self.assertIn(b"Subject: [PATCH 2/2] Add file3.txt", content)
            self.assertIn(b"+Content 3", content)
            self.assertNotIn(b"file2.txt", content)  # Should not modify file2

        # Clean up
        os.remove(os.path.join(self.repo_path, "0001-Add-file2.txt.patch"))
        os.remove(os.path.join(self.repo_path, "0002-Add-file3.txt.patch"))

    def test_format_patch_stdout(self):
        from dulwich.objects import Blob, Tree

        # Create a commit with modified file
        tree1 = Tree()
        blob1 = Blob.from_string(b"Original content\n")
        self.repo.object_store.add_object(blob1)
        tree1.add(b"file.txt", 0o100644, blob1.id)
        self.repo.object_store.add_object(tree1)
        self.repo.do_commit(
            message=b"Initial commit",
            tree=tree1.id,
        )

        tree2 = Tree()
        blob2 = Blob.from_string(b"Modified content\n")
        self.repo.object_store.add_object(blob2)
        tree2.add(b"file.txt", 0o100644, blob2.id)
        self.repo.object_store.add_object(tree2)
        self.repo.do_commit(
            message=b"Modify file.txt",
            tree=tree2.id,
        )

        # Mock stdout as a BytesIO for binary output
        stdout_stream = io.BytesIO()
        stdout_stream.buffer = stdout_stream

        # Run command with --stdout
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_cwd = os.getcwd()
        try:
            sys.stdout = stdout_stream
            sys.stderr = io.StringIO()
            os.chdir(self.repo_path)
            cli.main(["format-patch", "--stdout", "-n", "1"])
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            os.chdir(old_cwd)

        # Check output
        stdout_stream.seek(0)
        output = stdout_stream.read()
        self.assertIn(b"Subject: [PATCH 1/1] Modify file.txt", output)
        self.assertIn(b"diff --git a/file.txt b/file.txt", output)
        self.assertIn(b"-Original content", output)
        self.assertIn(b"+Modified content", output)
        self.assertIn(b"-- \nDulwich", output)

    def test_format_patch_empty_repo(self):
        # Test with empty repository
        result, stdout, stderr = self._run_cli("format-patch", "-n", "5")
        self.assertEqual(result, None)
        # Should produce no output for empty repo
        self.assertEqual(stdout.strip(), "")


class FetchPackCommandTest(DulwichCliTestCase):
    """Tests for fetch-pack command."""

    @patch("dulwich.cli.get_transport_and_path")
    def test_fetch_pack_basic(self, mock_transport):
        # Mock the transport
        mock_client = MagicMock()
        mock_transport.return_value = (mock_client, "/path/to/repo")
        mock_client.fetch.return_value = None

        result, stdout, stderr = self._run_cli(
            "fetch-pack", "git://example.com/repo.git"
        )
        mock_client.fetch.assert_called_once()


class LsRemoteCommandTest(DulwichCliTestCase):
    """Tests for ls-remote command."""

    def test_ls_remote_basic(self):
        # Create a commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Test basic ls-remote
        result, stdout, stderr = self._run_cli("ls-remote", self.repo_path)
        lines = stdout.strip().split("\n")
        self.assertTrue(any("HEAD" in line for line in lines))
        self.assertTrue(any("refs/heads/master" in line for line in lines))

    def test_ls_remote_symref(self):
        # Create a commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Test ls-remote with --symref option
        result, stdout, stderr = self._run_cli("ls-remote", "--symref", self.repo_path)
        lines = stdout.strip().split("\n")
        # Should show symref for HEAD in exact format: "ref: refs/heads/master\tHEAD"
        expected_line = "ref: refs/heads/master\tHEAD"
        self.assertIn(
            expected_line,
            lines,
            f"Expected line '{expected_line}' not found in output: {lines}",
        )


class PullCommandTest(DulwichCliTestCase):
    """Tests for pull command."""

    @patch("dulwich.porcelain.pull")
    def test_pull_basic(self, mock_pull):
        result, stdout, stderr = self._run_cli("pull", "origin")
        mock_pull.assert_called_once()

    @patch("dulwich.porcelain.pull")
    def test_pull_with_refspec(self, mock_pull):
        result, stdout, stderr = self._run_cli("pull", "origin", "master")
        mock_pull.assert_called_once()


class PushCommandTest(DulwichCliTestCase):
    """Tests for push command."""

    @patch("dulwich.porcelain.push")
    def test_push_basic(self, mock_push):
        result, stdout, stderr = self._run_cli("push", "origin")
        mock_push.assert_called_once()

    @patch("dulwich.porcelain.push")
    def test_push_force(self, mock_push):
        result, stdout, stderr = self._run_cli("push", "-f", "origin")
        mock_push.assert_called_with(".", "origin", None, force=True)


class ArchiveCommandTest(DulwichCliTestCase):
    """Tests for archive command."""

    def test_archive_basic(self):
        # Create a commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Archive produces binary output, so use BytesIO
        result, stdout, stderr = self._run_cli(
            "archive", "HEAD", stdout_stream=io.BytesIO()
        )
        # Should complete without error and produce some binary output
        self.assertIsInstance(stdout, bytes)
        self.assertGreater(len(stdout), 0)


class ForEachRefCommandTest(DulwichCliTestCase):
    """Tests for for-each-ref command."""

    def test_for_each_ref(self):
        # Create a commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        result, stdout, stderr = self._run_cli("for-each-ref")
        self.assertIn("refs/heads/master", stdout)


class PackRefsCommandTest(DulwichCliTestCase):
    """Tests for pack-refs command."""

    def test_pack_refs(self):
        # Create some refs
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")
        self._run_cli("branch", "test-branch")

        result, stdout, stderr = self._run_cli("pack-refs", "--all")
        # Check that packed-refs file exists
        self.assertTrue(
            os.path.exists(os.path.join(self.repo_path, ".git", "packed-refs"))
        )


class SubmoduleCommandTest(DulwichCliTestCase):
    """Tests for submodule commands."""

    def test_submodule_list(self):
        # Create an initial commit so repo has a HEAD
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        result, stdout, stderr = self._run_cli("submodule")
        # Should not crash on repo without submodules

    def test_submodule_init(self):
        # Create .gitmodules file for init to work
        gitmodules = os.path.join(self.repo_path, ".gitmodules")
        with open(gitmodules, "w") as f:
            f.write("")  # Empty .gitmodules file

        result, stdout, stderr = self._run_cli("submodule", "init")
        # Should not crash


class StashCommandTest(DulwichCliTestCase):
    """Tests for stash commands."""

    def test_stash_list_empty(self):
        result, stdout, stderr = self._run_cli("stash", "list")
        # Should not crash on empty stash

    def test_stash_push_pop(self):
        # Create a file and modify it
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("initial")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Modify file
        with open(test_file, "w") as f:
            f.write("modified")

        # Stash changes
        result, stdout, stderr = self._run_cli("stash", "push")
        self.assertIn("Saved working directory", stdout)

        # Note: Dulwich stash doesn't currently update the working tree
        # so the file remains modified after stash push

        # Note: stash pop is not fully implemented in Dulwich yet
        # so we only test stash push here


class MergeCommandTest(DulwichCliTestCase):
    """Tests for merge command."""

    def test_merge_basic(self):
        # Create initial commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("initial")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        # Create and checkout new branch
        self._run_cli("branch", "feature")
        self._run_cli("checkout", "feature")

        # Make changes in feature branch
        with open(test_file, "w") as f:
            f.write("feature changes")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Feature commit")

        # Go back to main
        self._run_cli("checkout", "master")

        # Merge feature branch
        result, stdout, stderr = self._run_cli("merge", "feature")


class HelpCommandTest(DulwichCliTestCase):
    """Tests for help command."""

    def test_help_basic(self):
        result, stdout, stderr = self._run_cli("help")
        self.assertIn("dulwich command line tool", stdout)

    def test_help_all(self):
        result, stdout, stderr = self._run_cli("help", "-a")
        self.assertIn("Available commands:", stdout)
        self.assertIn("add", stdout)
        self.assertIn("commit", stdout)


class RemoteCommandTest(DulwichCliTestCase):
    """Tests for remote commands."""

    def test_remote_add(self):
        result, stdout, stderr = self._run_cli(
            "remote", "add", "origin", "https://github.com/example/repo.git"
        )
        # Check remote was added to config
        config = self.repo.get_config()
        self.assertEqual(
            config.get((b"remote", b"origin"), b"url"),
            b"https://github.com/example/repo.git",
        )


class CheckIgnoreCommandTest(DulwichCliTestCase):
    """Tests for check-ignore command."""

    def test_check_ignore(self):
        # Create .gitignore
        gitignore = os.path.join(self.repo_path, ".gitignore")
        with open(gitignore, "w") as f:
            f.write("*.log\n")

        result, stdout, stderr = self._run_cli("check-ignore", "test.log", "test.txt")
        self.assertIn("test.log", stdout)
        self.assertNotIn("test.txt", stdout)


class LsFilesCommandTest(DulwichCliTestCase):
    """Tests for ls-files command."""

    def test_ls_files(self):
        # Add some files
        for name in ["a.txt", "b.txt", "c.txt"]:
            path = os.path.join(self.repo_path, name)
            with open(path, "w") as f:
                f.write(f"content of {name}")
        self._run_cli("add", "a.txt", "b.txt", "c.txt")

        result, stdout, stderr = self._run_cli("ls-files")
        self.assertIn("a.txt", stdout)
        self.assertIn("b.txt", stdout)
        self.assertIn("c.txt", stdout)


class LsTreeCommandTest(DulwichCliTestCase):
    """Tests for ls-tree command."""

    def test_ls_tree(self):
        # Create a directory structure
        os.mkdir(os.path.join(self.repo_path, "subdir"))
        with open(os.path.join(self.repo_path, "file.txt"), "w") as f:
            f.write("file content")
        with open(os.path.join(self.repo_path, "subdir", "nested.txt"), "w") as f:
            f.write("nested content")

        self._run_cli("add", ".")
        self._run_cli("commit", "--message=Initial")

        result, stdout, stderr = self._run_cli("ls-tree", "HEAD")
        self.assertIn("file.txt", stdout)
        self.assertIn("subdir", stdout)

    def test_ls_tree_recursive(self):
        # Create nested structure
        os.mkdir(os.path.join(self.repo_path, "subdir"))
        with open(os.path.join(self.repo_path, "subdir", "nested.txt"), "w") as f:
            f.write("nested")

        self._run_cli("add", ".")
        self._run_cli("commit", "--message=Initial")

        result, stdout, stderr = self._run_cli("ls-tree", "-r", "HEAD")
        self.assertIn("subdir/nested.txt", stdout)


class DescribeCommandTest(DulwichCliTestCase):
    """Tests for describe command."""

    def test_describe(self):
        # Create tagged commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")
        self._run_cli("tag", "v1.0")

        result, stdout, stderr = self._run_cli("describe")
        self.assertIn("v1.0", stdout)


class FsckCommandTest(DulwichCliTestCase):
    """Tests for fsck command."""

    def test_fsck(self):
        # Create a commit
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")

        result, stdout, stderr = self._run_cli("fsck")
        # Should complete without errors


class RepackCommandTest(DulwichCliTestCase):
    """Tests for repack command."""

    def test_repack(self):
        # Create some objects
        for i in range(5):
            test_file = os.path.join(self.repo_path, f"test{i}.txt")
            with open(test_file, "w") as f:
                f.write(f"content {i}")
            self._run_cli("add", f"test{i}.txt")
            self._run_cli("commit", f"--message=Commit {i}")

        result, stdout, stderr = self._run_cli("repack")
        # Should create pack files
        pack_dir = os.path.join(self.repo_path, ".git", "objects", "pack")
        self.assertTrue(any(f.endswith(".pack") for f in os.listdir(pack_dir)))


class ResetCommandTest(DulwichCliTestCase):
    """Tests for reset command."""

    def test_reset_soft(self):
        # Create commits
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("first")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=First")
        first_commit = self.repo.head()

        with open(test_file, "w") as f:
            f.write("second")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Second")

        # Reset soft
        result, stdout, stderr = self._run_cli("reset", "--soft", first_commit.decode())
        # HEAD should be at first commit
        self.assertEqual(self.repo.head(), first_commit)


class WriteTreeCommandTest(DulwichCliTestCase):
    """Tests for write-tree command."""

    def test_write_tree(self):
        # Create and add files
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")

        result, stdout, stderr = self._run_cli("write-tree")
        # Should output tree SHA
        self.assertEqual(len(stdout.strip()), 40)


class UpdateServerInfoCommandTest(DulwichCliTestCase):
    """Tests for update-server-info command."""

    def test_update_server_info(self):
        result, stdout, stderr = self._run_cli("update-server-info")
        # Should create info/refs file
        info_refs = os.path.join(self.repo_path, ".git", "info", "refs")
        self.assertTrue(os.path.exists(info_refs))


class SymbolicRefCommandTest(DulwichCliTestCase):
    """Tests for symbolic-ref command."""

    def test_symbolic_ref(self):
        # Create a branch
        test_file = os.path.join(self.repo_path, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")
        self._run_cli("add", "test.txt")
        self._run_cli("commit", "--message=Initial")
        self._run_cli("branch", "test-branch")

        result, stdout, stderr = self._run_cli(
            "symbolic-ref", "HEAD", "refs/heads/test-branch"
        )
        # HEAD should now point to test-branch
        self.assertEqual(
            self.repo.refs.read_ref(b"HEAD"), b"ref: refs/heads/test-branch"
        )


class FormatBytesTestCase(TestCase):
    """Tests for format_bytes function."""

    def test_bytes(self):
        """Test formatting bytes."""
        self.assertEqual("0.0 B", format_bytes(0))
        self.assertEqual("1.0 B", format_bytes(1))
        self.assertEqual("512.0 B", format_bytes(512))
        self.assertEqual("1023.0 B", format_bytes(1023))

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        self.assertEqual("1.0 KB", format_bytes(1024))
        self.assertEqual("1.5 KB", format_bytes(1536))
        self.assertEqual("2.0 KB", format_bytes(2048))
        self.assertEqual("1023.0 KB", format_bytes(1024 * 1023))

    def test_megabytes(self):
        """Test formatting megabytes."""
        self.assertEqual("1.0 MB", format_bytes(1024 * 1024))
        self.assertEqual("1.5 MB", format_bytes(1024 * 1024 * 1.5))
        self.assertEqual("10.0 MB", format_bytes(1024 * 1024 * 10))
        self.assertEqual("1023.0 MB", format_bytes(1024 * 1024 * 1023))

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        self.assertEqual("1.0 GB", format_bytes(1024 * 1024 * 1024))
        self.assertEqual("2.5 GB", format_bytes(1024 * 1024 * 1024 * 2.5))
        self.assertEqual("1023.0 GB", format_bytes(1024 * 1024 * 1024 * 1023))

    def test_terabytes(self):
        """Test formatting terabytes."""
        self.assertEqual("1.0 TB", format_bytes(1024 * 1024 * 1024 * 1024))
        self.assertEqual("5.0 TB", format_bytes(1024 * 1024 * 1024 * 1024 * 5))
        self.assertEqual("1000.0 TB", format_bytes(1024 * 1024 * 1024 * 1024 * 1000))


class ParseRelativeTimeTestCase(TestCase):
    """Tests for parse_relative_time function."""

    def test_now(self):
        """Test parsing 'now'."""
        self.assertEqual(0, parse_relative_time("now"))

    def test_seconds(self):
        """Test parsing seconds."""
        self.assertEqual(1, parse_relative_time("1 second ago"))
        self.assertEqual(5, parse_relative_time("5 seconds ago"))
        self.assertEqual(30, parse_relative_time("30 seconds ago"))

    def test_minutes(self):
        """Test parsing minutes."""
        self.assertEqual(60, parse_relative_time("1 minute ago"))
        self.assertEqual(300, parse_relative_time("5 minutes ago"))
        self.assertEqual(1800, parse_relative_time("30 minutes ago"))

    def test_hours(self):
        """Test parsing hours."""
        self.assertEqual(3600, parse_relative_time("1 hour ago"))
        self.assertEqual(7200, parse_relative_time("2 hours ago"))
        self.assertEqual(86400, parse_relative_time("24 hours ago"))

    def test_days(self):
        """Test parsing days."""
        self.assertEqual(86400, parse_relative_time("1 day ago"))
        self.assertEqual(604800, parse_relative_time("7 days ago"))
        self.assertEqual(2592000, parse_relative_time("30 days ago"))

    def test_weeks(self):
        """Test parsing weeks."""
        self.assertEqual(604800, parse_relative_time("1 week ago"))
        self.assertEqual(1209600, parse_relative_time("2 weeks ago"))
        self.assertEqual(
            36288000, parse_relative_time("60 weeks ago")
        )  # 60 * 7 * 24 * 60 * 60

    def test_invalid_format(self):
        """Test invalid time formats."""
        with self.assertRaises(ValueError) as cm:
            parse_relative_time("invalid")
        self.assertIn("Invalid relative time format", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            parse_relative_time("2 weeks")
        self.assertIn("Invalid relative time format", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            parse_relative_time("ago")
        self.assertIn("Invalid relative time format", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            parse_relative_time("two weeks ago")
        self.assertIn("Invalid number in relative time", str(cm.exception))

    def test_invalid_unit(self):
        """Test invalid time units."""
        with self.assertRaises(ValueError) as cm:
            parse_relative_time("5 months ago")
        self.assertIn("Unknown time unit: months", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            parse_relative_time("2 years ago")
        self.assertIn("Unknown time unit: years", str(cm.exception))

    def test_singular_plural(self):
        """Test that both singular and plural forms work."""
        self.assertEqual(
            parse_relative_time("1 second ago"), parse_relative_time("1 seconds ago")
        )
        self.assertEqual(
            parse_relative_time("1 minute ago"), parse_relative_time("1 minutes ago")
        )
        self.assertEqual(
            parse_relative_time("1 hour ago"), parse_relative_time("1 hours ago")
        )
        self.assertEqual(
            parse_relative_time("1 day ago"), parse_relative_time("1 days ago")
        )
        self.assertEqual(
            parse_relative_time("1 week ago"), parse_relative_time("1 weeks ago")
        )


if __name__ == "__main__":
    unittest.main()
