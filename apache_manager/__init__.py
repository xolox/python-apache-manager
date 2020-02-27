# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: February 27, 2020
# URL: https://apache-manager.readthedocs.io

"""The :mod:`apache_manager` module defines the core logic of the Apache manager."""

# Standard library modules.
import os
import re

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import (
    compact,
    concatenate,
    format_size,
    format_timespan,
    parse_size,
    parse_timespan,
    pluralize,
    Timer,
)
from humanfriendly.terminal import output
from humanfriendly.text import generate_slug
from proc.apache import find_apache_memory_usage, find_apache_workers
from proc.core import Process
from property_manager import (
    PropertyManager,
    cached_property,
    lazy_property,
    mutable_property,
    required_property,
    writable_property,
)
from six.moves.urllib.error import HTTPError
from six.moves.urllib.request import urlopen
from update_dotdee import ConfigLoader
from verboselogs import VerboseLogger

# Modules included in our package.
from apache_manager.exceptions import AddressDiscoveryError, StatusPageError

# Semi-standard module versioning.
__version__ = '2.1'

# Hide internal identifiers from API documentation.
__all__ = (
    # Configuration defaults.
    'CONFIG_NAME',
    'HANGING_WORKER_THRESHOLD',
    'IDLE_MODES',
    'NATIVE_WORKERS_LABEL',
    'PORTS_CONF',
    'STATUS_COLUMNS',
    # Public classes.
    'ApacheManager',
    'KillableWorker',
    'NetworkAddress',
    'NonNativeWorker',
    'WorkerStatus',
)

CONFIG_NAME = 'apache-manager'
"""The program name used to load configuration files (a string)."""

PORTS_CONF = '/etc/apache2/ports.conf'
"""
The absolute pathname of the configuration file that defines the port(s) that
Apache listens on (a string). This constant is used as a default value for
:attr:`~ApacheManager.ports_config`. It's based on Debian's Apache 2
packaging.
"""

STATUS_COLUMNS = (
    'Srv', 'PID', 'Acc', 'M', 'CPU', 'SS', 'Req', 'Conn', 'Child', 'Slot',
    'Client', 'VHost', 'Request',
)
"""
The expected column names in the worker status table of the Apache status page
(an iterable of strings).
"""

IDLE_MODES = ('_', 'I', '.')
"""
Worker modes that are considered idle (a tuple of strings). Refer to
:attr:`WorkerStatus.is_idle`.
"""

NATIVE_WORKERS_LABEL = 'native'
"""
The label used to identify native Apache workers in exported metrics (a string).

This is used by :func:`ApacheManager.save_metrics()` to distinguish native
Apache workers from WSGI process groups.
"""

HANGING_WORKER_THRESHOLD = 60 * 5
"""
The number of seconds before an active worker is considered 'hanging' (a
number). Refer to :attr:`ApacheManager.hanging_workers`.
"""

# Initialize a logger for this module.
logger = VerboseLogger(__name__)


