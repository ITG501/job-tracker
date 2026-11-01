#!/bin/bash
# Usage: ./deploy.sh "describe the change"
cd /home/goobytapag/job-tracker
git add .
git commit -m "${1:-update}"
git push
sudo systemctl restart job-tracker
echo "Deployed!"
