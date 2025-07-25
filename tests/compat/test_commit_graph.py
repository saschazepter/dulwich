# test_commit_graph.py -- Compatibility tests for commit graph functionality
# Copyright (C) 2024 Jelmer Vernooij <jelmer@jelmer.uk>
#
# SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-or-later
# Dulwich is dual-licensed under the Apache License, Version 2.0 and the GNU
# General Public License as published by the Free Software Foundation; version 2.0
# or (at your option) any later version. You can redistribute it and/or
# modify it under the terms of either of these two licenses.

"""Compatibility tests for Git commit graph functionality.

These tests verify that dulwich's commit graph implementation behaves
identically to C Git's implementation.
"""

import os
import tempfile

from dulwich.commit_graph import find_commit_graph_file, read_commit_graph
from dulwich.graph import can_fast_forward, find_merge_base
from dulwich.repo import Repo

from .utils import CompatTestCase, run_git_or_fail


class CommitGraphCompatTests(CompatTestCase):
    """Compatibility tests for commit graph functionality."""

    # Commit graph was introduced in Git 2.18.0
    min_git_version = (2, 18, 0)

    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.repo_path = os.path.join(self.test_dir, "test-repo")

        # Set up git identity to avoid committer identity errors
        self.overrideEnv("GIT_COMMITTER_NAME", "Test Author")
        self.overrideEnv("GIT_COMMITTER_EMAIL", "test@example.com")
        self.overrideEnv("GIT_AUTHOR_NAME", "Test Author")
        self.overrideEnv("GIT_AUTHOR_EMAIL", "test@example.com")

    def tearDown(self):
        from .utils import rmtree_ro

        rmtree_ro(self.test_dir)

    def create_test_repo_with_history(self):
        """Create a test repository with some commit history."""
        # Initialize repository
        run_git_or_fail(["init"], cwd=self.test_dir)
        os.rename(os.path.join(self.test_dir, ".git"), self.repo_path)

        work_dir = os.path.join(self.test_dir, "work")
        os.makedirs(work_dir)

        # Create .git file pointing to our repo
        with open(os.path.join(work_dir, ".git"), "w") as f:
            f.write(f"gitdir: {self.repo_path}\n")

        # Create some commits
        commits = []
        for i in range(5):
            filename = f"file{i}.txt"
            with open(os.path.join(work_dir, filename), "w") as f:
                f.write(f"Content {i}\n")

            run_git_or_fail(["add", filename], cwd=work_dir)
            run_git_or_fail(
                [
                    "commit",
                    "-m",
                    f"Commit {i}",
                    "--author",
                    "Test Author <test@example.com>",
                    "--date",
                    f"2024-01-0{i + 1} 12:00:00 +0000",
                ],
                cwd=work_dir,
            )

            # Get the commit SHA
            result = run_git_or_fail(["rev-parse", "HEAD"], cwd=work_dir)
            commits.append(result.strip())

        # Create a branch and merge
        run_git_or_fail(["checkout", "-b", "feature"], cwd=work_dir)

        with open(os.path.join(work_dir, "feature.txt"), "w") as f:
            f.write("Feature content\n")
        run_git_or_fail(["add", "feature.txt"], cwd=work_dir)
        run_git_or_fail(
            [
                "commit",
                "-m",
                "Feature commit",
                "--author",
                "Test Author <test@example.com>",
                "--date",
                "2024-01-06 12:00:00 +0000",
            ],
            cwd=work_dir,
        )

        result = run_git_or_fail(["rev-parse", "HEAD"], cwd=work_dir)
        feature_commit = result.strip()

        # Merge back to master
        run_git_or_fail(["checkout", "master"], cwd=work_dir)
        run_git_or_fail(
            ["merge", "feature", "--no-ff", "-m", "Merge feature"], cwd=work_dir
        )

        result = run_git_or_fail(["rev-parse", "HEAD"], cwd=work_dir)
        merge_commit = result.strip()

        commits.extend([feature_commit, merge_commit])
        return commits, work_dir

    def test_commit_graph_generation_and_reading(self):
        """Test that dulwich can read commit graphs generated by C Git."""
        commits, work_dir = self.create_test_repo_with_history()

        # Generate commit graph with C Git
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Verify commit graph file exists
        graph_file = find_commit_graph_file(self.repo_path)
        self.assertIsNotNone(graph_file, "Commit graph file should exist")

        # Read with dulwich
        commit_graph = read_commit_graph(graph_file)
        self.assertIsNotNone(commit_graph, "Should be able to read commit graph")

        # Verify we have the expected number of commits
        self.assertGreater(len(commit_graph), 0, "Commit graph should contain commits")

        # Open the repository with dulwich
        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Verify that all commits in the graph are accessible
        for entry in commit_graph:
            # entry.commit_id is hex ObjectID
            commit_obj = repo.object_store[entry.commit_id]
            self.assertIsNotNone(
                commit_obj, f"Commit {entry.commit_id.decode()} should be accessible"
            )

            # Verify tree ID matches
            self.assertEqual(
                entry.tree_id,
                commit_obj.tree,
                f"Tree ID mismatch for commit {entry.commit_id.decode()}",
            )

            # Verify parent information
            self.assertEqual(
                entry.parents,
                list(commit_obj.parents),
                f"Parent mismatch for commit {entry.commit_id.decode()}",
            )

    def test_merge_base_with_commit_graph(self):
        """Test that merge-base calculations work the same with and without commit graph."""
        commits, work_dir = self.create_test_repo_with_history()

        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Get some commit IDs for testing
        main_head = repo.refs[b"refs/heads/master"]
        feature_head = repo.refs[b"refs/heads/feature"]

        # Calculate merge base without commit graph
        merge_base_no_graph = find_merge_base(repo, [main_head, feature_head])

        # Generate commit graph
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Force reload of repository to pick up commit graph
        repo.close()
        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Calculate merge base with commit graph
        merge_base_with_graph = find_merge_base(repo, [main_head, feature_head])

        # Results should be identical
        self.assertEqual(
            merge_base_no_graph,
            merge_base_with_graph,
            "Merge base should be same with and without commit graph",
        )

        # Compare with C Git's result
        git_result = run_git_or_fail(
            ["merge-base", main_head.decode(), feature_head.decode()], cwd=work_dir
        )
        git_merge_base = [git_result.strip()]

        self.assertEqual(
            merge_base_with_graph,
            git_merge_base,
            "Dulwich merge base should match C Git result",
        )

    def test_fast_forward_with_commit_graph(self):
        """Test that fast-forward detection works the same with and without commit graph."""
        commits, work_dir = self.create_test_repo_with_history()

        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Test with a simple fast-forward case (older commit to newer commit)
        commit1 = commits[1]  # Second commit
        commit2 = commits[3]  # Fourth commit

        # Check without commit graph
        can_ff_no_graph = can_fast_forward(repo, commit1, commit2)

        # Generate commit graph
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Force reload
        repo.close()
        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Check with commit graph
        can_ff_with_graph = can_fast_forward(repo, commit1, commit2)

        # Results should be identical
        self.assertEqual(
            can_ff_no_graph,
            can_ff_with_graph,
            "Fast-forward detection should be same with and without commit graph",
        )

        # Compare with C Git (check if commit1 is ancestor of commit2)
        from .utils import run_git

        returncode, stdout, stderr = run_git(
            ["merge-base", "--is-ancestor", commit1.decode(), commit2.decode()],
            cwd=work_dir,
            capture_stdout=True,
            capture_stderr=True,
        )
        git_can_ff = returncode == 0

        self.assertEqual(
            can_ff_with_graph,
            git_can_ff,
            "Dulwich fast-forward detection should match C Git",
        )

    def test_generation_numbers_consistency(self):
        """Test that generation numbers are consistent with Git's topology."""
        commits, work_dir = self.create_test_repo_with_history()

        # Generate commit graph
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Read with dulwich
        graph_file = find_commit_graph_file(self.repo_path)
        commit_graph = read_commit_graph(graph_file)

        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Build a map of commit to generation number
        generation_map = {}
        for entry in commit_graph:
            generation_map[entry.commit_id] = entry.generation

        # Verify generation number properties:
        # 1. Root commits have generation 1
        # 2. For any commit, generation > max(parent_generations)
        for entry in commit_graph:
            repo.object_store[entry.commit_id]

            if not entry.parents:
                # Root commit should have generation 1
                self.assertGreaterEqual(
                    entry.generation,
                    1,
                    f"Root commit {entry.commit_id.decode()} should have generation >= 1",
                )
            else:
                # Non-root commit should have generation > max parent generation
                max_parent_gen = 0
                for parent_id in entry.parents:
                    if parent_id in generation_map:
                        max_parent_gen = max(max_parent_gen, generation_map[parent_id])

                if max_parent_gen > 0:  # Only check if we have parent generation info
                    self.assertGreater(
                        entry.generation,
                        max_parent_gen,
                        f"Commit {entry.commit_id.decode()} generation should be > max parent generation",
                    )

    def test_commit_graph_with_different_options(self):
        """Test commit graph generation with different C Git options."""
        commits, work_dir = self.create_test_repo_with_history()

        # Test different generation strategies
        strategies = [
            ["--reachable"],
            ["--stdin-commits"],
            ["--append"],
        ]

        for i, strategy in enumerate(strategies):
            with self.subTest(strategy=strategy):
                # Clean up any existing commit graph
                graph_path = os.path.join(
                    self.repo_path, "objects", "info", "commit-graph"
                )
                if os.path.exists(graph_path):
                    try:
                        os.remove(graph_path)
                    except PermissionError:
                        # On Windows, handle read-only files
                        from .utils import remove_ro

                        remove_ro(graph_path)

                if strategy == ["--stdin-commits"]:
                    # For stdin-commits, we need to provide commit IDs
                    process_input = b"\n".join(commits[:3]) + b"\n"  # First 3 commits
                    run_git_or_fail(
                        ["commit-graph", "write", *strategy],
                        cwd=work_dir,
                        input=process_input,
                    )
                elif strategy == ["--append"]:
                    # First create a base graph
                    run_git_or_fail(
                        ["commit-graph", "write", "--reachable"], cwd=work_dir
                    )
                    # Then append (this should work even if nothing new to add)
                    run_git_or_fail(["commit-graph", "write", *strategy], cwd=work_dir)
                else:
                    run_git_or_fail(["commit-graph", "write", *strategy], cwd=work_dir)

                # Verify dulwich can read the generated graph
                graph_file = find_commit_graph_file(self.repo_path)
                if graph_file:  # Some strategies might not generate a graph
                    commit_graph = read_commit_graph(graph_file)
                    self.assertIsNotNone(
                        commit_graph,
                        f"Should be able to read commit graph generated with {strategy}",
                    )

    def test_commit_graph_incremental_update(self):
        """Test that dulwich can read incrementally updated commit graphs."""
        commits, work_dir = self.create_test_repo_with_history()

        # Create initial commit graph
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Read initial graph
        graph_file = find_commit_graph_file(self.repo_path)
        initial_graph = read_commit_graph(graph_file)
        initial_count = len(initial_graph)

        # Add another commit
        with open(os.path.join(work_dir, "new_file.txt"), "w") as f:
            f.write("New content\n")
        run_git_or_fail(["add", "new_file.txt"], cwd=work_dir)
        run_git_or_fail(
            [
                "commit",
                "-m",
                "New commit",
                "--author",
                "Test Author <test@example.com>",
                "--date",
                "2024-01-08 12:00:00 +0000",
            ],
            cwd=work_dir,
        )

        # Update commit graph
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Read updated graph
        updated_graph = read_commit_graph(graph_file)

        # Should have one more commit
        self.assertEqual(
            len(updated_graph),
            initial_count + 1,
            "Updated commit graph should have one more commit",
        )

        # Verify all original commits are still present
        initial_commit_ids = {entry.commit_id for entry in initial_graph}
        updated_commit_ids = {entry.commit_id for entry in updated_graph}

        self.assertTrue(
            initial_commit_ids.issubset(updated_commit_ids),
            "All original commits should still be in updated graph",
        )

    def test_commit_graph_with_tags_and_refs(self):
        """Test commit graph behavior with various refs and tags."""
        commits, work_dir = self.create_test_repo_with_history()

        # Create some tags
        run_git_or_fail(["tag", "v1.0", commits[2].decode()], cwd=work_dir)
        run_git_or_fail(
            ["tag", "-a", "v2.0", commits[4].decode(), "-m", "Version 2.0"],
            cwd=work_dir,
        )

        # Generate commit graph
        run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=work_dir)

        # Verify dulwich can read the graph
        graph_file = find_commit_graph_file(self.repo_path)
        commit_graph = read_commit_graph(graph_file)

        repo = Repo(self.repo_path)
        self.addCleanup(repo.close)

        # Verify tagged commits are in the graph
        tagged_commits = [commits[2], commits[4]]
        graph_commit_ids = {entry.commit_id for entry in commit_graph}

        for tagged_commit in tagged_commits:
            self.assertIn(
                tagged_commit,
                graph_commit_ids,
                f"Tagged commit {tagged_commit.decode()} should be in commit graph",
            )

        # Test merge base with tagged commits
        merge_base = find_merge_base(repo, [commits[0], commits[4]])
        self.assertEqual(
            merge_base,
            [commits[0]],
            "Merge base calculation should work with tagged commits",
        )

    def test_empty_repository_commit_graph(self):
        """Test commit graph behavior with empty repository."""
        # Create empty repository
        run_git_or_fail(["init", "--bare"], cwd=self.test_dir)
        empty_repo_path = os.path.join(self.test_dir, ".git")

        # Try to write commit graph (should succeed but create empty graph)
        try:
            run_git_or_fail(["commit-graph", "write", "--reachable"], cwd=self.test_dir)
        except AssertionError:
            # Some Git versions might fail on empty repos, which is fine
            pass

        # Check if commit graph file exists
        graph_file = find_commit_graph_file(empty_repo_path)
        if graph_file:
            # If it exists, dulwich should be able to read it
            commit_graph = read_commit_graph(graph_file)
            self.assertIsNotNone(
                commit_graph, "Should be able to read empty commit graph"
            )
            self.assertEqual(
                len(commit_graph), 0, "Empty repository should have empty commit graph"
            )