class ApacheManager(PropertyManager):

    """
    Apache web server manager.

    Most of the computed properties on this class are cached to avoid repeated
    expensive computations (refer to :class:`~property_manager.cached_property`
    for details). The easiest way to invalidate all of these cached properties
    at once is to call the :func:`refresh()` method.
    """

    @cached_property
    def combined_memory_usage(self):
        """
        The result of :func:`~proc.apache.find_apache_memory_usage()`.

        This property caches the result so that when :attr:`memory_usage` and
        :attr:`wsgi_process_groups` are both dereferenced, the function
        :func:`~proc.apache.find_apache_memory_usage()` only has to be called
        once.
        """
        return find_apache_memory_usage()

    @lazy_property
    def config(self):
        """A dictionary with user defined configuration options."""
        if CONFIG_NAME in self.config_loader.section_names:
            return self.config_loader.get_options(CONFIG_NAME)
        return {}

    @lazy_property
    def config_loader(self):
        r"""
        An :class:`~update_dotdee.ConfigLoader` object that provides access to the configuration.

        .. [[[cog
        .. from update_dotdee import inject_documentation
        .. inject_documentation(program_name='apache-manager')
        .. ]]]

        Configuration files are text files in the subset of `ini syntax`_ supported by
        Python's configparser_ module. They can be located in the following places:

        =========  ============================  =================================
        Directory  Main configuration file       Modular configuration files
        =========  ============================  =================================
        /etc       /etc/apache-manager.ini       /etc/apache-manager.d/\*.ini
        ~          ~/.apache-manager.ini         ~/.apache-manager.d/\*.ini
        ~/.config  ~/.config/apache-manager.ini  ~/.config/apache-manager.d/\*.ini
        =========  ============================  =================================

        The available configuration files are loaded in the order given above, so that
        user specific configuration files override system wide configuration files.

        .. _configparser: https://docs.python.org/3/library/configparser.html
        .. _ini syntax: https://en.wikipedia.org/wiki/INI_file

        .. [[[end]]]

        Configuration options are loaded from the ``[apache-manager]`` section.
        The following options are currently supported:

        ============================  =================================
        Configuration option          Instance property (documentation)
        ``hanging-worker-threshold``  :attr:`hanging_worker_threshold`
        ``max-memory-active``         :attr:`max_memory_active`
        ``max-memory-idle``           :attr:`max_memory_idle`
        ``worker-timeout``            :attr:`worker_timeout`
        ============================  =================================
        """
        return ConfigLoader(program_name=CONFIG_NAME)

    @cached_property
    def foreign_workers(self):
        """A list of :class:`NonNativeWorker` objects."""
        native_process_ids = set(w.pid for w in self.workers)
        return [
            NonNativeWorker(process=process)
            for process in find_apache_workers()
            if process.pid not in native_process_ids
        ]

    @mutable_property
    def hanging_worker_threshold(self):
        """
        The number of seconds before an active worker is considered 'hanging' (a number).

        This value is used to compute :attr:`~ApacheManager.hanging_workers`.
        The configuration file option is called ``hanging-worker-threshold``
        (its value will be parsed by :func:`~humanfriendly.parse_timespan()`).
        The default value is :data:`HANGING_WORKER_THRESHOLD`.
        """
        value = self.config.get('hanging-worker-threshold')
        return parse_timespan(value) if value else HANGING_WORKER_THRESHOLD

    @cached_property
    def hanging_workers(self):
        """
        A list of workers that appear to be 'hanging' (unresponsive).

        :raises: Any exceptions raised by :attr:`html_status` or
                 :exc:`.StatusPageError` if parsing of the Apache status page
                 fails.

        This property's value is based on :attr:`workers` but excludes workers
        that aren't active and workers whose 'seconds since the beginning of
        the current request' is lower than :attr:`hanging_worker_threshold`.
        """
        return [ws for ws in self.workers if ws.is_active and ws.ss >= self.hanging_worker_threshold]

    @cached_property
    def html_status(self):
        """
        The content of Apache's `HTML status page`_ (a string). See also :attr:`text_status`.

        :raises: Any exceptions raised by :func:`fetch_status_page()`.

        .. _HTML status page: http://httpd.apache.org/docs/trunk/mod/mod_status.html
        """
        return self.fetch_status_page(self.html_status_url)

    @cached_property(writable=True)
    def html_status_url(self):
        """
        The URL on which Apache's HTML status page can be retrieved (a string).

        :raises: Any exceptions raised by :attr:`listen_addresses`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> manager.html_status_url
        'http://127.0.0.1:80/server-status'
        """
        status_url = "%s/server-status" % self.listen_addresses[0].url
        logger.debug("Discovered Apache HTML status page URL: %s", status_url)
        return status_url

    @cached_property
    def killable_workers(self):
        """
        A list of :class:`KillableWorker` objects.

        This combines :attr:`workers` and :attr:`foreign_workers`.
        """
        all_workers = list(self.workers)
        all_workers.extend(self.foreign_workers)
        return sorted(all_workers, key=lambda p: p.pid)

    @cached_property
    def listen_addresses(self):
        """
        The network address(es) where Apache is listening (a list of :class:`NetworkAddress` objects).

        :raises: :exc:`.AddressDiscoveryError` when discovery fails (e.g. because
                 ``/etc/apache2/ports.conf`` is missing or can't be parsed).

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> manager.listen_addresses
        [NetworkAddress(protocol='http',
                        address='127.0.0.1',
                        port=81,
                        url='http://127.0.0.1:81')]
        """
        logger.debug("Discovering where Apache is listening by parsing %s ..", self.ports_config)
        # Make sure the configuration file exists.
        if not os.path.isfile(self.ports_config):
            raise AddressDiscoveryError(compact("""
                Failed to discover any addresses or ports that Apache is
                listening on! The configuration file {filename} is missing. Are
                you sure the Apache web server is properly installed? If so
                you'll have to specify the configuration's location.
            """, filename=self.ports_config))
        # Parse the configuration file.
        matched_addresses = []
        pattern = re.compile(r'^(.+):(\d+)$')
        with open(self.ports_config) as handle:
            for lnum, line in enumerate(handle, start=1):
                tokens = line.split()
                # We are looking for `Listen' directives.
                if len(tokens) >= 2 and tokens[0] == 'Listen':
                    parsed_value = None
                    # Check for a port number without an IP address.
                    if tokens[1].isdigit():
                        parsed_value = NetworkAddress(port=int(tokens[1]))
                    else:
                        # Check for an IP address with a port number.
                        match = pattern.match(tokens[1])
                        if match:
                            address = match.group(1)
                            port = int(match.group(2))
                            if address == '0.0.0.0':
                                address = '127.0.0.1'
                            parsed_value = NetworkAddress(address=address, port=port)
                    # Check if we have a match.
                    if parsed_value is not None:
                        # Override the protocol if necessary.
                        if len(tokens) >= 3:
                            parsed_value.protocol = tokens[2]
                        logger.debug("Parsed listen directive on line %i: %s", lnum, parsed_value)
                        matched_addresses.append(parsed_value)
                    else:
                        logger.warning("Failed to parse listen directive on line %i: %s", lnum, line)
        # Sanity check the results.
        if not matched_addresses:
            raise AddressDiscoveryError(compact("""
                Failed to discover any addresses or ports that Apache is
                listening on! Maybe I'm parsing the wrong configuration file?
                ({filename})
            """, filename=self.ports_config))
        # Log and return sorted port numbers.
        logger.debug("Discovered %s that Apache is listening on: %s",
                     pluralize(len(matched_addresses), "address", "addresses"),
                     concatenate(map(str, matched_addresses)))
        return matched_addresses

    @property
    def manager_metrics(self):
        """
        Information about the interaction between the Apache manager and the Apache web server.

        Here's an example of the resulting dictionary:

        >>> from apache_manager import ApacheManager
        >>> from pprint import pprint
        >>> manager = ApacheManager()
        >>> pprint(manager.manager_metrics)
        {'foreign_worker_count': 0,
         'native_worker_count': 50,
         'status_response': True,
         'workers_hanging': 0,
         'workers_killed_active': 0,
         'workers_killed_idle': 0}

        Notes about these metrics:

        - The ``status_response`` key is :data:`None` by default. Once an
          Apache status page has been fetched it becomes :data:`True` if the
          status page was fetched successfully or :data:`False` if fetching of
          the status page failed (see :func:`fetch_status_page()`,
          :attr:`html_status` and :attr:`text_status`).
        - The ``workers_hanging`` key gives the number of hanging workers
          (based on the length of :attr:`hanging_workers`).
        - The ``workers_killed_active`` and ``workers_killed_idle`` keys give
          the number of Apache workers killed by :func:`kill_workers()`.
        """
        return dict(
            foreign_worker_count=len(self.foreign_workers),
            native_worker_count=len(self.workers),
            status_response=self.status_response,
            workers_hanging=len(self.hanging_workers),
            workers_killed_active=self.num_killed_active,
            workers_killed_idle=self.num_killed_idle,
        )

    @mutable_property
    def max_memory_active(self):
        """
        Memory limit for active Apache worker processes (number of bytes).

        The value of this property defines the maximum number of bytes of
        memory that active Apache worker processes are allowed to use (an
        integer) before :func:`kill_workers()` terminates them.

        The configuration file option is called ``max-memory-active``
        (its value will be parsed by :func:`~humanfriendly.parse_size()`).
        The default value of 0 disables killing of active workers.
        """
        value = self.config.get('max-memory-active')
        return parse_size(value, binary=True) if value else 0

    @mutable_property
    def max_memory_idle(self):
        """
        Memory limit for idle Apache worker processes (number of bytes).

        The value of this property defines the maximum number of bytes of
        memory that idle Apache worker processes are allowed to use (an
        integer) before :func:`kill_workers()` terminates them.

        The configuration file option is called ``max-memory-idle`` (its value
        will be parsed by :func:`~humanfriendly.parse_size()`). The default
        value of 0 disables killing of idle workers.
        """
        value = self.config.get('max-memory-idle')
        return parse_size(value, binary=True) if value else 0

    @cached_property
    def memory_usage(self):
        """
        The memory usage of the Apache workers (a :class:`~proc.apache.StatsList` object).

        Based on :func:`proc.apache.find_apache_memory_usage()`. See also
        :attr:`wsgi_process_groups`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> from pprint import pprint
        >>> manager = ApacheManager()
        >>> pprint(manager.memory_usage)
        [13697024, 466776064, 735391744, 180432896, 465453056]
        >>> print(manager.memory_usage.min)
        13697024
        >>> print(manager.memory_usage.average)
        141787428.571
        >>> print(manager.memory_usage.max)
        735391744
        """
        return self.combined_memory_usage[0]

    @writable_property
    def num_killed_active(self):
        """The number of active workers killed by :func:`kill_workers()` (an integer)."""
        return 0

    @writable_property
    def num_killed_idle(self):
        """The number of idle workers killed by :func:`kill_workers()` (an integer)."""
        return 0

    @mutable_property
    def ports_config(self):
        """
        The absolute pathname of the ``ports.conf`` configuration file (a string).

        The configuration file is expected to define the port(s) that Apache
        listens on. Defaults to :data:`PORTS_CONF`.
        """
        return PORTS_CONF

    @cached_property
    def server_metrics(self):
        """
        Global web server metrics parsed from the machine readable plain text status page.

        Here's an example of the values you can expect:

        >>> from apache_manager import ApacheManager
        >>> from pprint import pprint
        >>> manager = ApacheManager()
        >>> pprint(manager.server_metrics)
        {'busy_workers': 1,
         'bytes_per_request': 3120.19,
         'bytes_per_second': 1.52158,
         'cpu_load': 0.000195063,
         'idle_workers': 4,
         'requests_per_second': 0.000487657,
         'total_accesses': 85,
         'total_traffic': 259,
         'uptime': 174303}
        """
        logger.debug("Extracting metrics from Apache's plain text status page ..")
        return dict(
            # Example: "Total Accesses: 49038"
            total_accesses=int(self.extract_metric(r'^Total Accesses: (\d+)')),
            # Example: "Total kBytes: 169318"
            total_traffic=int(self.extract_metric(r'^Total KBytes: (\d+)')) * 1024,
            # Example: "CPULoad: 7.03642"
            cpu_load=float(self.extract_metric(r'^CPULoad: ([0-9.]+)')),
            # Example: "Uptime: 85017"
            uptime=int(self.extract_metric(r'^Uptime: (\d+)')),
            # Example: "ReqPerSec: .576802"
            requests_per_second=float(self.extract_metric(r'^ReqPerSec: ([0-9.]+)')),
            # Example: "BytesPerSec: 2039.38"
            bytes_per_second=float(self.extract_metric(r'^BytesPerSec: ([0-9.]+)')),
            # Example: "BytesPerReq: 3535.66"
            bytes_per_request=float(self.extract_metric(r'^BytesPerReq: ([0-9.]+)')),
            # Example: "BusyWorkers: 2"
            busy_workers=int(self.extract_metric(r'^BusyWorkers: (\d+)')),
            # Example: "IdleWorkers: 6"
            idle_workers=int(self.extract_metric(r'^IdleWorkers: (\d+)')),
        )

    @cached_property
    def slots(self):
        """
        The status of Apache workers (a list of :class:`WorkerStatus` objects).

        :raises: Any exceptions raised by :attr:`html_status` or
                 :exc:`.StatusPageError` if parsing of the Apache status page
                 fails.

        The :attr:`slots` property contains one :class:`WorkerStatus` object
        for each worker "slot" that Apache has allocated. This means that some
        of the :class:`WorkerStatus` objects may not have expected properties
        like :attr:`~WorkerStatus.pid` because they describe an "empty slot".
        See the :attr:`workers` property for a list of :class:`WorkerStatus`
        objects without empty slots.
        """
        # Use BeautifulSoup to parse the HTML response body.
        soup = BeautifulSoup(self.html_status, "html.parser")
        # Prepare a list of normalized column headings expected to be defined in the table.
        required_columns = [generate_slug(c) for c in STATUS_COLUMNS]
        # Check each table on the Apache status page, because different
        # multiprocessing modules result in a status page with a different
        # number of tables and the table with worker details is not clearly
        # marked as such in the HTML output ...
        for table in soup.findAll('table'):
            # Parse the table into a list of dictionaries, one for each row.
            matched_rows = list(parse_status_table(table))
            # Filter out rows that don't contain the required columns.
            validated_rows = [r for r in matched_rows if all(c in r for c in required_columns)]
            # If one or more rows remain we found the right table! :-)
            if validated_rows:
                return [WorkerStatus(status_fields=f) for f in validated_rows]
        raise StatusPageError(compact("""
            Failed to parse Apache status page! No tables found containing all
            of the required column headings and at least one row of data that
            could be parsed.
        """))

    @writable_property
    def status_response(self):
        """
        Whether the status page was fetched successfully by :func:`fetch_status_page()` (a boolean).

        This will be :data:`None` as long as :attr:`fetch_status_page` hasn't been called.
        """

    @cached_property
    def text_status(self):
        """
        The content of Apache's `plain text status page`_ (a string). See also :attr:`html_status`.

        :raises: Any exceptions raised by :func:`fetch_status_page()`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> print manager.text_status
        Total Accesses: 100
        Total kBytes: 275
        CPULoad: .000203794
        Uptime: 181556
        ReqPerSec: .000550794
        BytesPerSec: 1.55104
        BytesPerReq: 2816
        BusyWorkers: 1
        IdleWorkers: 5
        Scoreboard: ____W._.......................................

        .. _plain text status page: http://httpd.apache.org/docs/trunk/mod/mod_status.html#machinereadable
        """
        return self.fetch_status_page(self.text_status_url).decode()

    @cached_property
    def text_status_url(self):
        """
        The URL on which Apache's plain text status page can be retrieved (a string).

        :raises: Any exceptions raised by :attr:`listen_addresses`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> manager.text_status_url
        'http://127.0.0.1:80/server-status?auto'
        """
        status_url = "%s?auto" % self.html_status_url
        logger.debug("Discovered Apache plain text status page URL: %s", status_url)
        return status_url

    @mutable_property
    def worker_timeout(self):
        """
        Time limit for active requests (number of seconds).

        The value of this property defines the maximum number of seconds that
        Apache worker processes are allowed to spend on a single request
        before :func:`kill_workers()` terminates them.

        The configuration file option is called ``worker-timeout`` (its value
        will be parsed by :func:`~humanfriendly.parse_timespan()`). The default
        value of 0 disables killing of hanging workers.
        """
        value = self.config.get('worker-timeout')
        return parse_timespan(value) if value else 0

    @cached_property
    def workers(self):
        """
        The status of the Apache workers, a list of :class:`WorkerStatus` objects.

        :raises: Any exceptions raised by :attr:`html_status` or
                 :exc:`.StatusPageError` if parsing of the Apache status page
                 fails.

        This property's value is based on :attr:`slots` but excludes empty
        slots (i.e. every :class:`WorkerStatus` object in :attr:`workers` will
        have expected properties like :attr:`~WorkerStatus.pid`).
        """
        return [ws for ws in self.slots if ws.m != '.']

    @cached_property
    def wsgi_process_groups(self):
        """
        The memory usage of Apache workers in WSGI process groups.

        The value of this property is a dictionary with process group names as
        keys and :class:`~proc.apache.StatsList` objects as values.

        Based on :func:`proc.apache.find_apache_memory_usage()`. See also
        :attr:`memory_usage`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> from pprint import pprint
        >>> manager = ApacheManager()
        >>> pprint(manager.wsgi_process_groups)
        {'group-one': [44048384, 44724224, 44048384],
         'group-two': [52088832, 51879936, 55554048, 54956032, 54968320],
         'other-group': [13697024, 13697024, 13697024, 13697024]}
        """
        return self.combined_memory_usage[1]

    def extract_metric(self, pattern, default='0'):
        """
        Extract a metric from the Apache text status page.

        :param pattern: A regular expression that captures a metric from the
                        text status page (a string).
        :param default: The default value to return if the pattern isn't
                        matched (a string).
        :returns: The value of the capture group in the matched pattern or the
                  default value (if the pattern didn't match).

        This method is a helper for :attr:`server_metrics` that extracts
        metrics from the Apache text status page based on a regular expression
        pattern.
        """
        modified_pattern = re.sub(r'\s+', r'\\s+', pattern)
        match = re.search(modified_pattern, self.text_status, re.IGNORECASE | re.MULTILINE)
        if match:
            logger.debug("Pattern '%s' matched '%s'.", pattern, match.group(0))
            return match.group(1)
        else:
            logger.warning("Pattern %r didn't match plain text Apache status page contents!", pattern)
            return default

    def fetch_status_page(self, status_url):
        """
        Fetch an Apache status page and return its content.

        :param url: The URL of the status page (a string).
        :returns: The response body (a string).
        :raises: :exc:`.StatusPageError` if fetching of the status page fails.
        """
        timer = Timer()
        # Get the Apache status page.
        logger.debug("Fetching Apache status page from %s ..", status_url)
        try:
            response = urlopen(status_url)
        except HTTPError as e:
            # These objects can be treated as response objects.
            response = e
        # Validate the HTTP response status.
        response_code = response.getcode()
        if response_code != 200:
            # Record the failure.
            self.status_response = False
            # Notify the caller using a custom exception.
            raise StatusPageError(compact("""
                Failed to retrieve Apache status page from {url}! Expected to
                get HTTP response status 200, got {code} instead.
            """, url=status_url, code=response_code))
        response_body = response.read()
        logger.debug("Fetched %s in %s.", format_size(len(response_body)), timer)
        self.status_response = True
        return response_body

    def kill_workers(self, **options):
        """
        Kill Apache worker processes that exceed resource usage thresholds.

        :param max_memory_active: Overrides :attr:`max_memory_active`.
        :param max_memory_idle: Overrides :attr:`max_memory_idle`.
        :param timeout: Overrides :attr:`worker_timeout`.
        :param dry_run: :data:`True` disables the killing of workers, so that
                        the ramifications of running this method become clear
                        without doing any damage (defaults to :data:`False`).
        :returns: A list of integers with process ids of killed workers.

        Some implementation notes about this method:

        - If any of the parameters are zero the respective resource usage
          threshold will not be applied.
        - Memory usage is measured using :attr:`~KillableWorker.memory_usage`.
        - The number of seconds since the beginning of the most recent request
          is measured using :attr:`WorkerStatus.ss`.
        - Worker processes are killed using the
          :meth:`executor.process.ControllableProcess.kill()`
          method.

        See also :attr:`num_killed_active` and :attr:`num_killed_idle`.
        """
        killed = set()
        num_checked = 0
        dry_run = options.get('dry_run', False)
        # Use configured values for thresholds not specified by the caller.
        max_memory_active = options.get('max_memory_active', self.max_memory_active)
        max_memory_idle = options.get('max_memory_idle', self.max_memory_idle)
        timeout = options.get('timeout', self.worker_timeout)
        for worker in self.killable_workers:
            # Depending on the multiprocessing module in use multiple workers
            # may be using the same OS process. We leave it up to the caller
            # whether's it's wise to kill workers using non-preforked processes
            # (hint: it's not) but we definitely shouldn't try to kill a single
            # OS process more than once!
            if worker.pid not in killed:
                kill_worker = False
                memory_usage_threshold = max_memory_active if worker.is_active else max_memory_idle
                if memory_usage_threshold and worker.memory_usage > memory_usage_threshold:
                    logger.notice(
                        "Killing %s using %s (%s) ..",
                        worker, format_size(worker.memory_usage),
                        worker.request or 'last request unknown',
                    )
                    kill_worker = True
                elif timeout and worker.is_active and getattr(worker, 'ss', 0) > timeout:
                    logger.notice(
                        "Killing %s hanging for %s since last request (%s) ..",
                        worker, format_timespan(worker.ss),
                        worker.request or 'unknown',
                    )
                    kill_worker = True
                if kill_worker:
                    if not dry_run:
                        worker.process.kill()
                    killed.add(worker.pid)
                    if worker.is_active:
                        self.num_killed_active += 1
                    else:
                        self.num_killed_idle += 1
            num_checked += 1
        if killed:
            logger.notice("Killed %i of %s.", len(killed), pluralize(num_checked, "Apache worker"))
        else:
            logger.info("No Apache workers killed (found %s within resource usage limits).",
                        pluralize(num_checked, "worker"))
        return list(killed)

    def refresh(self):
        """Clear cached properties so that their values are recomputed when dereferenced."""
        self.clear_cached_properties()

    def save_metrics(self, data_file):
        """
        Store monitoring metrics in a data file.

        :param data_file: The pathname of the data file (a string).

        This method stores the metrics provided by :attr:`manager_metrics` and
        :attr:`server_metrics` in a text file in an easy to parse format.
        Here's an example of what the contents of the file look like::

            # Global Apache server metrics.
            busy-workers         1
            bytes-per-request    0.0
            bytes-per-second     0.0
            cpu-load             1.13893
            idle-workers         4
            requests-per-second  1.89822
            total-accesses       15
            total-traffic        0
            uptime               790212

            # Metrics internal to apache-manager.
            foreign-worker-count   0
            native-worker-count    50
            status-response        0
            workers-hanging        0
            workers-killed-active  0
            workers-killed-idle    0

            # Memory usage of native Apache worker processes.
            memory-usage  native  count    5
            memory-usage  native  min      331776
            memory-usage  native  max      1662976
            memory-usage  native  average  598016.0
            memory-usage  native  median   331776

            # Memory usage of 'example' WSGI worker processes.
            memory-usage  example  count    4
            memory-usage  example  min      356352
            memory-usage  example  max      372736
            memory-usage  example  average  368640.0
            memory-usage  example  median   372736.0

        The values in the example above have been aligned to ease readability;
        in reality the names and values are delimited by tab characters (as
        long as you parse the file as whitespace delimited name/value pairs it
        will be fine, this is trivial to do with e.g. AWK_).

        .. _AWK: https://en.wikipedia.org/wiki/AWK
        """
        if data_file == '-':
            logger.debug("Reporting metrics on standard output ..")
        else:
            logger.debug("Storing metrics in %s ..", data_file)
        # Start with the server metrics.
        listing = ['# Global Apache server metrics.']
        for name, value in sorted(self.server_metrics.items()):
            listing.append('%s\t%s' % (name.replace('_', '-'), value))
        # Add our internal metrics.
        listing.extend(['', '# Metrics internal to apache-manager.'])
        for name, value in sorted(self.manager_metrics.items()):
            if isinstance(value, bool):
                value = 0 if value else 1
            listing.append('%s\t%s' % (name.replace('_', '-'), value))
        # Add memory usage metrics per group of (WSGI) workers.
        groups = dict(self.wsgi_process_groups)
        ordered_group_names = [NATIVE_WORKERS_LABEL] + sorted(groups.keys())
        groups[NATIVE_WORKERS_LABEL] = self.memory_usage
        metric_names = ('count', 'min', 'max', 'average', 'median')
        for group_name in ordered_group_names:
            listing.append('')
            if group_name == NATIVE_WORKERS_LABEL:
                listing.append('# Memory usage of native Apache worker processes.')
            else:
                listing.append('# Memory usage of %r WSGI worker processes.' % group_name)
            for metric in metric_names:
                listing.append('\t'.join([
                    'memory-usage', group_name, metric, str(
                        len(groups[group_name]) if metric == 'count'
                        else getattr(groups[group_name], metric)
                    ),
                ]))
        if data_file == '-':
            output('\n'.join(listing))
        else:
            temporary_file = '%s.tmp' % data_file
            with open(temporary_file, 'w') as handle:
                handle.write('\n'.join(listing) + '\n')
            os.rename(temporary_file, data_file)


