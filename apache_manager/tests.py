# Monitor and control Apache web server workers from Python.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: February 27, 2020
# URL: https://apache-manager.readthedocs.io

"""Test suite for the `apache-manager` project."""

# Standard library modules.
import itertools
import logging
import multiprocessing
import os
import re
import sys
import tempfile
import time
import unittest

# External dependencies.
import coloredlogs
from capturer import CaptureOutput
from executor import execute
from humanfriendly import compact, dedent
from natsort import NaturalOrderKey
from property_manager import set_property
from six import text_type
from six.moves.urllib.request import Request, urlopen

# Modules included in our package.
from apache_manager import ApacheManager, coerce_value
from apache_manager.cli import main
from apache_manager.exceptions import AddressDiscoveryError, StatusPageError

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def setUpModule():
    """
    Prepare the test suite.

    Sets up logging to the terminal. When a test fails the logging output can
    help to perform a post-mortem analysis of the failure in question (even
    when its hard to reproduce locally). This is especially useful when
    debugging remote test failures, whether they happened on Travis CI or a
    user's local system.

    Also makes sure that the Apache web server is installed and running because
    this is required to run the test suite.
    """
    # Set up logging to the terminal.
    coloredlogs.install(level=logging.DEBUG)
    # Make sure Apache is installed and configured.
    try:
        manager = ApacheManager()
        manager.fetch_status_page(manager.text_status_url)
    except Exception as e:
        logger.exception("Failed to connect to local Apache server!")
        raise Exception(compact("""
            Please make sure the Apache web server is installed and configured
            (running) before you run this test suite because this test suite
            tests the actual integration with Apache (it doesn't use mocking)
            and so requires Apache to be installed, configured and running.

            Swallowed exception: {message} ({type})
        """, message=e, type=type(e)))


