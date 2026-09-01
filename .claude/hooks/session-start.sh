#!/bin/bash
set -euo pipefail

# Attribute commits made in this repository to its owner rather than to the
# coding agent. Agent sessions start with their own git identity, which would
# otherwise land in the history as the commit author and committer.
git config user.name  "ruiming2ai"
git config user.email "ruiming2ai@outlook.com"
