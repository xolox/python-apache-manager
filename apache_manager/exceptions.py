# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: November 26, 2019
# URL: https://apache-manager.readthedocs.io

"""
Custom exceptions raised by the Apache manager.

The :mod:`~apache_manager.exceptions` module defines custom exceptions raised
by the Apache manager.
"""


class ApacheManagerError(Exception):

    """Base exception for custom exceptions raised by :mod:`apache_manager`."""


class AddressDiscoveryError(ApacheManagerError):

    """Raised by :attr:`~apache_manager.ApacheManager.listen_addresses` when port discovery fails."""


class StatusPageError(ApacheManagerError):

    """Raised by :attr:`~apache_manager.ApacheManager.workers` when the status page can't be retrieved."""