class NetworkAddress(PropertyManager):

    """Network address objects encapsulate everything we need to know to connect to Apache."""

    @property
    def url(self):
        """The URL corresponding to :attr:`protocol`, :attr:`address` and :attr:`port` (a string)."""
        tokens = [self.protocol, '://', self.address]
        if not ((self.protocol == 'http' and self.port == 80) or
                (self.protocol == 'https' and self.port == 443)):
            tokens.append(':%s' % self.port)
        return ''.join(tokens)

    @required_property
    def protocol(self):
        """The protocol that Apache is listening for (one of the strings 'http' or 'https')."""
        return 'https' if self.port == 443 else 'http'

    @required_property
    def address(self):
        """The IP address on which Apache is listening (a string)."""
        return '127.0.0.1'

    @required_property
    def port(self):
        """The port number on which Apache is listening (an integer)."""

    def __str__(self):
        """Use :attr:`url` for a human friendly representation."""
        return self.url


class KillableWorker(PropertyManager):

    """
    Abstract base class to represent killable Apache worker processes.

    Worker processes can be killed based on resource usage thresholds like
    memory usage and/or requests that are taking too long to process. There
    are currently two implementations of killable workers:

    - :class:`WorkerStatus` represents the information about a worker process
      that was retrieved from Apache's status page.

    - :class:`NonNativeWorker` represents processes that are direct descendants
      of the master Apache process but are not included in the workers listed
      on Apache's status page (e.g. WSGI daemon processes spawned by
      mod_wsgi_).
    """

    @required_property
    def is_active(self):
        """:data:`True` if the worker is processing a request, :data:`False` otherwise."""

    @property
    def is_alive(self):
        """:data:`True` if :attr:`process` is running, :data:`False` otherwise."""
        return self.process.is_alive if self.process else False

    @lazy_property
    def memory_usage(self):
        """
        The memory usage of the worker process in bytes.

        The value of this property is an integer or :data:`None` (if the
        process disappeared before the process information is requested).

        The value of this property is based on the
        :attr:`~proc.core.Process.rss` property of the
        :class:`proc.core.Process` class.
        """
        return self.process.rss if self.process else None

    @required_property
    def pid(self):
        """
        The process ID of the Apache worker (an integer or :data:`None`).

        If :attr:`process` is set then the value of :attr:`pid` defaults to
        :attr:`proc.core.Process.pid`.
        """
        return self.process.pid if self.process else None

    @mutable_property(cached=True)
    def process(self):
        """
        The :class:`proc.core.Process` object for this worker process (or :data:`None`).

        If :attr:`pid` is set then the value of :attr:`process` defaults to the
        result of :meth:`proc.core.Process.from_pid()`. If the worker process
        disappears before the process information is requested :attr:`process`
        will be :data:`None`.
        """
        return Process.from_pid(self.pid) if self.pid else None

    @mutable_property
    def request(self):
        """The HTTP status line of the most recent request (a string or :data:`None`)."""


