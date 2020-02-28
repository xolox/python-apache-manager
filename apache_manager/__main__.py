# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: February 28, 2020
# URL: https://apache-manager.readthedocs.io

"""Enable the use of ``python -m apache_manager ...`` to invoke the command line interface."""

# Modules included in our package.
from apache_manager.cli import main

if __name__ == "__main__":
    main()
