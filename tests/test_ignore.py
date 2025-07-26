# test_ignore.py -- Tests for ignore files.
# Copyright (C) 2017 Jelmer Vernooij <jelmer@jelmer.uk>
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

"""Tests for ignore files."""

import os
import re
import shutil
import tempfile
from io import BytesIO

from dulwich.ignore import (
    IgnoreFilter,
    IgnoreFilterManager,
    IgnoreFilterStack,
    Pattern,
    match_pattern,
    read_ignore_patterns,
    translate,
)
from dulwich.porcelain import _quote_path
from dulwich.repo import Repo

from . import TestCase

POSITIVE_MATCH_TESTS = [
    (b"foo.c", b"*.c"),
    (b".c", b"*.c"),
    (b"foo/foo.c", b"*.c"),
    (b"foo/foo.c", b"foo.c"),
    (b"foo.c", b"/*.c"),
    (b"foo.c", b"/foo.c"),
    (b"foo.c", b"foo.c"),
    (b"foo.c", b"foo.[ch]"),
    (b"foo/bar/bla.c", b"foo/**"),
    (b"foo/bar/bla/blie.c", b"foo/**/blie.c"),
    (b"foo/bar/bla.c", b"**/bla.c"),
    (b"bla.c", b"**/bla.c"),
    (b"foo/bar", b"foo/**/bar"),
    (b"foo/bla/bar", b"foo/**/bar"),
    (b"foo/bar/", b"bar/"),
    (b"foo/bar/", b"bar"),
    (b"foo/bar/something", b"foo/bar/*"),
]

NEGATIVE_MATCH_TESTS = [
    (b"foo.c", b"foo.[dh]"),
    (b"foo/foo.c", b"/foo.c"),
    (b"foo/foo.c", b"/*.c"),
    (b"foo/bar/", b"/bar/"),
    (b"foo/bar/", b"foo/bar/*"),
    (b"foo/bar", b"foo?bar"),
]


TRANSLATE_TESTS = [
    (b"*.c", b"(?ms)(.*/)?[^/]*\\.c/?\\Z"),
    (b"foo.c", b"(?ms)(.*/)?foo\\.c/?\\Z"),
    (b"/*.c", b"(?ms)[^/]*\\.c/?\\Z"),
    (b"/foo.c", b"(?ms)foo\\.c/?\\Z"),
    (b"foo.c", b"(?ms)(.*/)?foo\\.c/?\\Z"),
    (b"foo.[ch]", b"(?ms)(.*/)?foo\\.[ch]/?\\Z"),
    (b"bar/", b"(?ms)(.*/)?bar\\/\\Z"),
    (b"foo/**", b"(?ms)foo/.*/?\\Z"),
    (b"foo/**/blie.c", b"(?ms)foo/(?:[^/]+/)*blie\\.c/?\\Z"),
    (b"**/bla.c", b"(?ms)(.*/)?bla\\.c/?\\Z"),
    (b"foo/**/bar", b"(?ms)foo/(?:[^/]+/)*bar/?\\Z"),
    (b"foo/bar/*", b"(?ms)foo\\/bar\\/[^/]+/?\\Z"),
    (b"/foo\\[bar\\]", b"(?ms)foo\\[bar\\]/?\\Z"),
    (b"/foo[bar]", b"(?ms)foo[bar]/?\\Z"),
    (b"/foo[0-9]", b"(?ms)foo[0-9]/?\\Z"),
]


class TranslateTests(TestCase):
    def test_translate(self) -> None:
        for pattern, regex in TRANSLATE_TESTS:
            if re.escape(b"/") == b"/":
                # Slash is no longer escaped in Python3.7, so undo the escaping
                # in the expected return value..
                regex = regex.replace(b"\\/", b"/")
            self.assertEqual(
                regex,
                translate(pattern),
                f"orig pattern: {pattern!r}, regex: {translate(pattern)!r}, expected: {regex!r}",
            )


class ReadIgnorePatterns(TestCase):
    def test_read_file(self) -> None:
        f = BytesIO(
            b"""
# a comment
\x20\x20
# and an empty line:

\\#not a comment
!negative
with trailing whitespace 
with escaped trailing whitespace\\ 
"""
        )
        self.assertEqual(
            list(read_ignore_patterns(f)),
            [
                b"\\#not a comment",
                b"!negative",
                b"with trailing whitespace",
                b"with escaped trailing whitespace ",
            ],
        )


