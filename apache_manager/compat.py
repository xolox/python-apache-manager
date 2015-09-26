# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: September 26, 2015
# URL: https://apache-manager.readthedocs.org

"""
Compatibility between Python 2 and Python 3.

The :mod:`~apache_manager.compat` module hides ugly details required to support
Python 2 as well as Python 3.
"""

# Python 2 / 3 compatibility.
try:
    # Python 2.
    from urllib2 import HTTPError, Request, urlopen  # NOQA
except ImportError:
    # Python 3.
    from urllib.request import HTTPError, Request, urlopen  # NOQA