class ApacheManagerTestCase(unittest.TestCase):

    """:mod:`unittest` compatible container for the `apache-manager` test suite."""

    def setUp(self):
        """Reset the logging level before every test runs."""
        coloredlogs.set_level(logging.DEBUG)

    def test_port_discovery(self):
        """Test Apache port discovery and error handling."""
        # Test that port discovery raises an exception when ports.conf doesn't exist.
        filename = 'this-ports-config-does-not-exist-%i.conf' % os.getpid()
        pathname = os.path.join(tempfile.gettempdir(), filename)
        manager = ApacheManager(ports_config=pathname)
        self.assertRaises(AddressDiscoveryError, lambda: manager.listen_addresses)
        # Test that port discovery raises an exception when parsing of ports.conf fails.
        with tempfile.NamedTemporaryFile() as temporary_file:
            manager = ApacheManager(ports_config=temporary_file.name)
            self.assertRaises(AddressDiscoveryError, lambda: manager.listen_addresses)
        # Test parsing of `Listen' directives with a port number but no IP address.
        with tempfile.NamedTemporaryFile() as temporary_file:
            with open(temporary_file.name, 'w') as handle:
                handle.write('Listen 12345\n')
            manager = ApacheManager(ports_config=temporary_file.name)
            assert any(a.port == 12345 for a in manager.listen_addresses)
        # Test parsing of `Listen' directives with an IP address and port number.
        with tempfile.NamedTemporaryFile() as temporary_file:
            with open(temporary_file.name, 'w') as handle:
                handle.write('Listen 127.0.0.2:54321\n')
            manager = ApacheManager(ports_config=temporary_file.name)
            assert any(a.address == '127.0.0.2' and a.port == 54321
                       for a in manager.listen_addresses)
        # Test that port discovery on the host system returns at least one port.
        assert len(ApacheManager().listen_addresses) >= 1

    def test_text_status_page(self):
        """Test that the plain text status page can be fetched."""
        manager = ApacheManager()
        assert manager.text_status

    def test_html_status_page(self):
        """Test that the HTML status page can be fetched."""
        manager = ApacheManager()
        assert manager.html_status

    def test_extract_metric(self):
        """Test that extract_metric() fails "gracefully" by returning a default value."""
        manager = ApacheManager()
        assert manager.extract_metric('This pattern is expected to never match', '42') == '42'

    def test_status_code_validation(self):
        """Test that unexpected HTTP responses from Apache raise an exception."""
        manager = ApacheManager()
        self.assertRaises(
            StatusPageError,
            manager.fetch_status_page,
            manager.html_status_url + "-non-existing-endpoint",
        )

    def test_status_page_parsing_errors(self):
        """Test what happens when the status page can't be parsed."""
        manager = ApacheManager()
        set_property(
            manager,
            'html_status',
            dedent(
                '''
                <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
                <title>404 Not Found</title>
                <h1>Not Found</h1>
                <p>
                    The requested URL was not found on the server.
                    If you entered the URL manually please check your spelling and try again.
                </p>
                '''
            ),
        )
        self.assertRaises(StatusPageError, getattr, manager, 'slots')

    def test_worker_table_parsing(self):
        """Test that parsing of worker information from the HTML status page works."""
        manager = ApacheManager()
        # We expect there to be at least one slot and worker.
        assert manager.slots
        assert manager.workers
        # There will never be more workers than there are slots, although there
        # may be more slots than workers. However we can't reasonable test this
        # because it depends on the system's Apache configuration.
        assert len(manager.slots) >= len(manager.workers)
        retry(lambda: any(w.is_active for w in manager.workers))
        retry(lambda: any(w.is_alive for w in manager.workers))
        retry(lambda: any(w.is_idle for w in manager.workers))
        retry(lambda: any(isinstance(w.acc, tuple)for w in manager.workers))
        # Validate the worker status properties. Some properties like 'vhost'
        # may be empty when a large enough number of workers is configured
        # that some workers are never initialized. To cope with this but
        # nevertheless validate that each WorkerStatus property can be parsed
        # successfully, we run the following checks against all known workers
        # and assert that they pass for at least one worker.
        assert any(self.check_worker_status(w) for w in manager.workers)

    def check_worker_status(self, worker):
        """Try to validate WorkerStatus properties."""
        try:
            # Validate the WorkerStatus.acc property.
            assert isinstance(worker.acc, tuple)
            assert len(worker.acc) == 3
            assert all(isinstance(n, int) for n in worker.acc)
            # Validate the WorkerStatus.child property.
            assert isinstance(worker.child, float)
            # Validate the WorkerStatus.client property.
            assert isinstance(worker.client, text_type) and worker.client
            # Validate the WorkerStatus.conn property.
            assert isinstance(worker.conn, float)
            # Validate the WorkerStatus.cpu property.
            assert isinstance(worker.cpu, float)
            # Validate the WorkerStatus.req property.
            assert isinstance(worker.req, int)
            # Validate the WorkerStatus.slot property.
            assert isinstance(worker.slot, float)
            # Validate the WorkerStatus.srv property.
            assert isinstance(worker.srv, tuple)
            assert len(worker.srv) == 2
            assert all(isinstance(n, int) for n in worker.srv)
            # Validate the WorkerStatus.vhost property.
            assert isinstance(worker.vhost, text_type) and worker.vhost
        except AssertionError:
            return False
        else:
            return True

    def test_manager_metrics(self):
        """Test that the Apache manager successfully reports metrics about itself."""
        manager = ApacheManager()
        assert 'status_response' in manager.manager_metrics
        assert manager.manager_metrics['workers_killed_active'] >= 0
        assert manager.manager_metrics['workers_killed_idle'] >= 0

    def test_server_uptime(self):
        """
        Test that server metrics parsing works (based on the uptime).

        This test verifies that the uptime increases after a second. It doubles
        as a regression test against a bug that was exposed the first time
        apache-manager was run on an Ubuntu 18.04 system with a newer Apache
        version that changed the format of the status page, causing buggy
        regular expression matching (caused by lack of anchoring).
        """
        manager = ApacheManager()
        initial_uptime = manager.server_metrics['uptime']
        assert initial_uptime > 0
        time.sleep(2)
        manager.refresh()
        later_uptime = manager.server_metrics['uptime']
        assert later_uptime > initial_uptime

    def test_memory_usage(self):
        """Test that memory usage analysis works."""
        manager = ApacheManager()
        # Make sure there are Apache workers alive that have handled a couple of requests.
        for i in range(10):
            manager.fetch_status_page(manager.text_status_url)
        assert sum(manager.memory_usage) > 0
        # TODO Create a WSGI process group so we can perform a useful test here?
        assert isinstance(manager.wsgi_process_groups, dict)

    def test_refresh(self):
        """Test refreshing of cached properties."""
        manager = ApacheManager()
        initial_accesses = manager.server_metrics['total_accesses']
        time.sleep(1)
        cached_accesses = manager.server_metrics['total_accesses']
        assert cached_accesses == initial_accesses
        manager.refresh()
        time.sleep(1)
        fresh_accesses = manager.server_metrics['total_accesses']
        assert fresh_accesses > initial_accesses

    def test_save_metrics(self):
        """Test that monitoring metrics can be saved to a text file."""
        fd, temporary_file = tempfile.mkstemp()
        try:
            manager = ApacheManager()
            manager.save_metrics(temporary_file)
            with open(temporary_file) as handle:
                keywords = [tokens[0] for tokens in (line.split() for line in handle) if tokens]
            assert all(required_kw in keywords for required_kw in [
                'busy-workers',
                'bytes-per-request',
                'bytes-per-second',
                'cpu-load',
                'idle-workers',
                'requests-per-second',
                'status-response',
                'total-accesses',
                'total-traffic',
                'uptime',
                'workers-killed-active',
                'workers-killed-idle',
            ])
        finally:
            os.unlink(temporary_file)

    def test_kill_active_worker(self):
        """Test killing of active workers based on memory usage thresholds."""
        if os.getuid() != 0:
            self.skipTest("Skipping test that kills active workers (superuser privileges are required)")
        with TemporaryWSGIApp('wsgi-memory-hog') as context:

            # Create a WSGI application that keeps allocating memory but never returns.
            context.install_wsgi_app('''
                import itertools
                import os
                import random
                import string

                def application(environ, start_response):
                    # Store the PID of the Apache worker handling this request.
                    with open({pid_file}, 'w') as handle:
                        handle.write(str(os.getpid()))
                    # Start the response.
                    start_response('200 OK', [])
                    # Keep allocating memory but never return.
                    random_heap_objects = []
                    for i in itertools.count():
                        random_heap_objects.append(random_string())

                def random_string():
                    length = random.randint(1024*512, 1024*1024)
                    characters = string.ascii_letters + string.digits
                    return ''.join(random.choice(characters) for i in range(length))
            ''', pid_file=repr(context.pid_file))

            # Activate the WSGI application by making a request.
            context.make_request()

            # Get the PID of the Apache worker handling the request.
            def get_worker_pid():
                try:
                    with open(context.pid_file) as handle:
                        return int(handle.read())
                except Exception:
                    raise AssertionError(compact("""
                        It looks like the WSGI application (affectionately called
                        "memory hog" :-) never got a chance to run! Please review the
                        messages Apache emitted when its configuration was reloaded to
                        pinpoint the cause of this issue.
                    """))

            # Use the Apache manager to kill the worker handling the request.
            def kill_active_worker():
                manager = ApacheManager()
                worker_pid = get_worker_pid()
                killed_processes = manager.kill_workers(max_memory_active=1024 * 1024 * 50)
                assert worker_pid in killed_processes

            # It might take a while for the worker to hit the memory limit.
            retry(kill_active_worker)

    def test_kill_worker_that_times_out(self):
        """Test killing of active workers based on time usage thresholds."""
        if os.getuid() != 0:
            self.skipTest("Skipping test that kills workers that time out (superuser privileges are required)")
        with TemporaryWSGIApp('wsgi-timeout') as context:

            # Create a WSGI application that doesn't allocate too much memory but never returns.
            context.install_wsgi_app('''
                import itertools
                import os
                import time

                def application(environ, start_response):
                    # Store the PID of the Apache worker handling this request.
                    with open({pid_file}, 'w') as handle:
                        handle.write(str(os.getpid()))
                    # Start the response.
                    start_response('200 OK', [])
                    # Waste time doing nothing ;-).
                    for i in itertools.count():
                        time.sleep(1)
            ''', pid_file=repr(context.pid_file))

            # Activate the WSGI application by making a request.
            context.make_request()

            # Get the PID of the Apache worker handling the request.
            def get_worker_pid():
                try:
                    with open(context.pid_file) as handle:
                        return int(handle.read())
                except Exception:
                    raise AssertionError(compact("""
                        It looks like the WSGI application (called "wsgi-timeout")
                        never got a chance to run! Please review the messages Apache
                        emitted when its configuration was reloaded to pinpoint the
                        cause of this issue.
                    """))

            # Use the Apache manager to kill the worker handling the request.
            def kill_timeout_worker():
                manager = ApacheManager()
                worker_pid = get_worker_pid()
                killed_processes = manager.kill_workers(timeout=30)
                assert worker_pid in killed_processes

            # It will take a while for the worker to hit the time limit.
            retry(kill_timeout_worker)

    def test_kill_idle_workers(self):
        """Test killing of idle workers based on memory usage thresholds."""
        def kill_idle_workers(dry_run):
            time.sleep(1)
            arguments = ['--kill-workers', '--max-memory-idle=1K']
            if dry_run:
                arguments.insert(0, '--dry-run')
            workers_alive_before = set(w.pid for w in ApacheManager().workers if w.is_alive)
            exit_code, output = run_cli(arguments)
            # Check that one or more worker processes were (simulated to be) killed.
            assert exit_code == 0
            assert re.search(r'Killing native worker \d+ \(idle\)', output) is not None
            # Check that one or more worker processes actually died?
            if not dry_run:
                workers_alive_after = set(w.pid for w in ApacheManager().workers if w.is_alive)
                assert workers_alive_before != workers_alive_after
        for sleep_time in range(1, 60):
            time.sleep(sleep_time)
            try:
                kill_idle_workers(dry_run=True)
                if os.getuid() == 0:
                    kill_idle_workers(dry_run=False)
                break
            except AssertionError:
                logger.exception("Swallowing failed assertion and retrying ..")

    def test_user_friendly_cli(self):
        """Test that CLI reports metrics that are formatted in a human friendly way by default."""
        exit_code, output = run_cli(['--verbose', '--quiet'])
        assert exit_code == 0
        for token in ('Server metrics:', 'Memory usage', 'Minimum:', 'Average:', 'Maximum:'):
            assert token in output

    def test_system_friendly_cli(self):
        """Test that CLI can report machine readable metrics on standard output."""
        exit_code, output = run_cli(['--collect-metrics', '--data-file=-'])
        assert exit_code == 0
        expected_tokens = ['uptime', 'workers-killed-active', 'workers-killed-idle']
        assert all(t in output.split() for t in expected_tokens)

    def test_coerce_value(self):
        """Test value coercion."""
        # Test the happy path.
        assert coerce_value(int, '42') == 42
        assert coerce_value(float, '3.14') == 3.14
        # Test the error handling.
        assert coerce_value(float, '1/5') is None

    def test_max_memory_configuration(self):
        """Test marshaling of ``max-memory-*`` configuration options."""
        manager = ApacheManager()
        manager.config['max-memory-active'] = '500M'
        manager.config['max-memory-idle'] = '250M'
        assert manager.max_memory_active == 524288000
        assert manager.max_memory_idle == 262144000


