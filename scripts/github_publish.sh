#!/usr/bin/env bash
# Publish this repo to GitHub (requires Personal Access Token, NOT account password).
# Usage:
#   export GITHUB_TOKEN=ghp_xxxxxxxx   # from https://github.com/settings/tokens
#   bash scripts/github_publish.sh

set -euo pipefail
cd "$(dirname "$0")/.."

USER="${GITHUB_USER:-haijiang6666}"
REPO="${GITHUB_REPO:-Trump_following}"
REMOTE="https://${USER}:${GITHUB_TOKEN}@github.com/${USER}/${REPO}.git"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "ERROR: Set GITHUB_TOKEN first (repo scope)."
  echo "  GitHub no longer accepts account password for git push."
  echo "  Create token: https://github.com/settings/tokens → Fine-grained or classic (repo)"
  exit 1
fi

git branch -M main
if git remote get-url origin &>/dev/null; then
  git remote set-url origin "$REMOTE"
else
  git remote add origin "$REMOTE"
fi

# Create repo if missing (needs curl + token)
status=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  "https://api.github.com/repos/${USER}/${REPO}")
if [[ "$status" == "404" ]]; then
  echo "Creating github.com/${USER}/${REPO} ..."
  curl -s -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -d "{\"name\":\"${REPO}\",\"private\":false,\"description\":\"Trump OGE 278-T equity trade analysis\"}" \
    "https://api.github.com/user/repos" >/dev/null
fi

echo "Pushing to origin main ..."
git push -u origin main

# Reset remote to non-token URL (token stays in shell history if you used export — prefer env file)
git remote set-url origin "https://github.com/${USER}/${REPO}.git"

echo ""
echo "Done: https://github.com/${USER}/${REPO}"
echo "Enable GitHub Pages: repo → Settings → Pages → Deploy from branch main /docs or use gh-pages branch with index.html"
