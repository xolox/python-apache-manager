#!/bin/bash -e

# This shell script is used by .travis.yml to install and configure the Apache
# webserver and mod_wsgi in order to test the apache-manager project. This script
# assumes it is running on Ubuntu 12.04 because that's what Travis CI uses at
# the time of writing.

# Let apt-get, dpkg and related tools know that we want the following
# commands to be 100% automated (no interactive prompts).
export DEBIAN_FRONTEND=noninteractive

# Update apt-get's package lists.
sudo apt-get update -qq

# Use apt-get to install the Apache webserver and mod_wsgi.
sudo apt-get install --yes apache2 libapache2-mod-wsgi

# Enable the prefork MPM.
a2dismod --maintmode --quiet mpm_event
a2dismod --maintmode --quiet mpm_worker
a2enmod --maintmode --quiet mpm_prefork
service apache2 restart
