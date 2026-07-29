"""Tests for the `make deploy` pre-flight guard (scripts/deploy-guard.sh).

Background (see docs/decisions): 2026-07-29 事故——本機一份過期的
checkout 跑了 `make deploy`，CloudFormation 判定 template 裡沒有的資源被
移除，直接刪掉了兩張 DynamoDB 表。這裡驗證 guard 能在跑 `sam deploy` 之前
擋掉 (1) 工作區 dirty (2) HEAD 落後 origin/main 這兩種情境，且乾淨/同步時
guard 會放行、真的進到 sam 這一步。

每個測試都用一個真的 bare git repo 當 origin，clone 出一份 workdir（帶
Makefile + scripts/deploy-guard.sh），透過 `make deploy` 驅動整條路徑，不
直接呼叫 shell script，藉此涵蓋 Makefile 與 guard script 的串接。

不打真的 AWS：把 PATH 指到一個 stub `sam`（記錄呼叫參數到檔案、直接
exit 0）與 stub `aws`（直接失敗，讓後續 smoke-test-processor 不會真的打
網路），乾淨/同步情境只驗證「guard 有放行、sam 有被叫到」。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
GUARD_SCRIPT = REPO_ROOT / "scripts" / "deploy-guard.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, timeout=30
    )


def _init_repo_with_remote(tmp_path: Path) -> Path:
    """建立一個 bare origin，clone 出一份帶 Makefile + guard script 的 workdir。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True, capture_output=True)

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "checkout", "-q", "-b", "main")

    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()
    shutil.copy(GUARD_SCRIPT, scripts_dir / "deploy-guard.sh")
    (scripts_dir / "deploy-guard.sh").chmod(0o755)
    shutil.copy(MAKEFILE, repo / "Makefile")
    # deploy target 有 `cd infra`；空目錄不會被 git 追蹤，不影響 dirty 判斷
    (repo / "infra").mkdir()

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "push", "-q", "-u", "origin", "main")
    # bare repo 預設 HEAD 可能指向 master（不存在），之後 clone 才能正常 checkout main
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=origin, check=True, capture_output=True
    )
    return repo


def _stub_bin_dir(tmp_path: Path, sam_log: Path) -> Path:
    """準備一個 PATH 目錄：stub sam（記錄呼叫參數、exit 0）+ stub aws（直接失敗，避免真的打網路）。"""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()

    sam_stub = bin_dir / "sam"
    sam_stub.write_text(f'#!/bin/bash\necho "$@" >> "{sam_log}"\nexit 0\n')
    sam_stub.chmod(0o755)

    aws_stub = bin_dir / "aws"
    aws_stub.write_text("#!/bin/bash\nexit 1\n")
    aws_stub.chmod(0o755)

    return bin_dir


def _run_make_deploy(repo: Path, extra_path: Path | None = None) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    if extra_path is not None:
        env["PATH"] = f"{extra_path}:{env['PATH']}"
    return subprocess.run(
        ["make", "deploy"], cwd=repo, capture_output=True, text=True, timeout=30, env=env
    )


class TestDeployGuard:
    def test_dirty_workspace_blocks_deploy_and_never_calls_sam(self, tmp_path):
        repo = _init_repo_with_remote(tmp_path)
        sam_log = tmp_path / "sam-calls.log"
        bin_dir = _stub_bin_dir(tmp_path, sam_log)

        (repo / "Makefile").write_text((repo / "Makefile").read_text() + "\n# uncommitted change\n")

        result = _run_make_deploy(repo, bin_dir)

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "未 commit" in output
        assert "FORCE=1" in output
        assert not sam_log.exists(), "sam 不該被呼叫到"

    def test_head_behind_origin_main_blocks_deploy_and_never_calls_sam(self, tmp_path):
        repo = _init_repo_with_remote(tmp_path)
        sam_log = tmp_path / "sam-calls.log"
        bin_dir = _stub_bin_dir(tmp_path, sam_log)

        # 從另一份 clone 推一個新 commit 上 origin/main，讓 repo 的 HEAD 落後
        other_clone = tmp_path / "other-clone"
        subprocess.run(
            ["git", "clone", "-q", str(tmp_path / "origin.git"), str(other_clone)],
            check=True,
            capture_output=True,
        )
        _git(other_clone, "config", "user.name", "Other")
        _git(other_clone, "config", "user.email", "other@example.com")
        (other_clone / "Makefile").write_text((other_clone / "Makefile").read_text() + "\n# newer commit\n")
        _git(other_clone, "add", "-A")
        _git(other_clone, "commit", "-q", "-m", "newer commit on origin/main")
        _git(other_clone, "push", "-q", "origin", "main")

        result = _run_make_deploy(repo, bin_dir)

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "origin/main" in output
        assert "FORCE=1" in output
        assert not sam_log.exists(), "sam 不該被呼叫到"

    def test_clean_and_synced_guard_passes_and_reaches_sam(self, tmp_path):
        repo = _init_repo_with_remote(tmp_path)
        sam_log = tmp_path / "sam-calls.log"
        bin_dir = _stub_bin_dir(tmp_path, sam_log)

        # 工作區乾淨、HEAD 與 origin/main 一致（剛 clone 完就是這個狀態）
        result = _run_make_deploy(repo, bin_dir)

        assert "guard 通過" in (result.stdout + result.stderr)
        assert sam_log.exists(), "guard 通過後應該要進到 sam deploy"
        sam_calls = sam_log.read_text()
        assert "--confirm-changeset" in sam_calls
        assert "deploy" in sam_calls

    def test_force_skips_guard_even_when_dirty(self, tmp_path):
        repo = _init_repo_with_remote(tmp_path)
        sam_log = tmp_path / "sam-calls.log"
        bin_dir = _stub_bin_dir(tmp_path, sam_log)

        (repo / "Makefile").write_text((repo / "Makefile").read_text() + "\n# uncommitted change\n")

        import os

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["FORCE"] = "1"
        result = subprocess.run(
            ["make", "deploy"], cwd=repo, capture_output=True, text=True, timeout=30, env=env
        )

        assert "FORCE=1" in result.stdout + result.stderr
        assert sam_log.exists(), "FORCE=1 應該跳過 guard、進到 sam deploy"