class MatchPatternTests(TestCase):
    def test_matches(self) -> None:
        for path, pattern in POSITIVE_MATCH_TESTS:
            self.assertTrue(
                match_pattern(path, pattern),
                f"path: {path!r}, pattern: {pattern!r}",
            )

    def test_no_matches(self) -> None:
        for path, pattern in NEGATIVE_MATCH_TESTS:
            self.assertFalse(
                match_pattern(path, pattern),
                f"path: {path!r}, pattern: {pattern!r}",
            )


class ParentExclusionTests(TestCase):
    """Tests for parent directory exclusion helper functions."""

    def test_check_parent_exclusion_direct_directory(self) -> None:
        """Test _check_parent_exclusion with direct directory exclusion."""
        from dulwich.ignore import Pattern, _check_parent_exclusion

        # Pattern: dir/, !dir/file.txt
        patterns = [Pattern(b"dir/"), Pattern(b"!dir/file.txt")]

        # dir/file.txt has parent 'dir' excluded
        self.assertTrue(_check_parent_exclusion("dir/file.txt", patterns))

        # dir/subdir/file.txt also has parent 'dir' excluded
        self.assertTrue(_check_parent_exclusion("dir/subdir/file.txt", patterns))

        # other/file.txt has no parent excluded
        self.assertFalse(_check_parent_exclusion("other/file.txt", patterns))

    def test_check_parent_exclusion_no_negation(self) -> None:
        """Test _check_parent_exclusion when there's no negation pattern."""
        from dulwich.ignore import Pattern, _check_parent_exclusion

        # Only exclusion patterns
        patterns = [Pattern(b"*.log"), Pattern(b"build/")]

        # No negation pattern, so no parent exclusion check needed
        self.assertFalse(_check_parent_exclusion("build/file.txt", patterns))

    def test_pattern_excludes_parent_directory_slash(self) -> None:
        """Test _pattern_excludes_parent for patterns ending with /."""
        from dulwich.ignore import _pattern_excludes_parent

        # Pattern: parent/
        self.assertTrue(
            _pattern_excludes_parent("parent/", "parent/file.txt", "!parent/file.txt")
        )
        self.assertTrue(
            _pattern_excludes_parent(
                "parent/", "parent/sub/file.txt", "!parent/sub/file.txt"
            )
        )
        self.assertFalse(
            _pattern_excludes_parent("parent/", "other/file.txt", "!other/file.txt")
        )
        self.assertFalse(
            _pattern_excludes_parent("parent/", "parent", "!parent")
        )  # No / in path

    def test_pattern_excludes_parent_double_asterisk(self) -> None:
        """Test _pattern_excludes_parent for **/ patterns."""
        from dulwich.ignore import _pattern_excludes_parent

        # Pattern: **/node_modules/**
        self.assertTrue(
            _pattern_excludes_parent(
                "**/node_modules/**",
                "foo/node_modules/bar/file.txt",
                "!foo/node_modules/bar/file.txt",
            )
        )
        self.assertTrue(
            _pattern_excludes_parent(
                "**/node_modules/**", "node_modules/file.txt", "!node_modules/file.txt"
            )
        )
        self.assertFalse(
            _pattern_excludes_parent(
                "**/node_modules/**", "foo/bar/file.txt", "!foo/bar/file.txt"
            )
        )

    def test_pattern_excludes_parent_glob(self) -> None:
        """Test _pattern_excludes_parent for dir/** patterns."""
        from dulwich.ignore import _pattern_excludes_parent

        # Pattern: logs/** - allows exact file negations for immediate children
        self.assertFalse(
            _pattern_excludes_parent("logs/**", "logs/file.txt", "!logs/file.txt")
        )

        # Directory negations still have parent exclusion
        self.assertTrue(
            _pattern_excludes_parent("logs/**", "logs/keep/", "!logs/keep/")
        )

        # Non-exact negations have parent exclusion
        self.assertTrue(
            _pattern_excludes_parent("logs/**", "logs/keep/", "!logs/keep/file.txt")
        )

        # Nested paths have parent exclusion
        self.assertTrue(
            _pattern_excludes_parent("logs/**", "logs/sub/file.txt", "!logs/sub/")
        )
        self.assertTrue(
            _pattern_excludes_parent(
                "logs/**", "logs/sub/file.txt", "!logs/sub/file.txt"
            )
        )


