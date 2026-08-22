#!/usr/bin/env python3
"""校验配方钉住的上游提交仍然活在上游分支上。

配方按提交钉住第三方源码。被 force-push 或 rebase-merge 改写掉的提交还能下载一阵子，
然后就不能了：归档 URL 开始返回 404，所有据此记录的构建都不再可复现。这里真实发生过
一次 —— Stage1 钉了 Sage 一个 pull request 分支上的提交，评审期间那条分支被 rebase，
钉住的提交最后不属于任何分支。

本地什么都查不出来，因为配方照样解析、校验和照样匹配缓存里的内容。只有 forge 知道那个
提交是否仍能从上游默认分支到达，所以这里去问它。

    python3 tests/check-pins.py                    # 网络不可达时跳过
    python3 tests/check-pins.py --require-network  # 网络失败即失败（CI 用）

设置 GITHUB_TOKEN 会带上认证：未认证的 GitHub API 限额是每 IP 每小时 60 次，而 Actions
runner 共享出口 IP，撞上限额会让健康的构建失败。
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".github", "pkg", "src", "distfiles", "out"}

# codeload 与 /archive/ 两个入口都把提交号放在路径最后一段；其余形式（release
# tarball、版本 tag）不是按提交钉的，不在此列。
GITHUB_COMMIT = re.compile(
    r"https://(?:codeload\.github\.com|github\.com)/([^/]+)/([^/]+)/"
    r"(?:tar\.gz|archive(?:/refs/heads|/refs/tags)?)/([0-9a-f]{40})"
)

# per_page=1 把响应压小：diverged 的比较结果会带上完整提交列表，而这里只读 status。
API = "https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}?per_page=1"

ATTEMPTS = 3


class Unreachable(Exception):
    """问不到 forge —— 与「问到了但答案是否定的」不是一回事。"""


def recipes():
    for path in sorted(REPO.rglob("recipe.toml")):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def pins():
    """产出全树每一处 GitHub 提交钉：(配方路径, owner, repo, commit)。"""
    for path in recipes():
        for owner, repo, commit in GITHUB_COMMIT.findall(path.read_text()):
            yield path, owner, repo.removesuffix(".git"), commit


def headers() -> dict:
    head = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "sclinux-pin-check",
    }
    # CI 注入 GITHUB_TOKEN 以拿到较高限额；本地不设也能跑，只是受未认证配额约束。
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        head["Authorization"] = f"Bearer {token}"
    return head


def default_branch(owner: str, repo: str) -> str:
    return fetch(f"https://api.github.com/repos/{owner}/{repo}")["default_branch"]


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers=headers())
    last = ""
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            # 比较入口返回 404 表示这个提交根本不在该仓库里 —— 这是答案，不是故障。
            if error.code == 404:
                return {"status": "missing"}
            hint = ""
            if error.code in (403, 429):
                hint = "（限额或权限；设置 GITHUB_TOKEN 可提高配额）"
            raise Unreachable(f"{url}: HTTP {error.code}{hint}") from error
        except (urllib.error.URLError, http.client.HTTPException, OSError,
                json.JSONDecodeError) as error:
            # 响应被截断或连接中断属于故障，不是关于这个钉的答案。先重试再放弃。
            last = repr(error)
            if attempt + 1 < ATTEMPTS:
                time.sleep(1 + attempt)
    raise Unreachable(f"{url}: {last}")


def ancestor_of_default(owner: str, repo: str, commit: str) -> tuple[bool, str]:
    """判断 commit 是否能从上游默认分支到达。"""
    base = default_branch(owner, repo)
    # base...head 为 behind 表示 head 是 base 的祖先，identical 表示两者是同一个提交。
    # ahead 与 diverged 都意味着这个钉落在默认分支从未走过的线上。
    status = fetch(API.format(owner=owner, repo=repo, base=base, head=commit)).get("status")
    return status in {"behind", "identical"}, f"{status} relative to {base}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-network",
        action="store_true",
        help="treat an unreachable forge as a failure instead of skipping",
    )
    args = parser.parse_args(argv)

    found = list(pins())
    if not found:
        print("no GitHub commit pins found")
        return 0

    failed = 0
    skipped = 0
    for path, owner, repo, commit in found:
        where = path.relative_to(REPO)
        try:
            reachable, detail = ancestor_of_default(owner, repo, commit)
        except Unreachable as error:
            skipped += 1
            print(f"SKIP  {where}: cannot reach the forge ({error})")
            continue
        if reachable:
            print(f"ok    {where} pins {owner}/{repo}@{commit[:7]} ({detail})")
        else:
            failed += 1
            print(
                f"FAIL  {where} pins {owner}/{repo}@{commit[:7]}\n"
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
