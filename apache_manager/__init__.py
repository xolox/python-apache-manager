# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 4, 2015
# URL: https://apache-manager.readthedocs.org

"""The :mod:`apache_manager` module defines the core logic of the Apache manager."""

# Semi-standard module versioning.
__version__ = '0.2'

# Hide internal identifiers from API documentation.
__all__ = (
    'ApacheManager',
    'IDLE_MODES',
    'KillableWorker',
    'NonNativeWorker',
    'PORTS_CONF',
    'STATUS_COLUMNS',
    'WorkerStatus',
)

# Standard library modules.
import logging
import os
import re

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import compact, format_size, format_timespan, pluralize, Timer
from proc.apache import find_apache_memory_usage, find_apache_workers
from proc.core import Process
from property_manager import PropertyManager, cached_property, lazy_property, mutable_property, writable_property

# Modules included in our package.
from apache_manager.exceptions import PortDiscoveryError, StatusPageError
from apache_manager.compat import HTTPError, urlopen

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

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


class ApacheManager(PropertyManager):

    """
    Apache web server manager.

    Most of the computed properties on this class are cached to avoid repeated
    expensive computations (refer to :class:`~property_manager.cached_property`
    for details). The easiest way to invalidate all of these cached properties
    at once is to call the :func:`refresh()` method.
    """

    def __init__(self, *args, **kw):
        """
        Initialize a :class:`ApacheManager` object.

        :param args: The first positional argument is used to set
                     :attr:`ports_config`.
        """
        if args:
            args = list(args)
            kw['ports_config'] = args.pop(0)
        super(ApacheManager, self).__init__(*args, **kw)

    @writable_property
    def num_killed_active(self):
        """The number of active workers killed by :func:`kill_workers()` (an integer)."""
        return 0

    @writable_property
    def num_killed_idle(self):
        """The number of idle workers killed by :func:`kill_workers()` (an integer)."""
        return 0

    @writable_property
    def status_response(self):
        """
        Whether the status page was fetched successfully by :func:`fetch_status_page()` (a boolean).

        This will be :data:`None` as long as :attr:`fetch_status_page` hasn't been called.
        """
        return None

    @mutable_property
    def ports_config(self):
        """
        The absolute pathname of the ``ports.conf`` configuration file (a string).

        The configuration file is expected to define the port(s) that Apache
        listens on. Defaults to :data:`PORTS_CONF`.
        """
        return PORTS_CONF

    @cached_property
    def listen_ports(self):
        """
        Port(s) on which Apache is configured to be listening (a sorted list of integers).

        :raises: :exc:`.PortDiscoveryError` when port discovery fails (e.g.
                 because ``/etc/apache2/ports.conf`` is missing or can't be
                 parsed).

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> manager.listen_ports
        [80, 443]
        """
        logger.debug("Discovering Apache ports by parsing %s ..", self.ports_config)
        # Make sure the configuration file exists.
        if not os.path.isfile(self.ports_config):
            raise PortDiscoveryError(compact("""
                Failed to discover port(s) that Apache is listening on! The
                configuration file {filename} is missing. Are you sure the Apache
                web server is properly installed? If so you'll have to specify the
                location of the configuration file.
            """, filename=self.ports_config))
        # Parse the configuration file.
        matched_ports = []
        with open(self.ports_config) as handle:
            for lnum, line in enumerate(handle, start=1):
                tokens = line.split()
                if len(tokens) >= 2 and tokens[0] == 'Listen' and tokens[1].isdigit():
                    port_number = int(tokens[1])
                    if port_number not in matched_ports:
                        logger.debug("Found port number %i on line %i.", port_number, lnum)
                        matched_ports.append(port_number)
        # Sanity check the results.
        if not matched_ports:
            raise PortDiscoveryError(compact("""
                Failed to discover port(s) that Apache is listening on! Maybe I'm
                parsing the wrong configuration file? ({filename})
            """, filename=self.ports_config))
        # Log and return sorted port numbers.
        sorted_ports = sorted(matched_ports)
        logger.debug("Discovered %s: %s", pluralize(len(sorted_ports), "Apache port"), sorted_ports)
        return sorted_ports

    @cached_property
    def html_status_url(self):
        """
        The URL on which Apache's HTML status page can be retrieved (a string).

        :raises: Any exceptions raised by :attr:`listen_ports`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> manager.status_url
        'http://127.0.0.1:80/server-status'
        """
        port_number = self.listen_ports[0]
        url_scheme = 'https' if port_number == 443 else 'http'
        status_url = "%s://127.0.0.1:%i/server-status" % (url_scheme, port_number)
        logger.debug("Discovered Apache HTML status page URL: %s", status_url)
        return status_url

    @cached_property
    def text_status_url(self):
        """
        The URL on which Apache's plain text status page can be retrieved (a string).

        :raises: Any exceptions raised by :attr:`listen_ports`.

        Here's an example:

        >>> from apache_manager import ApacheManager
        >>> manager = ApacheManager()
        >>> manager.status_url
        'http://127.0.0.1:80/server-status?auto'
        """
        status_url = "%s?auto" % self.html_status_url
        logger.debug("Discovered Apache plain text status page URL: %s", status_url)
        return status_url

    @cached_property
    def html_status(self):
        """
        The content of Apache's `HTML status page`_ (a string). See also :attr:`text_status`.

        :raises: Any exceptions raised by :func:`fetch_status_page()`.

        .. _HTML status page: http://httpd.apache.org/docs/trunk/mod/mod_status.html
        """
        return self.fetch_status_page(self.html_status_url)

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
        like :attr`WorkerStatus.pid` because they describe an "empty slot".
        See the :attr:`workers` property for a list of :class:`WorkerStatus`
        objects without empty slots.
        """
        # Use BeautifulSoup to parse the HTML response body.
        soup = BeautifulSoup(self.html_status, "html.parser")
        # Prepare a list of normalized column headings expected to be defined in the table.
        required_columns = [normalize_text(c) for c in STATUS_COLUMNS]
        # Check each table on the Apache status page, because different
        # multiprocessing modules result in a status page with a different
        # number of tables and the table with worker details is not clearly
        # marked as such in the HTML output ...
        for table in soup.findAll('table'):
            # Parse the table into a list of dictionaries, one for each row.
            matched_rows = list(parse_status_table(table, WorkerStatus))
            # Filter out rows that don't contain the required columns.
            validated_rows = [r for r in matched_rows if all(c in r for c in required_columns)]
            # If one or more rows remain we found the right table! :-)
            if validated_rows:
                return validated_rows
        raise StatusPageError(compact("""
            Failed to parse Apache status page! No tables found containing all
            of the required column headings and at least one row of data that
            could be parsed.
        """))

    @cached_property
    def workers(self):
        """
        The status of the Apache workers, a list of :class:`WorkerStatus` objects.

        :raises: Any exceptions raised by :attr:`html_status` or
                 :exc:`.StatusPageError` if parsing of the Apache status page
                 fails.

        This property's value is based on :attr:`slots` but excludes empty
        slots (i.e. every :class:`WorkerStatus` object in :attr:`workers` will
        have expected properties like :attr`WorkerStatus.pid`).
        """
        return [ws for ws in self.slots if ws.m != '.']

    @cached_property
    def killable_workers(self):
        """A list of :class:`KillableWorker` objects."""
        all_workers = list(self.workers)
        native_pids = set(w.pid for w in self.workers)
        for process in find_apache_workers():
            if process.pid not in native_pids:
                all_workers.append(NonNativeWorker(process))
        return all_workers

    @property
    def manager_metrics(self):
        """
        Information about the interaction between the Apache manager and the Apache web server.

        Here's an example of the resulting dictionary:

        >>> from apache_manager import ApacheManager
        >>> from pprint import pprint
        >>> manager = ApacheManager()
        >>> pprint(manager.manager_metrics)
        {'workers_killed_active': 0, 'workers_killed_idle': 0, 'status_response': None}

        Notes about these metrics:

        - The ``status_response`` key is :data:`None` by default. Once an
          Apache status page has been fetched it becomes :data:`True` if the
          status page was fetched successfully or :data:`False` if fetching of
          the status page failed (see :func:`fetch_status_page()`,
          :attr:`html_status` and :attr:`text_status`).
        - The ``workers_killed_active`` and ``workers_killed_idle`` keys give
          the number of Apache workers killed by :func:`kill_workers()`.
        """
        return dict(workers_killed_active=self.num_killed_active,
                    workers_killed_idle=self.num_killed_idle,
                    status_response=self.status_response)

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
            total_accesses=int(self.extract_metric(r'Total Accesses: (\d+)')),
            # Example: "Total kBytes: 169318"
            total_traffic=int(self.extract_metric(r'Total KBytes: (\d+)')) * 1024,
            # Example: "CPULoad: 7.03642"
            cpu_load=float(self.extract_metric(r'CPULoad: ([0-9.]+)')),
            # Example: "Uptime: 85017"
            uptime=int(self.extract_metric(r'Uptime: (\d+)')),
            # Example: "ReqPerSec: .576802"
            requests_per_second=float(self.extract_metric(r'ReqPerSec: ([0-9.]+)')),
            # Example: "BytesPerSec: 2039.38"
            bytes_per_second=float(self.extract_metric(r'BytesPerSec: ([0-9.]+)')),
            # Example: "BytesPerReq: 3535.66"
            bytes_per_request=float(self.extract_metric(r'BytesPerReq: ([0-9.]+)')),
            # Example: "BusyWorkers: 2"
            busy_workers=int(self.extract_metric(r'BusyWorkers: (\d+)')),
            # Example: "IdleWorkers: 6"
            idle_workers=int(self.extract_metric(r'IdleWorkers: (\d+)')),
        )

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
        modified_pattern = re.sub(r'\s+', r'\s+', pattern)
        match = re.search(modified_pattern, self.text_status, re.IGNORECASE)
        if match:
            logger.debug("Pattern '%s' matched '%s'.", pattern, match.group(0))
            return match.group(1)
        else:
            logger.warning("Pattern %r didn't match plain text Apache status page contents!", pattern)
            return default

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
        >>> pprint(mgr.wsgi_process_groups)
        {'group-one': [44048384, 44724224, 44048384],
         'group-two': [52088832, 51879936, 55554048, 54956032, 54968320],
         'other-group': [13697024, 13697024, 13697024, 13697024]}
        """
        return self.combined_memory_usage[1]

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

    def kill_workers(self, max_memory_active=0, max_memory_idle=0, timeout=0, dry_run=False):
        """
        Kill Apache worker processes that exceed resource usage thresholds.

        :param max_memory_active: The maximum number of bytes of memory that
                                  active Apache worker processes are allowed to
                                  use (an integer).
        :param max_memory_idle: The maximum number of bytes of memory that
                                idle Apache worker processes are allowed to use
                                (an integer).
        :param timeout: The maximum number of seconds since the beginning of
                        the most recent request (a number).
        :returns: A list of integers with process ids of killed workers.

        Some implementation notes about this method:

        - If any of the parameters are zero the respective resource usage
          threshold will not be applied.
        - Memory usage is measured using :attr:`~KillableWorker.memory_usage`.
        - The number of seconds since the beginning of the most recent request
          is measured using :attr:`WorkerStatus.ss`.
        - Worker processes are killed using the
          :func:`~proc.core.Process.kill()` method of the
          :class:`proc.core.Process` class.

        See also :attr:`num_killed_active` and :attr:`num_killed_idle`.
        """
        killed = set()
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
                    logger.info("Killing %s using %s (last request: %s) ..",
                                worker, format_size(worker.memory_usage),
                                worker.request or 'unknown')
                    kill_worker = True
                elif timeout and worker.is_active and getattr(worker, 'ss', 0) > timeout:
                    logger.info("Killing %s hanging for %s since last request (last request: %s) ..",
                                worker, format_timespan(worker.ss),
                                worker.request or 'unknown')
                    kill_worker = True
                if kill_worker:
                    if not dry_run:
                        worker.process.kill()
                    killed.add(worker.pid)
                    if worker.is_active:
                        self.num_killed_active += 1
                    else:
                        self.num_killed_idle += 1
        if killed:
            logger.info("Killed %s.", pluralize(len(killed), "Apache worker"))
        else:
            logger.info("No Apache workers killed (all workers within resource usage limits).")
        return list(killed)

    def save_metrics(self, data_file):
        """
        Store monitoring metrics in a data file.

        :param data_file: The pathname of the data file (a string).

        This method stores the metrics provided by :attr:`manager_metrics` and
        :attr:`server_metrics` in a text file in an easy to parse format.
        Here's an example of what the contents of the file look like::

            busy-workers           1
            bytes-per-request      2816.0
            bytes-per-second       1.53378
            cpu-load               0.00020425
            idle-workers           5
            requests-per-second    0.000544665
            status-response        0
            total-accesses         96
            total-traffic          264
            uptime                 176255
            workers-killed-active  0
            workers-killed-idle    0

        The values in the example above have been aligned to ease readability;
        in reality the names and values are delimited by a tab character (as
        long as you parse the file as whitespace delimited name/value pairs it
        will be fine, this is trivial to do with e.g. AWK_).

        .. _AWK: https://en.wikipedia.org/wiki/AWK
        """
        if data_file == '-':
            logger.debug("Reporting metrics on standard output ..")
        else:
            logger.debug("Storing metrics in %s ..", data_file)
        combined_metrics = dict(self.server_metrics)
        combined_metrics.update(self.manager_metrics)
        output = []
        for name, value in sorted(combined_metrics.items()):
            if isinstance(value, bool):
                value = 0 if value else 1
            output.append('%s\t%s' % (name.replace('_', '-'), value))
        if data_file == '-':
            print('\n'.join(output))
        else:
            temporary_file = '%s.tmp' % data_file
            with open(temporary_file, 'w') as handle:
                handle.write('\n'.join(output) + '\n')
            os.rename(temporary_file, data_file)

    def refresh(self):
        """Clear cached properties so that their values are recomputed when dereferenced."""
        self.clear_cached_properties()