class IgnoreFilterTests(TestCase):
    def test_included(self) -> None:
        filter = IgnoreFilter([b"a.c", b"b.c"])
        self.assertTrue(filter.is_ignored(b"a.c"))
        self.assertIs(None, filter.is_ignored(b"c.c"))
        self.assertEqual([Pattern(b"a.c")], list(filter.find_matching(b"a.c")))
        self.assertEqual([], list(filter.find_matching(b"c.c")))

    def test_included_ignorecase(self) -> None:
        filter = IgnoreFilter([b"a.c", b"b.c"], ignorecase=False)
        self.assertTrue(filter.is_ignored(b"a.c"))
        self.assertFalse(filter.is_ignored(b"A.c"))
        filter = IgnoreFilter([b"a.c", b"b.c"], ignorecase=True)
        self.assertTrue(filter.is_ignored(b"a.c"))
        self.assertTrue(filter.is_ignored(b"A.c"))
        self.assertTrue(filter.is_ignored(b"A.C"))

    def test_excluded(self) -> None:
        filter = IgnoreFilter([b"a.c", b"b.c", b"!c.c"])
        self.assertFalse(filter.is_ignored(b"c.c"))
        self.assertIs(None, filter.is_ignored(b"d.c"))
        self.assertEqual([Pattern(b"!c.c")], list(filter.find_matching(b"c.c")))
        self.assertEqual([], list(filter.find_matching(b"d.c")))

    def test_include_exclude_include(self) -> None:
        filter = IgnoreFilter([b"a.c", b"!a.c", b"a.c"])
        self.assertTrue(filter.is_ignored(b"a.c"))
        self.assertEqual(
            [Pattern(b"a.c"), Pattern(b"!a.c"), Pattern(b"a.c")],
            list(filter.find_matching(b"a.c")),
        )

    def test_manpage(self) -> None:
        # A specific example from the gitignore manpage
        filter = IgnoreFilter([b"/*", b"!/foo", b"/foo/*", b"!/foo/bar"])
        self.assertTrue(filter.is_ignored(b"a.c"))
        self.assertTrue(filter.is_ignored(b"foo/blie"))
        self.assertFalse(filter.is_ignored(b"foo"))
        self.assertFalse(filter.is_ignored(b"foo/bar"))
        self.assertFalse(filter.is_ignored(b"foo/bar/"))
        self.assertFalse(filter.is_ignored(b"foo/bar/bloe"))

    def test_regex_special(self) -> None:
        # See https://github.com/dulwich/dulwich/issues/930#issuecomment-1026166429
        filter = IgnoreFilter([b"/foo\\[bar\\]", b"/foo"])
        self.assertTrue(filter.is_ignored("foo"))
        self.assertTrue(filter.is_ignored("foo[bar]"))

    def test_from_path_pathlib(self) -> None:
        import tempfile
        from pathlib import Path

        # Create a temporary .gitignore file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".gitignore", delete=False
        ) as f:
            f.write("*.pyc\n__pycache__/\n")
            temp_path = f.name

        self.addCleanup(os.unlink, temp_path)

        # Test with pathlib.Path
        path_obj = Path(temp_path)
        ignore_filter = IgnoreFilter.from_path(path_obj)

        # Test that it loaded the patterns correctly
        self.assertTrue(ignore_filter.is_ignored("test.pyc"))
        self.assertTrue(ignore_filter.is_ignored("__pycache__/"))
        self.assertFalse(ignore_filter.is_ignored("test.py"))


class IgnoreFilterStackTests(TestCase):
    def test_stack_first(self) -> None:
        filter1 = IgnoreFilter([b"[a].c", b"[b].c", b"![d].c"])
        filter2 = IgnoreFilter([b"[a].c", b"![b],c", b"[c].c", b"[d].c"])
        stack = IgnoreFilterStack([filter1, filter2])
        self.assertIs(True, stack.is_ignored(b"a.c"))
        self.assertIs(True, stack.is_ignored(b"b.c"))
        self.assertIs(True, stack.is_ignored(b"c.c"))
        self.assertIs(False, stack.is_ignored(b"d.c"))
        self.assertIs(None, stack.is_ignored(b"e.c"))


