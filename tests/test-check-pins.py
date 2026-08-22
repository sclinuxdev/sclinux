#!/usr/bin/env python3
"""上游钉校验器的回归测试。

forge 被打桩：这里断言每种比较状态如何归类，以及故障与答案是否始终可区分，
全程不发网络请求。
"""

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_pins", HERE / "check-pins.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def with_status(status: str):
    """给 fetch() 打桩，让仓库查询与比较查询都能有应答。"""

    def fetch(url: str) -> dict:
        if "/compare/" in url:
            return {"status": status}
        return {"default_branch": "main"}

    return fetch


def outage(url: str) -> dict:
    raise checker.Unreachable(f"{url}: stubbed outage")


def reachable_for(status: str) -> bool:
    original = checker.fetch
    checker.fetch = with_status(status)
    try:
        result, _ = checker.ancestor_of_default("owner", "repo", "0" * 40)
        return result
    finally:
        checker.fetch = original


# 用计数而不是手写常量：常量在增删用例的那一刻就会失准，之后这行汇总就没意义了。
checks_run = 0


def check(description: str, actual: object, expected: object) -> bool:
    global checks_run
    checks_run += 1
    if actual == expected:
        print(f"ok    {description}")
        return True
    print(f"FAIL  {description}\n        expected {expected!r}, got {actual!r}")
    return False


def main() -> int:
    failed = 0

    # 钉是否可靠，取决于默认分支能否到达它。ahead 与 diverged 都描述分支从未走过
    # 的提交 —— 被改写掉的那个 Sage pull request 提交就是这个形状。
    for status, expected in (
        ("behind", True),
        ("identical", True),
        ("ahead", False),
        ("diverged", False),
        ("missing", False),
    ):
        failed += not check(
            f"comparison status {status!r} is {'accepted' if expected else 'rejected'}",
            reachable_for(status),
            expected,
        )

    # 故障不能被读成任何一种判定。
    original = checker.fetch
    checker.fetch = outage
    try:
        raised = False
        try:
            checker.ancestor_of_default("owner", "repo", "0" * 40)
        except checker.Unreachable:
            raised = True
        failed += not check("an unreachable forge raises Unreachable", raised, True)
    finally:
        checker.fetch = original

    # 只有 40 位提交号算钉。release tarball 与 tag 归档给的是版本号，
    # 上游本来就应当持续提供。
    commit = "a" * 40
    failed += not check(
        "codeload commit archives are recognised as pins",
        checker.GITHUB_COMMIT.findall(
            f"url = \"https://codeload.github.com/owner/repo/tar.gz/{commit}\""
        ),
        [("owner", "repo", commit)],
    )
    failed += not check(
        "github archive commit URLs are recognised as pins",
        checker.GITHUB_COMMIT.findall(
            f"url = \"https://github.com/owner/repo/archive/{commit}.tar.gz\""
        ),
        [("owner", "repo", commit)],
    )
    failed += not check(
        "tag and release tarballs are not pins",
        checker.GITHUB_COMMIT.findall(
            "url = \"https://github.com/systemd/systemd/archive/refs/tags/v261.2.tar.gz\""
        ),
        [],
    )
    failed += not check(
        "a short hash is not treated as a commit pin",
        checker.GITHUB_COMMIT.findall(
            "url = \"https://codeload.github.com/owner/repo/tar.gz/abc1234\""
        ),
        [],
    )

    # 构建产物里带着配方副本，一并遍历会重复报告。
    failed += not check(
        "build product directories are skipped",
        {"pkg", "src", "distfiles", "out"} <= checker.SKIP_DIRS,
        True,
    )

    print(f"\n{checks_run - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
