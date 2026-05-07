#!/usr/bin/env bash
set -euo pipefail
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  golang-go cargo rustc ncat masscan dirsearch whatweb wafw00f arjun tcpdump wireshark \
  rizin jadx bulk-extractor scalpel testdisk yara stegseek outguess sonic-visualiser yq \
  nikto qemu-user sleuthkit tshark
