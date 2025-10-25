"""Git maintenance implementation.

This module provides the git maintenance functionality for optimizing
and maintaining Git repositories.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .repo import BaseRepo

logger = logging.getLogger(__name__)


class MaintenanceSchedule(str, Enum):
    """Maintenance schedule types."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass
class MaintenanceResult:
    """Result from running maintenance tasks."""

    tasks_run: list[str] = field(default_factory=list)
    tasks_succeeded: list[str] = field(default_factory=list)
    tasks_failed: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


class MaintenanceTask(ABC):
    """Base class for maintenance tasks."""

    name: str = ""

    def __init__(
        self,
        repo: "BaseRepo",
        auto: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Initialize maintenance task.

        Args:
            repo: Repository object
            auto: If True, only run if needed
            progress: Optional progress callback
        """
        self.repo = repo
        self.auto = auto
        self.progress = progress

    @abstractmethod
    def run(self) -> bool:
        """Run the maintenance task.

        Returns:
            True if successful, False otherwise
        """

    def is_enabled(self) -> bool:
        """Check if task is enabled in repository configuration.

        Returns:
            True if task is enabled
        """
        if not self.name:
            return False

        config = self.repo.get_config()

        try:
            enabled = config.get_boolean(
                (b"maintenance", self.name.encode()), b"enabled"
            )
            return enabled if enabled is not None else self.default_enabled()
        except KeyError:
            # Return default enabled state
            return self.default_enabled()

    def default_enabled(self) -> bool:
        """Return default enabled state for this task.

        Returns:
            True if task should be enabled by default
        """
        return False


class GcTask(MaintenanceTask):
    """Garbage collection maintenance task."""

    name = "gc"

    def default_enabled(self) -> bool:
        """GC is enabled by default."""
        return True

    def run(self) -> bool:
        """Run garbage collection.

        Returns:
            True if successful, False otherwise
        """
        from .gc import garbage_collect
        from .repo import Repo

        if self.progress:
            self.progress("Running gc task")
        assert isinstance(self.repo, Repo)
        garbage_collect(self.repo, auto=self.auto, progress=self.progress)
        return True


class CommitGraphTask(MaintenanceTask):
    """Commit-graph maintenance task."""

    name = "commit-graph"

    def default_enabled(self) -> bool:
        """Commit-graph is enabled by default."""
        return True

    def run(self) -> bool:
        """Update commit-graph file.

        Returns:
            True if successful, False otherwise
        """
        if self.progress:
            self.progress("Running commit-graph task")

        # Get all refs
        refs = list(self.repo.refs.as_dict().values())
        if refs:
            self.repo.object_store.write_commit_graph(refs, reachable=True)
        return True


class LooseObjectsTask(MaintenanceTask):
    """Loose-objects maintenance task.

    This packs loose objects that are not already packed.
    """

    name = "loose-objects"

    def run(self) -> bool:
        """Pack loose objects.

        Returns:
            True if successful, False otherwise
        """
        from .object_store import PackBasedObjectStore

        if self.progress:
            self.progress("Running loose-objects task")

        # Pack loose objects using the object store's method
        assert isinstance(self.repo.object_store, PackBasedObjectStore)
        count = self.repo.object_store.pack_loose_objects(progress=self.progress)

        if self.progress and count > 0:
            self.progress(f"Packed {count} loose objects")

        return True


class IncrementalRepackTask(MaintenanceTask):
    """Incremental-repack maintenance task.

    This consolidates pack files incrementally.
    """

    name = "incremental-repack"

    def run(self) -> bool:
        """Consolidate pack files incrementally.

        Returns:
            True if successful, False otherwise
        """
        from .object_store import PackBasedObjectStore

        if self.progress:
            self.progress("Running incremental-repack task")

        # Get all packs sorted by size
        assert isinstance(self.repo.object_store, PackBasedObjectStore)
        packs = self.repo.object_store.packs
        if len(packs) <= 1:
            # Nothing to consolidate
            if self.progress:
                self.progress("No packs to consolidate")
            return True

        # In auto mode, only repack if there are many small packs
        # This is a heuristic similar to git's auto gc behavior
        if self.auto:
            # Only repack if we have more than 50 packs
            # (matching git's gc.autoPackLimit default)
            if len(packs) < 50:
                if self.progress:
                    self.progress(
                        f"Skipping incremental repack: only {len(packs)} packs"
                    )
                return True

        # Perform a full repack to consolidate all packs
        if self.progress:
            self.progress(f"Consolidating {len(packs)} pack files")

        count = self.repo.object_store.repack(progress=self.progress)

        if self.progress:
            self.progress(f"Repacked {count} objects")

        return True


class PackRefsTask(MaintenanceTask):
    """Pack-refs maintenance task."""

    name = "pack-refs"

    def run(self) -> bool:
        """Pack loose references.

        Returns:
            True if successful, False otherwise
        """
        if self.progress:
            self.progress("Running pack-refs task")

        self.repo.refs.pack_refs(all=True)
        return True


class PrefetchTask(MaintenanceTask):
    """Prefetch maintenance task.

    This prefetches remote refs to keep the object database up-to-date.
    """

    name = "prefetch"

    def run(self) -> bool:
        """Prefetch remote refs.

        Returns:
            True if successful, False otherwise
        """
        from .porcelain import fetch
        from .repo import Repo

        if self.progress:
            self.progress("Running prefetch task")

        config = self.repo.get_config()

        # Get all configured remotes
        remotes = set()
        for section in config.sections():
            if len(section) == 2 and section[0] == b"remote":
                remotes.add(section[1].decode())

        if not remotes:
            if self.progress:
                self.progress("No remotes configured, skipping prefetch")
            return True

        # Fetch from each remote
        success = True
        for remote_name in sorted(remotes):
            try:
                if self.progress:
                    self.progress(f"Fetching from {remote_name}")

                # Fetch quietly without updating working tree
                # The fetch operation will update refs under refs/remotes/
                assert isinstance(self.repo, Repo)
                fetch(
                    self.repo,
                    remote_location=remote_name,
                    quiet=True,
                )
            except Exception as e:
                # Log error and mark as failed
                logger.error(f"Failed to fetch from {remote_name}: {e}")
                success = False

        return success


# Registry of available maintenance tasks
MAINTENANCE_TASKS: dict[str, type[MaintenanceTask]] = {
    "gc": GcTask,
    "commit-graph": CommitGraphTask,
    "loose-objects": LooseObjectsTask,
    "incremental-repack": IncrementalRepackTask,
    "pack-refs": PackRefsTask,
    "prefetch": PrefetchTask,
}


def get_enabled_tasks(
    repo: "BaseRepo",
    task_filter: Optional[list[str]] = None,
) -> list[str]:
    """Get list of enabled maintenance tasks.

    Args:
        repo: Repository object
        task_filter: Optional list of specific task names to run

    Returns:
        List of enabled task names
    """
    if task_filter:
        # Validate requested tasks exist
        return [name for name in task_filter if name in MAINTENANCE_TASKS]

    enabled_tasks = []

    # Check each task to see if it's enabled
    for task_name, task_class in MAINTENANCE_TASKS.items():
        # Create temporary task instance to check if enabled
        task = task_class(repo, auto=False, progress=None)
        if task.is_enabled():
            enabled_tasks.append(task_name)

    return enabled_tasks


def run_maintenance(
    repo: "BaseRepo",
    tasks: Optional[list[str]] = None,
    auto: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> MaintenanceResult:
    """Run maintenance tasks on a repository.

    Args:
        repo: Repository object
        tasks: Optional list of specific task names to run
        auto: If True, only run tasks if needed
        progress: Optional progress callback

    Returns:
        MaintenanceResult with task execution results
    """
    result = MaintenanceResult()

    enabled_tasks = get_enabled_tasks(repo, tasks)

    for task_name in enabled_tasks:
        result.tasks_run.append(task_name)

        task_class = MAINTENANCE_TASKS.get(task_name)
        if not task_class:
            result.tasks_failed.append(task_name)
            result.errors[task_name] = "Unknown task"
            continue

        try:
            task = task_class(repo, auto=auto, progress=progress)
            success = task.run()

            if success:
                result.tasks_succeeded.append(task_name)
            else:
                result.tasks_failed.append(task_name)
        except Exception as e:
            result.tasks_failed.append(task_name)
            result.errors[task_name] = str(e)
            logger.error(f"Task {task_name} failed: {e}")

    return result