class IgnoreFilterManagerTests(TestCase):
    def test_load_ignore(self) -> None:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"/foo/bar\n")
            f.write(b"/dir2\n")
            f.write(b"/dir3/\n")
        os.mkdir(os.path.join(repo.path, "dir"))
        with open(os.path.join(repo.path, "dir", ".gitignore"), "wb") as f:
            f.write(b"/blie\n")
        with open(os.path.join(repo.path, "dir", "blie"), "wb") as f:
            f.write(b"IGNORED")
        p = os.path.join(repo.controldir(), "info", "exclude")
        with open(p, "wb") as f:
            f.write(b"/excluded\n")
        m = IgnoreFilterManager.from_repo(repo)
        self.assertTrue(m.is_ignored("dir/blie"))
        self.assertIs(None, m.is_ignored(os.path.join("dir", "bloe")))
        self.assertIs(None, m.is_ignored("dir"))
        self.assertTrue(m.is_ignored(os.path.join("foo", "bar")))
        self.assertTrue(m.is_ignored(os.path.join("excluded")))
        self.assertTrue(m.is_ignored(os.path.join("dir2", "fileinignoreddir")))
        self.assertFalse(m.is_ignored("dir3"))
        self.assertTrue(m.is_ignored("dir3/"))
        self.assertTrue(m.is_ignored("dir3/bla"))

    def test_nested_gitignores(self) -> None:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"/*\n")
            f.write(b"!/foo\n")

        os.mkdir(os.path.join(repo.path, "foo"))
        with open(os.path.join(repo.path, "foo", ".gitignore"), "wb") as f:
            f.write(b"/bar\n")

        with open(os.path.join(repo.path, "foo", "bar"), "wb") as f:
            f.write(b"IGNORED")

        m = IgnoreFilterManager.from_repo(repo)
        self.assertTrue(m.is_ignored("foo/bar"))

    def test_load_ignore_ignorecase(self) -> None:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)
        config = repo.get_config()
        config.set(b"core", b"ignorecase", True)
        config.write_to_path()
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"/foo/bar\n")
            f.write(b"/dir\n")
        m = IgnoreFilterManager.from_repo(repo)
        self.assertTrue(m.is_ignored(os.path.join("dir", "blie")))
        self.assertTrue(m.is_ignored(os.path.join("DIR", "blie")))

    def test_ignored_contents(self) -> None:
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"a/*\n")
            f.write(b"!a/*.txt\n")
        m = IgnoreFilterManager.from_repo(repo)
        os.mkdir(os.path.join(repo.path, "a"))
        self.assertIs(None, m.is_ignored("a"))
        self.assertIs(None, m.is_ignored("a/"))
        self.assertFalse(m.is_ignored("a/b.txt"))
        self.assertTrue(m.is_ignored("a/c.dat"))

    def test_issue_1203_directory_negation(self) -> None:
        """Test for issue #1203: gitignore patterns with directory negation."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Create .gitignore with the patterns from the issue
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"data/**\n")
            f.write(b"!data/*/\n")

        # Create directory structure
        os.makedirs(os.path.join(repo.path, "data", "subdir"))

        m = IgnoreFilterManager.from_repo(repo)

        # Test the expected behavior
        self.assertTrue(
            m.is_ignored("data/test.dvc")
        )  # File in data/ should be ignored
        self.assertFalse(m.is_ignored("data/"))  # data/ directory should not be ignored
        self.assertTrue(
            m.is_ignored("data/subdir/")
        )  # Subdirectory should be ignored (matches Git behavior)

    def test_issue_1721_directory_negation_with_double_asterisk(self) -> None:
        """Test for issue #1721: regression with negated subdirectory patterns using **."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Create .gitignore with the patterns from issue #1721
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"data/**\n")
            f.write(b"!data/**/\n")
            f.write(b"!data/**/*.csv\n")

        # Create directory structure
        os.makedirs(os.path.join(repo.path, "data", "subdir"))

        m = IgnoreFilterManager.from_repo(repo)

        # Test the expected behavior - issue #1721 was that data/myfile was not ignored
        self.assertTrue(
            m.is_ignored("data/myfile")
        )  # File should be ignored (fixes issue #1721)
        self.assertFalse(m.is_ignored("data/"))  # data/ is matched by !data/**/
        self.assertFalse(
            m.is_ignored("data/subdir/")
        )  # Subdirectory is matched by !data/**/
        # With data/** pattern, Git allows CSV files to be re-included via !data/**/*.csv
        self.assertFalse(m.is_ignored("data/test.csv"))  # CSV files are not ignored
        self.assertFalse(
            m.is_ignored("data/subdir/test.csv")
        )  # CSV files in subdirs are not ignored
        self.assertTrue(
            m.is_ignored("data/subdir/other.txt")
        )  # Non-CSV files in subdirs are ignored

    def test_parent_directory_exclusion(self) -> None:
        """Test Git's parent directory exclusion rule.

        Git rule: "It is not possible to re-include a file if a parent directory of that file is excluded."
        """
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Test case 1: Direct parent directory exclusion
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"parent/\n")
            f.write(b"!parent/file.txt\n")
            f.write(b"!parent/child/\n")

        m = IgnoreFilterManager.from_repo(repo)

        # parent/ is excluded, so files inside cannot be re-included
        self.assertTrue(m.is_ignored("parent/"))
        self.assertTrue(m.is_ignored("parent/file.txt"))  # Cannot re-include
        self.assertTrue(m.is_ignored("parent/child/"))  # Cannot re-include
        self.assertTrue(m.is_ignored("parent/child/file.txt"))

    def test_parent_exclusion_with_wildcards(self) -> None:
        """Test parent directory exclusion with wildcard patterns."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Test case 2: Parent excluded by wildcard
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"*/build/\n")
            f.write(b"!*/build/important.txt\n")

        m = IgnoreFilterManager.from_repo(repo)

        self.assertTrue(m.is_ignored("src/build/"))
        self.assertTrue(m.is_ignored("src/build/important.txt"))  # Cannot re-include
        self.assertTrue(m.is_ignored("test/build/"))
        self.assertTrue(m.is_ignored("test/build/important.txt"))  # Cannot re-include

    def test_parent_exclusion_with_double_asterisk(self) -> None:
        """Test parent directory exclusion with ** patterns."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Test case 3: Complex ** pattern with parent exclusion
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"**/node_modules/\n")
            f.write(b"!**/node_modules/keep.txt\n")

        m = IgnoreFilterManager.from_repo(repo)

        self.assertTrue(m.is_ignored("node_modules/"))
        self.assertTrue(m.is_ignored("node_modules/keep.txt"))  # Cannot re-include
        self.assertTrue(m.is_ignored("src/node_modules/"))
        self.assertTrue(m.is_ignored("src/node_modules/keep.txt"))  # Cannot re-include
        self.assertTrue(m.is_ignored("deep/nested/node_modules/"))
        self.assertTrue(
            m.is_ignored("deep/nested/node_modules/keep.txt")
        )  # Cannot re-include

    def test_no_parent_exclusion_with_glob_contents(self) -> None:
        """Test that dir/** allows specific file negations for immediate children."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Test: dir/** allows specific file negations (unlike dir/ which doesn't)
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"logs/**\n")
            f.write(b"!logs/important.log\n")
            f.write(b"!logs/keep/\n")

        m = IgnoreFilterManager.from_repo(repo)

        # logs/ itself is excluded by logs/**
        self.assertTrue(m.is_ignored("logs/"))
        # Specific file negation works with dir/** patterns
        self.assertFalse(m.is_ignored("logs/important.log"))
        # Directory negations still don't work (parent exclusion)
        self.assertTrue(m.is_ignored("logs/keep/"))
        # Nested paths are ignored
        self.assertTrue(m.is_ignored("logs/subdir/"))
        self.assertTrue(m.is_ignored("logs/subdir/file.txt"))

    def test_parent_exclusion_ordering(self) -> None:
        """Test that parent exclusion depends on pattern ordering."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)

        # Test case 5: Order matters for parent exclusion
        with open(os.path.join(repo.path, ".gitignore"), "wb") as f:
            f.write(b"!data/important/\n")  # This comes first but won't work
            f.write(b"data/\n")  # This excludes the parent

        m = IgnoreFilterManager.from_repo(repo)

        self.assertTrue(m.is_ignored("data/"))
        self.assertTrue(m.is_ignored("data/important/"))  # Cannot re-include
        self.assertTrue(m.is_ignored("data/important/file.txt"))


