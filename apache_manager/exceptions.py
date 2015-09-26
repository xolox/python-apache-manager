# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: September 26, 2015
# URL: https://apache-manager.readthedocs.org

"""
Custom exceptions raised by the Apache manager.

The :mod:`~apache_manager.exceptions` module defines custom exceptions raised
by the Apache manager.
"""


class ApacheManagerError(Exception):

    """Base exception for custom exceptions raised by :mod:`apache_manager`."""


class PortDiscoveryError(ApacheManagerError):

    """Raised by :attr:`.ApacheManager.listen_ports` when port discovery fails."""


class StatusPageError(ApacheManagerError):

    """Raised by :attr:`.ApacheManager.workers` when the status page can't be retrieved."""
