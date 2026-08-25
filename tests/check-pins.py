#!/usr/bin/env python3
"""校验配方钉住的上游提交仍然活在上游默认分支上。

配方按提交钉住第三方源码。被 force-push 或 rebase-merge 改写掉的提交还能下载一阵子，
然后就不能了：归档 URL 开始返回 404，所有据此记录的构建都不再可复现。这里真实发生过
一次 —— Stage1 钉了 Sage 一个 pull request 分支上的提交，评审期间那条分支被 rebase，
钉住的提交最后不属于任何分支。

本地什么都查不出来，因为配方照样解析、校验和照样匹配缓存里的内容。判据只存在于上游
仓库的提交图里，所以这里把那张图取回来问 git：钉住的提交是不是默认分支的祖先。

取图用 `--filter=blob:none`，只要提交与树、不要文件内容 —— 一个上游通常几百 KB、一两秒。
判定用 `git merge-base --is-ancestor`，是 git 自己的祖先语义，不依赖任何 forge 的 API。

    python3 tests/check-pins.py                    # 网络不可达时跳过
    python3 tests/check-pins.py --require-network  # 网络失败即失败（CI 用）
"""

import argparse
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD_ARTIFACT_DIRS = {"pkg", "src", "distfiles"}
GENERATED_TOP_LEVEL_DIRS = {".git", "out"}

# 只认直接以 40 位对象 ID 为 ref 的归档。显式 refs/tags 与 refs/heads 即使名字
# 长得像哈希也不是提交钉；GitHub 的网页/codeload 与 REST 归档各有一种路径形状。
COMMIT_PIN_PATTERNS = (
    re.compile(
        r"^https://(?:codeload\.github\.com|github\.com)/([^/]+)/([^/\s]+?)/"
        r"(?:tar\.gz|zip|archive)/([0-9a-f]{40})"
        r"(?:\.(?:tar\.gz|zip))?(?:[?#].*)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^https://api\.github\.com/repos/([^/]+)/([^/\s]+?)/"
        r"(?:tarball|zipball)/([0-9a-f]{40})(?:[?#].*)?$",
        re.IGNORECASE,
    ),
)

TIMEOUT = 180


class Unreachable(Exception):
    """取不到上游的提交图 —— 与「取到了但这个钉不在分支上」不是一回事。"""


def is_build_artifact(path: Path, root: Path) -> bool:
    """path 是否位于某个配方自己的 sage build 产物目录。"""
    for ancestor in path.parents:
        if ancestor == root:
            return False
        if (
            ancestor.name in BUILD_ARTIFACT_DIRS
            and (ancestor.parent / "recipe.toml").is_file()
        ):
            return True
    return False


def recipes(root: Path = REPO):
    for path in sorted(root.rglob("recipe.toml")):
        relative = path.relative_to(root)
        if relative.parts[0] in GENERATED_TOP_LEVEL_DIRS:
            continue
        # sage build 只会在配方目录自身下面创建这些目录。仅凭祖先目录同名就跳过，
        # 会漏掉 contrib/src/foo/recipe.toml 这类合法配方树。
        if is_build_artifact(path, root):
            continue
        yield path


def commit_pin(url: str) -> tuple[str, str, str] | None:
    """解析 GitHub 提交归档 URL；tag、branch 与 release URL 返回 None。"""
    for pattern in COMMIT_PIN_PATTERNS:
        match = pattern.match(url)
        if match:
            owner, repo, commit = match.groups()
            return owner, repo.removesuffix(".git"), commit.lower()
    return None


def pins(root: Path = REPO):
    """产出全树每一处提交钉：(配方路径, 上游 URL, commit)。"""
    for path in recipes(root):
        source = tomllib.loads(path.read_text()).get("source")
        url = source.get("url") if isinstance(source, dict) else None
        parsed = commit_pin(url) if isinstance(url, str) else None
        if parsed:
            owner, repo, commit = parsed
            yield (
                path,
                f"https://github.com/{owner}/{repo}.git",
                commit,
            )


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT
    )


def fetch_default_branch(url: str, workdir: Path) -> None:
    """把上游默认分支的提交图取进 workdir，FETCH_HEAD 指向它的顶端。"""
    if git("init", "-q", ".", cwd=workdir).returncode != 0:
        raise Unreachable("cannot create a scratch repository")
    # HEAD 就是远端的默认分支，不必先问它叫什么名字。
    fetched = git("fetch", "-q", "--filter=blob:none", url, "HEAD", cwd=workdir)
    if fetched.returncode != 0:
        lines = fetched.stderr.strip().splitlines()
        raise Unreachable(f"{url}: {lines[-1] if lines else 'fetch failed'}")


def verdict(commit: str, workdir: Path) -> tuple[bool, str]:
    """钉住的提交能否从上游默认分支到达。"""
    # 提交图已经取回，对象仍然缺失就说明默认分支根本到不了它 —— 这正是被改写掉的钉
    # 呈现的样子，而不是一次故障。
    if git("cat-file", "-e", f"{commit}^{{commit}}", cwd=workdir).returncode != 0:
        return False, "上游默认分支的提交图里没有这个对象"
    reachable = git("merge-base", "--is-ancestor", commit, "FETCH_HEAD", cwd=workdir)
    if reachable.returncode == 0:
        return True, "是默认分支的祖先"
    return False, "对象存在但不是默认分支的祖先"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-network",
        action="store_true",
        help="treat an unreachable upstream as a failure instead of skipping",
    )
    args = parser.parse_args(argv)

    found = list(pins())
    if not found:
        print("no commit pins found")
        return 0

    grouped: dict[str, list[tuple[Path, str]]] = {}
    for path, url, commit in found:
        grouped.setdefault(url, []).append((path, commit))

    failed = 0
    skipped = 0
    for url, entries in grouped.items():
        try:
            with tempfile.TemporaryDirectory() as scratch:
                workdir = Path(scratch)
                fetch_default_branch(url, workdir)
                results = [
                    (path, commit, *verdict(commit, workdir))
                    for path, commit in entries
                ]
        except (Unreachable, subprocess.TimeoutExpired) as error:
            skipped += len(entries)
            for path, _ in entries:
                print(
                    f"SKIP  {path.relative_to(REPO)}: "
                    f"cannot reach the upstream ({error})"
                )
            continue
        for path, commit, reachable, detail in results:
            where = path.relative_to(REPO)
            short = (
                f"{url.removeprefix('https://github.com/').removesuffix('.git')}"
                f"@{commit[:7]}"
            )
            if reachable:
                print(f"ok    {where} pins {short} ({detail})")
            else:
                failed += 1
                print(
                    f"FAIL  {where} pins {short}\n"
                    f"        {detail} -- the pinned commit is not on the upstream\n"
                    f"        default branch, so its archive URL is not durable.\n"
                    f"        Repin to a commit that branch contains."
                )

    print(f"\n{len(found) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    if skipped and args.require_network:
        print("--require-network was given, so a skipped pin is a failure")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
