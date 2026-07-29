#!/bin/bash
# make deploy 的 pre-flight guard：在跑 sam deploy 之前擋掉 stale / dirty 的本機部署。
#
# 背景：2026-07-29 事故——本機一份過期的 checkout（template 裡還沒有
# TracesTable/CorrectionsTable）跑了 make deploy，CloudFormation 判定這兩個
# 資源被移除，直接刪掉兩張 DynamoDB 表。
#
# 檢查兩項：
#   1. 工作區必須乾淨（沒有未 commit 的 unstaged/staged 變更）
#   2. HEAD 必須等於 origin/main（避免過期 checkout 誤刪雲端資源）
#
# 緊急情境逃生口：make deploy FORCE=1 可跳過本檢查（風險自負）。
set -euo pipefail

if [ -n "${FORCE:-}" ]; then
    echo "⚠️  FORCE=1：已跳過部署前置檢查（工作區乾淨/HEAD 同步 origin/main），風險自負" >&2
    exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "❌ 部署中止：工作區有未 commit 的變更（unstaged 或 staged）。" >&2
    echo "   請先 git commit 或 git stash 後再部署。" >&2
    echo "   緊急情境可用 make deploy FORCE=1 跳過此檢查（風險自負）。" >&2
    exit 1
fi

git fetch -q origin main
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse FETCH_HEAD)
if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
    echo "❌ 部署中止：本機 HEAD ($LOCAL_HEAD) 與 origin/main ($REMOTE_HEAD) 不一致，可能是過期或未同步的 checkout。" >&2
    echo "   請先 git pull（或切到最新的 main）同步到 origin/main 後再部署。" >&2
    echo "   緊急情境可用 make deploy FORCE=1 跳過此檢查（風險自負）。" >&2
    exit 1
fi

echo "✅ deploy guard 通過：工作區乾淨，且 HEAD 與 origin/main 一致"
