#!/bin/bash
# Usage: ./deploy.sh "describe the change"
cd /home/goobytapag/job-tracker
.venv/bin/python -m unittest discover -s tests -q || { echo "Tests failed, not deploying."; exit 1; }
git add .
git commit -m "${1:-update}"
git push
sudo systemctl restart job-tracker
echo "Deployed!"