class KillableWorker(object):

    """
    Abstract base class to represent killable Apache worker processes.

    Worker processes can be killed based on resource usage thresholds like
    memory usage and/or requests that are taking too long to process.
    """

    @property
    def is_alive(self):
        """:data:`True` if :attr:`pid` refers to an existing process, :data:`False` otherwise."""
        return self.process.is_alive if self.process else False

    @lazy_property(writable=True)
    def process(self):
        """
        The :class:`proc.core.Process` object for this :attr:`pid`.

        If the worker process disappears before the process information is
        requested then this value will be :data:`None`.
        """
        if self.pid:
            return Process.from_pid(self.pid)

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


class WorkerStatus(KillableWorker, dict):

    """
    :class:`WorkerStatus` objects represent the state of an Apache worker.

    These objects are constructed by :attr:`ApacheManager.workers`. To give you
    an idea of what :class:`WorkerStatus` objects look like, here's a simple
    example:

    >>> from apache_manager import ApacheManager
    >>> from pprint import pprint
    >>> manager = ApacheManager()
    >>> pprint(manager.workers[0])
    {'acc': (0, 8, 8),
     'child': 0.02,
     'client': '127.0.0.1',
     'conn': 0.0,
     'cpu': 0.03,
     'm': '_',
     'pid': 17313,
     'req': 0,
     'request': 'GET /server-status HTTP/1.0',
     'slot': 0.02,
     'srv': (0, 0),
     'ss': 22,
     'vhost': '127.0.1.1'}

    The naming of the fields may look somewhat obscure, this is because they
    match the names given on the Apache status page. If any of the fields are
    not available their value will be :data:`None`. The following properties
    are parsed from the Apache status page:

    .. attribute:: srv

       Child Server number and generation (a tuple of two integers).

    .. attribute:: pid

       The process ID of the Apache worker (an integer).

    .. attribute:: acc

       The number of accesses this connection / this child / this slot (a tuple
       of three integers).

    .. attribute:: m

       Mode of operation (a string). Some known modes:

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

    .. attribute:: cpu

       The CPU usage (number of seconds as a floating point number).

    .. attribute:: ss

       The number of seconds since the beginning of the most recent request (a
       float).

    .. attribute:: req

       The number of milliseconds required to process the most recent request
       (an integer).

    .. attribute:: conn

       The number of kilobytes transferred this connection (a float).

    .. attribute:: child

        The number of megabytes transferred this child (a float).

    .. attribute:: slot

        The total number of megabytes transferred this slot (a float).

    .. attribute:: request

       The HTTP status line of the most recent request (a string).

    The following computed properties are based on the properties parsed from
    the Apache status page:
    """

    def __init__(self, *args, **kw):
        """
        Initialize a :class:`WorkerStatus` object.

        The constructor of these objects is used as a dictionary constructor,
        i.e. passing it an iterable of key/value pairs with collected metrics.
        """
        # Delegate initialization to the superclass.
        super(WorkerStatus, self).__init__(*args, **kw)
        # Coerce fields to their proper Python type.
        self['acc'] = tuple(coerce_value(int, n) for n in self['acc'].split('/'))
        self['child'] = coerce_value(float, self['child'])
        self['conn'] = coerce_value(float, self['conn'])
        self['cpu'] = coerce_value(float, self['cpu'])
        self['pid'] = coerce_value(int, self['pid'])
        self['req'] = coerce_value(int, self['req'])
        self['slot'] = coerce_value(float, self['slot'])
        self['srv'] = tuple(coerce_value(int, n) for n in self['srv'].split('-'))
        self['ss'] = coerce_value(int, self['ss'])
        # The default value of the `request' field is NULL. We hide this
        # obscure (C) implementation detail from Python.
        if self.get('request', 'NULL') == 'NULL':
            self['request'] = None

    @property
    def is_idle(self):
        """
        :data:`True` if the worker is idle, :data:`False` otherwise.

        The value of this property is based on :attr:`m` and
        :data:`IDLE_MODES`.
        """
        return self['m'] in IDLE_MODES

    @property
    def is_active(self):
        """
        :data:`True` if the worker isn't idle, :data:`False` otherwise.

        The value of this property is based on :attr:`is_idle`.
        """
        return not self.is_idle

    def __getattr__(self, name):
        """Provide access to dictionary fields with attribute syntax."""
        try:
            return self[name]
        except KeyError:
            raise AttributeError("'%s' object has no attribute '%s'" % (self.__class__.__name__, name))

    def __str__(self):
        """Render a human friendly representation of a native Apache worker."""
        return "native worker %i (%s)" % (self.pid, "active" if self.is_active else "idle")


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

    def __init__(self, process):
        """
        Initialize a :class:`NonNativeWorker` object.

        :param process: A :class:`proc.tree.ProcessNode` object.
        """
        self.process = process
        self.pid = process.pid
        self.is_active = True
        self.request = None

    def __str__(self):
        """Render a human friendly representation of a non-native Apache worker."""
        return "non-native worker %i" % self.pid


def parse_status_table(table, dict_type=dict):
    """Parse one of the status tables from Apache's HTML status page."""
    headings = dict((i, normalize_text(coerce_tag(th))) for i, th in enumerate(table.findAll('th')))
    logger.debug("Parsed table headings: %r", headings)
    for tr in table.findAll('tr'):
        values_by_index = [coerce_tag(td) for td in tr.findAll('td')]
        logger.debug("Parsed values by index: %r", values_by_index)
        if values_by_index:
            # Ignore exceptions during coercion.
            # TODO This can obscure real problems. Find a better way to make it robust!
            try:
                values_by_name = dict_type((headings[i], v) for i, v in enumerate(values_by_index))
                logger.debug("Parsed values by name: %r", values_by_name)
                yield values_by_name
            except Exception:
                pass


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


def normalize_text(value):
    """Lossy normalization of text values to make string comparisons less fragile."""
    try:
        return re.sub('[^a-z0-9]', '', value.lower())
    except Exception:
        return ''