class QuotePathTests(TestCase):
    """Tests for _quote_path function."""

    def test_ascii_paths(self) -> None:
        """Test that ASCII paths are not quoted."""
        self.assertEqual(_quote_path("file.txt"), "file.txt")
        self.assertEqual(_quote_path("dir/file.txt"), "dir/file.txt")
        self.assertEqual(_quote_path("path with spaces.txt"), "path with spaces.txt")

    def test_unicode_paths(self) -> None:
        """Test that unicode paths are quoted with C-style escapes."""
        # Russian characters
        self.assertEqual(
            _quote_path("тест.txt"), '"\\321\\202\\320\\265\\321\\201\\321\\202.txt"'
        )
        # Chinese characters
        self.assertEqual(
            _quote_path("файл.测试"),
            '"\\321\\204\\320\\260\\320\\271\\320\\273.\\346\\265\\213\\350\\257\\225"',
        )
        # Mixed ASCII and unicode
        self.assertEqual(
            _quote_path("test-тест.txt"),
            '"test-\\321\\202\\320\\265\\321\\201\\321\\202.txt"',
        )

    def test_special_characters(self) -> None:
        """Test that special characters are properly escaped."""
        # Quotes in filename
        self.assertEqual(
            _quote_path('file"with"quotes.txt'), '"file\\"with\\"quotes.txt"'
        )
        # Backslashes in filename
        self.assertEqual(
            _quote_path("file\\with\\backslashes.txt"),
            '"file\\\\with\\\\backslashes.txt"',
        )
        # Mixed special chars and unicode
        self.assertEqual(
            _quote_path('тест"файл.txt'),
            '"\\321\\202\\320\\265\\321\\201\\321\\202\\"\\321\\204\\320\\260\\320\\271\\320\\273.txt"',
        )

    def test_empty_and_edge_cases(self) -> None:
        """Test edge cases."""
        self.assertEqual(_quote_path(""), "")
        self.assertEqual(_quote_path("a"), "a")  # Single ASCII char
        self.assertEqual(_quote_path("я"), '"\\321\\217"')  # Single unicode char


