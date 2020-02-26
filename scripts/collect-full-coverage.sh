#!/bin/bash -e

cat >&2 << EOF

  To collect full coverage statistics the 'apache-manager' test suite needs to
  be run with super user privileges, so you will be asked to provide your sudo
  password. Please make sure you don't run this on production web servers
  because the test suite involves the killing of Apache workers that exceed
  resource usage thresholds.

EOF

sudo mkdir -p /var/lib/apache-manager
sudo chown www-data /var/lib/apache-manager
sudo $(which py.test) --cov --cov-fail-under=90
sudo rm -r /var/lib/apache-manager
