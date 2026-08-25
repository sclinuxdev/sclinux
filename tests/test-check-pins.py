#!/usr/bin/env python3
"""上游钉校验器的回归测试。

上游仓库用本地临时仓库伪造：这里造出「钉在主线上」「钉被改写掉」「上游取不到」三种
形状，断言各自的判定，以及故障与答案是否始终可区分。全程不访问网络。
"""

import importlib.util
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_pins", HERE / "check-pins.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def run(*args: str, cwd: pathlib.Path) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def commit(message: str, repo: pathlib.Path) -> str:
    """在 repo 上落一笔空提交，返回它的完整提交号。"""
    run("commit", "-q", "--allow-empty", "-m", message, cwd=repo)
    return run("rev-parse", "HEAD", cwd=repo)


def upstream(scratch: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """伪造一个上游：默认分支两笔，外加一笔谁都到不了的游离提交。

    游离提交正是被改写掉的钉呈现的形状 —— 对象还在，但没有分支能到达它。
    """
    repo = scratch / "upstream"
    repo.mkdir()
    run("init", "-q", "-b", "main", ".", cwd=repo)
    run("config", "user.email", "test@example.invalid", cwd=repo)
    run("config", "user.name", "pin test", cwd=repo)
    commit("first", repo)
    on_branch = commit("second", repo)

    run("checkout", "-q", "-b", "scratch", cwd=repo)
    orphaned = commit("rewritten away", repo)
    run("checkout", "-q", "main", cwd=repo)
    run("branch", "-q", "-D", "scratch", cwd=repo)

    return repo, on_branch, orphaned


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

    with tempfile.TemporaryDirectory() as raw:
        scratch = pathlib.Path(raw)
        repo, on_branch, orphaned = upstream(scratch)
        url = str(repo)

        # 钉在默认分支上是唯一可靠的形状。
        with tempfile.TemporaryDirectory() as work:
            reachable, _ = checker.verdict(url, on_branch, pathlib.Path(work))
        failed += not check("a pin on the default branch passes", reachable, True)

        # 被改写掉的钉：对象在源仓库里还在，但取回的提交图到不了它。这正是 Stage1
        # 当初钉住那个 Sage 提交的形状。
        with tempfile.TemporaryDirectory() as work:
            reachable, detail = checker.verdict(url, orphaned, pathlib.Path(work))
        failed += not check("a pin no branch can reach fails", reachable, False)
        failed += not check(
            "the failure says the commit graph does not contain it",
            "没有这个对象" in detail,
            True,
        )

        # 上游取不到是故障，不能读成任何一种判定。
        try:
            with tempfile.TemporaryDirectory() as work:
                checker.verdict(str(scratch / "absent"), on_branch, pathlib.Path(work))
            outage = "no exception"
        except checker.Unreachable:
            outage = "Unreachable"
        except Exception as error:  # noqa: BLE001 -- 任何别的异常都算回归
            outage = type(error).__name__
        failed += not check(
            "an unreachable upstream raises Unreachable, not a verdict",
            outage,
            "Unreachable",
        )

    # 只有 40 位提交号算钉。release tarball 与 tag 归档给的是版本号，
    # 上游本来就应当持续提供。
    sha = "a" * 40
    failed += not check(
        "codeload commit archives are recognised as pins",
        checker.COMMIT_PIN.findall(f'url = "https://codeload.github.com/owner/repo/tar.gz/{sha}"'),
        [("owner", "repo", sha)],
    )
    failed += not check(
        "github archive commit URLs are recognised as pins",
        checker.COMMIT_PIN.findall(f'url = "https://github.com/owner/repo/archive/{sha}.tar.gz"'),
        [("owner", "repo", sha)],
    )
    failed += not check(
        "uppercase commit IDs are recognised as pins",
        checker.COMMIT_PIN.findall(
            f'url = "https://codeload.github.com/owner/repo/tar.gz/{sha.upper()}"'
        ),
        [("owner", "repo", sha.upper())],
    )
    failed += not check(
        "tag and release tarballs are not pins",
        checker.COMMIT_PIN.findall(
            'url = "https://github.com/systemd/systemd/archive/refs/tags/v261.2.tar.gz"'
        ),
        [],
    )
    failed += not check(
        "a short hash is not treated as a commit pin",
        checker.COMMIT_PIN.findall('url = "https://codeload.github.com/owner/repo/tar.gz/abc1234"'),
        [],
    )

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        active = root / "active" / "recipe.toml"
        active.parent.mkdir()
        active.write_text("[source]\nurl = 'https://example.invalid/v1.tar.gz'\n")
        active.with_name("src").mkdir()
        copied = active.with_name("src") / "copy" / "recipe.toml"
        copied.parent.mkdir()
        copied.write_text("")
        nested = root / "contrib" / "src" / "foo" / "recipe.toml"
        nested.parent.mkdir(parents=True)
        nested.write_text("")
        generated = root / "out" / "copy" / "recipe.toml"
        generated.parent.mkdir(parents=True)
        generated.write_text("")

        failed += not check(
            "only actual build product directories are skipped",
            list(checker.recipes(root)),
            [active, nested],
        )

        old_pin = "b" * 40
        active.write_text(
            "# https://codeload.github.com/owner/repo/tar.gz/"
            + old_pin
            + "\nprepare = ['echo https://codeload.github.com/owner/repo/tar.gz/"
            + old_pin
            + "']\n[source]\n"
            + "url = 'https://github.com/owner/repo/archive/refs/tags/v1.0.tar.gz'\n"
        )
        failed += not check(
            "comments and build commands are not effective source pins",
            list(checker.pins(root)),
            [],
        )

    print(f"\n{checks_run - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
