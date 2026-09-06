#!/usr/bin/env bash
# Create the "Crucible — Phase roadmap" Projects v2 board and add every repo issue.
#
# One-time prerequisite (interactive — opens a browser):
#   gh auth refresh -s project,read:project
#
# Then:
#   scripts/setup-github-project.sh
#
# gh 2.30 has no `gh project` command, so this drives the GraphQL API directly.
# Safe to re-run: it reuses an existing board with the same title.
set -euo pipefail

OWNER="mbenachour"
REPO="mbenachour/crucible"
TITLE="Crucible — Phase roadmap"

owner_id=$(gh api graphql -f query='query($l:String!){user(login:$l){id}}' -f l="$OWNER" -q '.data.user.id')

# Reuse or create the board.
project_id=$(gh api graphql -f query='
  query($l:String!){ user(login:$l){ projectsV2(first:50){ nodes{ id title number url } } } }' \
  -f l="$OWNER" -q ".data.user.projectsV2.nodes[] | select(.title==\"$TITLE\") | .id" || true)

if [ -z "${project_id:-}" ]; then
  read project_id project_url < <(gh api graphql -f query='
    mutation($o:ID!,$t:String!){ createProjectV2(input:{ownerId:$o,title:$t}){ projectV2{ id url } } }' \
    -f o="$owner_id" -f t="$TITLE" -q '.data.createProjectV2.projectV2 | "\(.id) \(.url)"')
  echo "created: $project_url"
else
  echo "reusing existing board: $project_id"
fi

# Add every issue (open + closed) in the repo to the board.
for node_id in $(gh api "repos/$REPO/issues?state=all&per_page=100" -q '.[] | select(.pull_request|not) | .node_id'); do
  gh api graphql -f query='
    mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }' \
    -f p="$project_id" -f c="$node_id" -q '.data.addProjectV2ItemById.item.id' >/dev/null && echo "added $node_id"
done

echo "done — open the board from: gh api graphql -f query='{user(login:\"$OWNER\"){projectsV2(first:20){nodes{title url}}}}'"
