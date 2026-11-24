from __future__ import annotations

import re
from typing import Iterable, List


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def missing_distributions(packages: Iterable[str], installed: Iterable[str]) -> List[str]:
    installed_names = {normalize_distribution_name(name) for name in installed}
    return [
        package
        for package in packages
        if normalize_distribution_name(package) not in installed_names
    ]