def retry(func, max_time=60):
    """Simple test helper to retry a function until assertions no longer fail."""
    timeout = time.time() + max_time
    for i in itertools.count():
        try:
            if func() is not False:
                break
        except AssertionError:
            if time.time() > timeout:
                raise
            logger.debug("Retrying function (attempts so far: %i) ..", i)
        time.sleep(1)


def run_cli(arguments):
    """Simple test helper to run the command line interface."""
    # Temporarily replace sys.argv.
    saved_arguments = sys.argv
    sys.argv = ['apache-manager'] + arguments
    try:
        # Capture the output of main().
        with CaptureOutput() as capturer:
            try:
                main()
                exit_code = 0
            except SystemExit as e:
                exit_code = e.code
            output = capturer.get_text()
        return exit_code, output
    finally:
        # Restore sys.argv before we return.
        sys.argv = saved_arguments


class TemporaryWSGIApp(object):

    """Context manager to create temporary Apache WSGI configurations."""

    def __init__(self, name):
        """
        Initialize a :class:`TemporaryWSGIApp` object.

        :param name: The name of the WSGI application (a string).
        """
        self.manager = ApacheManager()
        self.short_name = name
        self.name = 'apache-manager-%s' % name

    def install_wsgi_app(self, python_code, *args, **kw):
        """
        Install a temporary WSGI script file.

        :param python_code: A Python code template (a string).
        :param args: Positional arguments to be interpolated into the Python
                     code.
        :param kw: Keyword arguments to be interpolated into the Python code.
        """
        logger.info("Creating WSGI script: %s", self.wsgi_script_file)
        with open(self.wsgi_script_file, 'w') as handle:
            handle.write('%s\n' % dedent(python_code, *args, **kw))
        logger.info("Creating Apache virtual host: %s", self.virtual_host_file)
        with open(self.virtual_host_file, 'w') as handle:
            netloc = self.manager.listen_addresses[0]
            installed_version = execute('dpkg-query', '--show', '--showformat=${Version}', 'apache2', capture=True)
            if NaturalOrderKey(installed_version) >= NaturalOrderKey('2.4'):
                handle.write(dedent(
                    '''
                    <VirtualHost {netloc.address}:{netloc.port}>
                        ServerName {server_name}
                        WSGIScriptAlias / {wsgi_script}
                        <Directory "{root}">
                            Require all granted
                        </Directory>
                    </VirtualHost>
                    ''',
                    netloc=netloc,
                    server_name=self.name,
                    root=os.path.dirname(self.wsgi_script_file),
                    wsgi_script=self.wsgi_script_file,
                ))
            else:
                handle.write(dedent('''
                    <VirtualHost {netloc.address}:{netloc.port}>
                        ServerName {server_name}
                        WSGIScriptAlias / {wsgi_script}
                    </VirtualHost>
                ''', netloc=netloc, server_name=self.name, wsgi_script=self.wsgi_script_file))
        logger.info("Activating Apache virtual host ..")
        assert os.system('sudo service apache2 reload') == 0

    def make_request(self):
        """Make a request to the WSGI application that's aborted after 10 seconds."""
        # Create a subprocess to make the HTTP request.
        started_event = multiprocessing.Event()
        child = multiprocessing.Process(target=self.make_request_helper, args=(started_event,))
        # Start the subprocess.
        child.start()
        # Wait for the subprocess to initialize (forking a process and
        # initializing a Python interpreter can take some time).
        started_event.wait()
        # Give the subprocess a moment to make the request.
        child.join(10)
        # Terminate the child process; the HTTP request will never return
        # successfully so there's no point in waiting for it :-).
        child.terminate()

    def make_request_helper(self, started_event):
        """Helper method to make a request to the WSGI application in a subprocess."""
        # Let the operator know that we're about to make the HTTP request.
        logger.info("Making HTTP request to %s virtual host ..", self.name)
        # Let the parent process know we're ready to make the HTTP request.
        started_event.set()
        # Finally here's everything we wanted to do: It's a one liner :-P.
        urlopen(Request(self.manager.listen_addresses[0].url, None, dict(Host=self.name))).read()

    @property
    def virtual_host_file(self):
        """The absolute pathname of the Apache virtual host configuration."""
        return '/etc/apache2/sites-enabled/%s.conf' % self.name

    @property
    def wsgi_script_file(self):
        """The absolute pathname of the WSGI script file."""
        return '/var/lib/apache-manager/%s.wsgi' % self.short_name

    @property
    def pid_file(self):
        """The absolute pathname of the PID file."""
        return '/var/lib/apache-manager/%s.pid' % self.short_name

    def __enter__(self):
        """Enter the context (does nothing)."""
        return self

    def __exit__(self, exc_type=None, exc_value=None, traceback=None):
        """Leave the context, cleaning up the configuration files and unloading them from Apache."""
        for filename in self.virtual_host_file, self.wsgi_script_file, self.pid_file:
            if os.path.exists(filename):
                logger.info("Cleaning up temporary file: %s", filename)
                os.unlink(filename)
        logger.info("Deactivating Apache virtual host ..")
        assert os.system('sudo service apache2 reload') == 0