class NonNativeWorker(KillableWorker):

    """
    Non-native Apache worker processes.

    Objects of this type represent processes that are direct descendants of the
    master Apache process but are not included in the workers listed on
    Apache's status page (e.g. WSGI daemon processes spawned by mod_wsgi_).

    These processes (assumed to be workers of one kind or another) can only be
    killed based on their memory usage, because this information can be easily
    retrieved from the Linux ``/proc`` file system without an API provided by
    the Apache web server (because there is no such API for non-native
    workers, to the best of my knowledge).

    .. _mod_wsgi: https://code.google.com/p/modwsgi/
    """

    @required_property
    def process(self):
        """The :class:`proc.core.Process` object for this worker process."""

    @required_property
    def is_active(self):
        """:data:`True` because this information isn't available for non-native workers."""
        return True

    def __str__(self):
        """Render a human friendly representation of a non-native Apache worker."""
        wsgi_process_group = getattr(self.process, 'wsgi_process_group', None)
        if wsgi_process_group:
            return "WSGI worker %i (%s)" % (self.pid, wsgi_process_group)
        else:
            return "non-native worker %i" % self.pid


class WorkerStatus(KillableWorker):

    """
    :class:`WorkerStatus` objects represent the state of an Apache worker.

    These objects are constructed by the :attr:`ApacheManager.workers`
    property. To give you an idea of what :class:`WorkerStatus` objects look
    like, here's a simple example:

    >>> from apache_manager import ApacheManager
    >>> manager = ApacheManager()
    >>> print(manager.workers[0])
    WorkerStatus(acc=(0, 6, 128),
                 child=0.01,
                 conn=0.0,
                 cpu=0.03,
                 is_active=False,
                 is_alive=True,
                 is_idle=True,
                 m='_',
                 memory_usage=5185536,
                 pid=31212,
                 process=Process(...),
                 req=1,
                 request='GET /server-status HTTP/1.1',
                 slot=0.2,
                 srv=(0, 38),
                 ss=234)

    The naming of the fields may look somewhat obscure, this is because they
    match the names given on the Apache status page. If any of the fields are
    not available their value will be :data:`None`. The following properties
    are parsed from the Apache status page:

    The following computed properties are based on the properties parsed from
    the Apache status page:
    """

    @required_property
    def status_fields(self):
        """The raw status fields extracted from Apache's status page (a dictionary)."""

    @lazy_property
    def acc(self):
        """The number of accesses this connection / this child / this slot (a tuple of three integers)."""
        raw_value = self.status_fields.get('acc', '0/0/0')
        return tuple(coerce_value(int, n) for n in raw_value.split('/'))

    @lazy_property
    def child(self):
        """The number of megabytes transferred this child (a float)."""
        return coerce_value(float, self.status_fields.get('child', '0'))

    @lazy_property
    def client(self):
        """The IP address of the client that was last served (a string)."""
        return self.status_fields.get('client')

    @lazy_property
    def conn(self):
        """The number of kilobytes transferred this connection (a float)."""
        return coerce_value(float, self.status_fields.get('conn', '0'))

    @lazy_property
    def cpu(self):
        """The CPU usage (number of seconds as a floating point number)."""
        return coerce_value(float, self.status_fields.get('cpu', '0'))

    @property
    def is_idle(self):
        """
        :data:`True` if the worker is idle, :data:`False` otherwise.

        The value of this property is based on :attr:`m` and
        :data:`IDLE_MODES`.
        """
        return self.m in IDLE_MODES

    @property
    def is_active(self):
        """
        :data:`True` if the worker isn't idle, :data:`False` otherwise.

        The value of this property is based on :attr:`is_idle`.
        """
        return not self.is_idle

    @lazy_property
    def m(self):
        """
        The mode of operation (a string).

        Here's an overview of known modes (not intended as an exhaustive list):

        =====  =================================
        Mode   Description
        =====  =================================
        ``_``  Waiting for connection
        ``S``  Starting up
        ``R``  Reading request
        ``W``  Sending reply
        ``K``  Keepalive (read)
        ``D``  DNS lookup
        ``C``  Closing connection
        ``L``  Logging
        ``G``  Gracefully finishing
        ``I``  Idle cleanup of worker
        ``.``  Open slot with no current process
        =====  =================================

        See also :attr:`is_active` and :attr:`is_idle`.
        """
        return self.status_fields.get('m')

    @lazy_property
    def pid(self):
        """The process ID of the Apache worker (an integer)."""
        return coerce_value(int, self.status_fields.get('pid'))

    @lazy_property
    def req(self):
        """The number of milliseconds required to process the most recent request (an integer)."""
        return coerce_value(int, self.status_fields.get('req'))

    @lazy_property
    def request(self):
        """
        The HTTP status line of the most recent request (a string or :data:`None`).

        The default value of the :attr:`request` field on Apache's status page
        is the string ``NULL``. This obscure implementation detail is hidden by
        the :attr:`request` property.
        """
        value = self.status_fields.get('request', 'NULL')
        return value if value != 'NULL' else None

    @lazy_property
    def slot(self):
        """The total number of megabytes transferred this slot (a float)."""
        return coerce_value(float, self.status_fields.get('slot', '0'))

    @lazy_property
    def srv(self):
        """Child Server number and generation (a tuple of two integers)."""
        raw_value = self.status_fields.get('srv', '0-0')
        return tuple(coerce_value(int, n) for n in raw_value.split('-'))

    @lazy_property
    def ss(self):
        """The number of seconds since the beginning of the most recent request (a float)."""
        return coerce_value(int, self.status_fields.get('ss', '0'))

    @lazy_property
    def vhost(self):
        """The server name and port of the virtual host that served the last request (a string)."""
        return self.status_fields.get('vhost')

    def __str__(self):
        """Render a human friendly representation of a native Apache worker."""
        return "native worker %i (%s)" % (self.pid, "active" if self.is_active else "idle")


