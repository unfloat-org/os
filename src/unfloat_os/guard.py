"""Data never enters git (contract C6): the pre-commit guard for every repository.

Generalised from vitreometry's original hook: scan data and images, files over the size limit and
device file names that carry patient identifiers are refused. A repository may allow specific
paths (for example its logo files) in `.unfloat.toml` under `[guard] allow`.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from unfloat_os import gitfiles
from unfloat_os.report import Report
from unfloat_os.workspace import RepoConfig

DATA_SUFFIXES = {
    # imaging devices and video
    ".cin",
    ".raw",
    ".rf",
    ".exam",
    ".ite",
    ".avi",
    ".mp4",
    ".mov",
    ".dcm",
    # images
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".gif",
    ".bmp",
    ".heic",
    # arrays, matrices and sequencing data
    ".npy",
    ".npz",
    ".h5",
    ".h5ad",
    ".hdf5",
    ".mat",
    ".pkl",
    ".parquet",
    ".mtx",
    ".bam",
    ".bai",
    ".cram",
    ".fastq",
    ".fq",
    ".loom",
    ".zarr",
    # archives
    ".gz",
    ".tgz",
    ".zip",
    ".tar",
    ".7z",
    # documents that carry copyright or personal data
    ".pdf",
}
# Quantel device names: <name>_<patient number>_<yyyymmddhhmmss>_<OD|OS>_
DEVICE_NAME = re.compile(r"_[0-9]{4,}_[0-9]{14}_O[DS]_")


def allowed(repo: RepoConfig, path: Path) -> bool:
    return any(fnmatch.fnmatch(path.as_posix(), pattern) for pattern in repo.guard_allow)


def check_paths(repo: RepoConfig, paths: list[Path], size_of) -> Report:
    report = Report()
    for path in paths:
        where = f"{repo.name}/{path.as_posix()}"
        if DEVICE_NAME.search(path.as_posix()):
            report.error(where, "device file name with patient identifiers")
        if allowed(repo, path):
            continue
        if path.suffix.lower() in DATA_SUFFIXES:
            report.error(where, "looks like data, an image or a document; it belongs in the data folder")
        size = size_of(path)
        if size > repo.guard_max_bytes:
            report.error(where, f"{size} bytes (limit {repo.guard_max_bytes})")
    return report


def check_staged(repo: RepoConfig) -> Report:
    report = check_paths(repo, gitfiles.staged(repo.path), lambda p: gitfiles.staged_size(repo.path, p))
    if any(DEVICE_NAME.search(line) for line in gitfiles.staged_added_lines(repo.path)):
        report.error(repo.name, "staged text contains a device file name with patient identifiers")
    return report


def check_tree(repo: RepoConfig) -> Report:
    """The same rules over every tracked file, for `uo check` and CI."""
    return check_paths(repo, gitfiles.tracked(repo.path), lambda p: (repo.path / p).stat().st_size)