class CheckIgnoreQuotePathTests(TestCase):
    """Integration tests for check_ignore with quote_path parameter."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir)

    def test_quote_path_true_unicode_filenames(self) -> None:
        """Test that quote_path=True returns quoted unicode filenames."""
        from dulwich import porcelain

        # Create a repository
        repo = Repo.init(self.test_dir)
        self.addCleanup(repo.close)

        # Create .gitignore with unicode patterns
        gitignore_path = os.path.join(self.test_dir, ".gitignore")
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write("тест*\n")
            f.write("*.测试\n")

        # Create unicode files
        test_files = ["тест.txt", "файл.测试", "normal.txt"]
        for filename in test_files:
            filepath = os.path.join(self.test_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("test content")

        # Test with quote_path=True (default)
        abs_paths = [os.path.join(self.test_dir, f) for f in test_files]
        ignored_quoted = set(
            porcelain.check_ignore(self.test_dir, abs_paths, quote_path=True)
        )

        # Test with quote_path=False
        ignored_unquoted = set(
            porcelain.check_ignore(self.test_dir, abs_paths, quote_path=False)
        )

        # Verify quoted results
        expected_quoted = {
            '"\\321\\202\\320\\265\\321\\201\\321\\202.txt"',  # тест.txt
            '"\\321\\204\\320\\260\\320\\271\\320\\273.\\346\\265\\213\\350\\257\\225"',  # файл.测试
        }
        self.assertEqual(ignored_quoted, expected_quoted)

        # Verify unquoted results
        expected_unquoted = {"тест.txt", "файл.测试"}
        self.assertEqual(ignored_unquoted, expected_unquoted)

    def test_quote_path_ascii_filenames(self) -> None:
        """Test that ASCII filenames are unaffected by quote_path setting."""
        from dulwich import porcelain

        # Create a repository
        repo = Repo.init(self.test_dir)
        self.addCleanup(repo.close)

        # Create .gitignore
        gitignore_path = os.path.join(self.test_dir, ".gitignore")
        with open(gitignore_path, "w") as f:
            f.write("*.tmp\n")
            f.write("test*\n")

        # Create ASCII files
        test_files = ["test.txt", "file.tmp", "normal.txt"]
        for filename in test_files:
            filepath = os.path.join(self.test_dir, filename)
            with open(filepath, "w") as f:
                f.write("test content")

        # Test both settings
        abs_paths = [os.path.join(self.test_dir, f) for f in test_files]
        ignored_quoted = set(
            porcelain.check_ignore(self.test_dir, abs_paths, quote_path=True)
        )
        ignored_unquoted = set(
            porcelain.check_ignore(self.test_dir, abs_paths, quote_path=False)
        )

        # Both should return the same results for ASCII filenames
        expected = {"test.txt", "file.tmp"}
        self.assertEqual(ignored_quoted, expected)
        self.assertEqual(ignored_unquoted, expected)