def coerce_tag(tag):
    """
    Coerce a BeautifulSoup tag to its string contents (stripped from leading and trailing whitespace).

    Used by :func:`parse_status_table()` to get the text values of HTML tags.
    """
    try:
        return u''.join(tag.findAll(text=True)).strip()
    except Exception:
        return ''


def coerce_value(type, value):
    """
    Coerce a value to an expected type.

    :param type: The type to coerce the value to (any type).
    :param value: The value to coerce (any Python value).
    :returns: The coerced value or :data:`None` if an exception is raised
              during coercion.

    Used by :class:`WorkerStatus` to coerce metrics parsed from the Apache
    status page to their expected Python types.
    """
    try:
        return type(value)
    except Exception:
        return None


def parse_status_table(table):
    """Parse one of the status tables from Apache's HTML status page."""
    headings = dict((i, generate_slug(coerce_tag(th))) for i, th in enumerate(table.findAll('th')))
    logger.debug("Parsed table headings: %r", headings)
    for tr in table.findAll('tr'):
        values_by_index = [coerce_tag(td) for td in tr.findAll('td')]
        logger.debug("Parsed values by index: %r", values_by_index)
        if values_by_index:
            # Ignore exceptions during coercion.
            # TODO This can obscure real problems. Find a better way to make it robust!
            try:
                values_by_name = dict((headings[i], v) for i, v in enumerate(values_by_index))
                logger.debug("Parsed values by name: %r", values_by_name)
                yield values_by_name
            except Exception:
                pass
